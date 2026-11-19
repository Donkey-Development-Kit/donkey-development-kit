"""``donkey scan``: a static, deterministic model of a repository's agents, tools
and MCP servers (BG §2.5, #217).

:func:`scan` walks a directory, parses every ``.py`` file with :mod:`ast` and
finds the ``@donkey.agent`` and ``@donkey.tool`` markers (``core/toolspec.py``).
It **never imports or executes user code**: a marker counts only when its
receiver is statically a ``Donkey`` (the class imported from ``donkey_kit``, an
instance built from it, or such an instance imported from another scanned
module), resolved by Python's scoping rules, and decorator arguments are read
only when they are literals. A receiver the scan cannot be sure of (a name
also bound to something else, or a parameter hiding a module-level
``donkey``) is reported as ``ambiguous-receiver``, never guessed.

The :class:`ScanResult` is the single input to both publish targets, direct
Exchange publish (#218) and the self-managed scanner bundles (#1104/#1105), which
each own their on-disk artifact. Scan writes no file and defines no spec format:
``donkey.yaml`` went with the refused provisioning control plane (#730).

Identity follows #503. An MCP server is the set of tools that name it with
``server=``; a tool without one is *unattributed*, reported and never guessed.
Agent names and server IDs are service IDs, checked by
:func:`~donkey_kit.core.toolspec.validate_service_id`.

How an MCP server is *reached* (its transport and endpoint, #1193) is declared,
not read from code: nothing ties a ``server=`` ID to the object that serves it,
and the endpoint a client reaches is a deployment fact. Scan reads it from a
``[donkey.mcp_servers.<id>]`` table in the scanned root's ``.donkey-kit.toml``,
with the ``.donkey-kit.local.toml`` beside it overriding key by key, so each
environment can name its own endpoint. A server with no declaration reports
``None`` for both, never a guess.

An agent's protocol and endpoint are declared the same way, in a
``[donkey.agents.<name>]`` table (#1260): ``protocol`` is one of
:data:`AGENT_PROTOCOLS` and ``endpoint`` the URL the agent is served at.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from ..core.approvals import parse_timeout
from ..core.config import LOCAL_TOML_NAME, TOML_NAME
from ..core.endpoints import url_credential_problem
from ..core.toolspec import validate_service_id
from .publication import check_description_quality, content_digest

__all__ = [
    "AGENT_PROTOCOLS",
    "MCP_TRANSPORTS",
    "SCAN_FORMAT",
    "ScanIssue",
    "ScanResult",
    "ScannedAgent",
    "ScannedMcpServer",
    "ScannedParameter",
    "ScannedTool",
    "scan",
]

# Bumped when the JSON shape changes incompatibly, so a CI diff of two scans
# taken with different SDK versions says why it differs.
SCAN_FORMAT = 1

# Directories never walked: tool caches, virtualenvs and vendored packages hold
# code that is not the repository's own. Any directory starting with "." is
# skipped too (.git, .venv, .tox, ...).
_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "site-packages", "venv"})

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

_FuncDef = ast.FunctionDef | ast.AsyncFunctionDef

# The MCP specification's transports. SSE is deprecated by the spec in favour of
# streamable HTTP, but servers built on it are still deployed.
MCP_TRANSPORTS = ("stdio", "streamable-http", "sse")

_SERVER_KEYS = frozenset({"transport", "endpoint"})

#: The agent protocols a ``[donkey.agents.<name>]`` table may declare: the
#: ``protocol`` tokens of Agent Fabric's agent metadata schema that the SDK can
#: describe. ``a2a_v1`` is A2A 1.0, what ``donkey.serve()`` speaks; ``other`` is
#: any other protocol, published with no agent card. ``a2a`` (A2A 0.3) is left
#: out: the SDK builds no 0.3 card.
AGENT_PROTOCOLS = ("a2a_v1", "other")

_AGENT_KEYS = frozenset({"protocol", "endpoint"})


@dataclass(frozen=True)
class ScannedParameter:
    """One parameter of a scanned tool; ``annotation`` is its source text."""

    name: str
    annotation: str | None


@dataclass(frozen=True)
class ScannedTool:
    """A ``@donkey.tool`` found in source. ``server`` is ``None`` when unattributed."""

    name: str
    qualname: str
    path: str
    line: int
    description: str | None
    is_async: bool
    server: str | None
    approval: str | None
    risk: str | None
    parameters: tuple[ScannedParameter, ...]


@dataclass(frozen=True)
class ScannedAgent:
    """A ``@donkey.agent`` found in source; ``name`` is its service ID.

    ``protocol`` and ``endpoint`` are what ``[donkey.agents.<name>]`` declares
    (#1260), ``None`` when it declares nothing.
    """

    name: str
    qualname: str
    path: str
    line: int
    description: str | None
    is_async: bool
    skills: tuple[str, ...]
    protocol: str | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class ScannedMcpServer:
    """An MCP server, as the tools that name it with ``server=`` (#503).

    ``transport`` and ``endpoint`` are what ``[donkey.mcp_servers.<id>]``
    declares (#1193), ``None`` when it declares nothing. ``endpoint`` is always
    ``None`` for ``stdio``, which has no URL.
    """

    id: str
    tools: tuple[str, ...]
    transport: str | None = None
    endpoint: str | None = None


@dataclass(frozen=True)
class ScanIssue:
    """Something scan reports instead of emitting silently.

    ``kind`` is one of ``missing``, ``tautological`` or ``too-short`` (the
    description checks, BG §2.5), ``invalid-service-id``, ``invalid-skills``,
    ``invalid-argument`` (a positional or unknown argument, or a literal the
    decorator rejects: an ``approval``, ``risk`` or ``approval_timeout`` it
    would raise on), ``non-literal-argument`` (including ``**kwargs``),
    ``ambiguous-receiver`` (a ``@x.tool`` whose ``x`` is bound both to a
    ``Donkey`` and to something else, or hides an outer one),
    ``duplicate-service-id``, ``duplicate-tool``, ``syntax-error`` or
    ``unreadable``. A marker with an argument issue is not recorded: the
    decorator would raise when its module is imported. A ``[donkey.mcp_servers]`` declaration
    adds ``invalid-mcp-server`` (a malformed entry or an unknown key),
    ``invalid-transport``, ``invalid-endpoint`` and ``unknown-server`` (an ID no
    tool names). A ``[donkey.agents]`` declaration adds ``invalid-agent`` (a
    malformed entry, an unknown key, or ``a2a_v1`` with no endpoint),
    ``invalid-protocol``, ``invalid-endpoint`` and ``unknown-agent`` (a name no
    agent has). Their ``path`` is the TOML file and their ``line`` is ``0``.
    """

    kind: str
    subject: str
    path: str
    line: int
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """The issue as plain JSON-ready data, as every ``--json`` document lists it."""
        return {
            "kind": self.kind,
            "subject": self.subject,
            "path": self.path,
            "line": self.line,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ScanResult:
    """The deterministic model ``donkey scan`` produces (#217).

    Every collection is sorted, and paths are POSIX and relative to the scanned
    root, so the same repository always gives the same :meth:`to_json` bytes.
    """

    agents: tuple[ScannedAgent, ...] = ()
    tools: tuple[ScannedTool, ...] = ()
    mcp_servers: tuple[ScannedMcpServer, ...] = ()
    issues: tuple[ScanIssue, ...] = ()

    @property
    def unattributed(self) -> tuple[ScannedTool, ...]:
        """The tools with no ``server=``: reported, never guessed (#503)."""
        return tuple(t for t in self.tools if t.server is None)

    def rejected(self, table: Literal["agent", "mcp server"], name: str, key: str) -> bool:
        """Whether ``[donkey.agents.<name>]`` or ``[donkey.mcp_servers.<name>]``
        declares ``key`` with a value scan rejected (#1802).

        The model carries ``None`` for a rejected value, the same as for an
        undeclared key, so a reader that would say "undeclared" asks this first.
        """
        kind, subject = f"invalid-{key}", f"{table} {name}"
        return any(i.kind == kind and i.subject == subject for i in self.issues)

    def digest(self) -> str:
        """A content digest over what would be published (BG §2.5).

        Source locations and issues are left out, so moving code or fixing a
        description warning elsewhere does not look like a change to publish.
        """
        published = self._dict()
        for kind in ("agents", "tools"):
            for entry in published[kind]:
                del entry["path"], entry["line"]
        del published["issues"], published["unattributed"]
        return content_digest(published, {"scan_format": SCAN_FORMAT})

    def to_dict(self) -> dict[str, Any]:
        """The model as plain JSON-ready data, including :meth:`digest`."""
        data = self._dict()
        data["digest"] = self.digest()
        return data

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, two-space indent, trailing newline."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def _dict(self) -> dict[str, Any]:
        def tool(t: ScannedTool) -> dict[str, Any]:
            params = [{"name": p.name, "annotation": p.annotation} for p in t.parameters]
            return {
                "name": t.name,
                "qualname": t.qualname,
                "path": t.path,
                "line": t.line,
                "description": t.description,
                "is_async": t.is_async,
                "server": t.server,
                "approval": t.approval,
                "risk": t.risk,
                "parameters": params,
            }

        def agent(a: ScannedAgent) -> dict[str, Any]:
            return {
                "name": a.name,
                "qualname": a.qualname,
                "path": a.path,
                "line": a.line,
                "description": a.description,
                "is_async": a.is_async,
                "skills": list(a.skills),
                "protocol": a.protocol,
                "endpoint": a.endpoint,
            }

        return {
            "scan_format": SCAN_FORMAT,
            "agents": [agent(a) for a in self.agents],
            "tools": [tool(t) for t in self.tools],
            "mcp_servers": [
                {
                    "id": s.id,
                    "tools": list(s.tools),
                    "transport": s.transport,
                    "endpoint": s.endpoint,
                }
                for s in self.mcp_servers
            ],
            "unattributed": [t.qualname for t in self.unattributed],
            "issues": [i.to_dict() for i in self.issues],
        }


def scan(root: str | Path, *, local_overlay: bool = True) -> ScanResult:
    """Statically scan ``root`` (a directory or one ``.py`` file) for agents,
    tools and MCP servers (BG §2.5, #217). Never imports the scanned code.

    ``local_overlay=False`` reads the declarations from the project
    ``.donkey-kit.toml`` alone, leaving out the developer's
    ``.donkey-kit.local.toml``: what ``donkey scaffold`` builds on, so its
    output is the same in every checkout (#1747).

    Raises:
        FileNotFoundError: ``root`` does not exist.
    """
    base = Path(root)
    if not base.exists():
        raise FileNotFoundError(f"nothing to scan at {base}")
    files = [base] if base.is_file() else list(_python_files(base))
    origin = base.parent if base.is_file() else base

    issues: list[ScanIssue] = []
    modules: dict[str, _Module] = {}
    for path in files:
        rel = path.relative_to(origin).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(ScanIssue("unreadable", rel, rel, 0, str(exc)))
            continue
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            issues.append(ScanIssue("syntax-error", rel, rel, exc.lineno or 0, exc.msg))
            continue
        modules[_module_name(rel)] = _Module(rel, tree)

    # A Donkey instance can be re-exported through several modules
    # (app/gov.py -> app/__init__.py -> tools.py), so resolve to a fixpoint.
    # Each pass only adds names or turns one ambiguous, so it ends; the bound
    # is a guard, not a limit any repository reaches.
    exported: dict[str, dict[str, str]] = {name: {} for name in modules}
    for _ in range(2 * len(modules) + 2):
        changed = False
        for name, mod in modules.items():
            found = mod.exports(name, exported)
            if found != exported[name]:
                exported[name] = found
                changed = True
        if not changed:
            break

    agents: list[ScannedAgent] = []
    tools: list[ScannedTool] = []
    for name, mod in modules.items():
        collector = _Collector(mod, _Resolver(mod.package(name), exported))
        collector.visit(mod.tree)
        agents.extend(collector.agents)
        tools.extend(collector.tools)
        issues.extend(collector.issues)

    agents.sort(key=lambda a: (a.path, a.line))
    tools.sort(key=lambda t: (t.path, t.line))
    declared = _Declarations(origin, local_overlay=local_overlay)
    servers = declared.apply(_servers(tools))
    agents = declared.apply_agents(agents)
    issues.extend(declared.issues)
    issues.extend(_description_issues(agents, tools))
    issues.extend(_duplicate_issues(agents, tools, servers))
    issues.sort(key=lambda i: (i.path, i.line, i.kind, i.subject))
    return ScanResult(tuple(agents), tuple(tools), servers, tuple(issues))


def _python_files(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in _SKIPPED_DIRS
        )
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath, name)


def _module_name(rel: str) -> str:
    parts = rel.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    if parts and parts[0] == "src":  # the src layout: src/pkg is imported as pkg
        parts.pop(0)
    return ".".join(parts)


# The keywords each marker accepts: what ``Donkey.tool`` and ``Donkey.agent``
# take (donkey.py). Any other keyword is a TypeError when the module imports.
_TOOL_KEYWORDS = frozenset({"approval", "risk", "approval_timeout", "server"})
_AGENT_KEYWORDS = frozenset({"name", "skills"})
# The keywords whose value the scan records, so one it cannot read is an issue.
# approval_timeout is checked when it is a literal and otherwise not needed.
_RECORDED = frozenset({"approval", "risk", "server", "name", "skills"})

# What a name may be bound to, as far as a static scan can tell: the Donkey
# class, the donkey_kit module, a Donkey instance, or one of those in some
# bindings and something else in others. ``None`` is anything else.
_CLASS, _MODULE, _INSTANCE, _AMBIGUOUS = "class", "module", "instance", "ambiguous"
_DONKEYISH = frozenset({_CLASS, _INSTANCE, _AMBIGUOUS})


@dataclass
class _Binding:
    """One statement that binds a name: an import (``node`` and ``alias``), an
    assignment of ``value``, or anything else (all three ``None``)."""

    node: ast.Import | ast.ImportFrom | None = None
    alias: ast.alias | None = None
    value: ast.expr | None = None


@dataclass
class _Scope:
    """A module, class or function scope and the names bound directly in it."""

    kind: str  # "module", "class" or "function"
    parent: _Scope | None
    bindings: dict[str, list[_Binding]] = field(default_factory=dict)
    declared_global: set[str] = field(default_factory=set)
    declared_nonlocal: set[str] = field(default_factory=set)


_Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


class _Binder(ast.NodeVisitor):
    """Collects the names one scope binds, without entering nested scopes.

    A nested function or class binds its own name here and is kept in
    :attr:`children` to be scoped on its own. A comprehension's targets belong
    to the comprehension, but a ``:=`` inside one binds here, as Python does.
    """

    def __init__(self, scope: _Scope) -> None:
        self.scope = scope
        self.children: list[_Definition] = []

    def bind(self, name: str, binding: _Binding | None = None) -> None:
        self.scope.bindings.setdefault(name, []).append(binding or _Binding())

    def target(self, node: ast.expr, value: ast.expr | None) -> None:
        if isinstance(node, ast.Name):
            self.bind(node.id, _Binding(value=value))
        elif isinstance(node, ast.Tuple | ast.List):
            for element in node.elts:
                self.target(element, None)
        elif isinstance(node, ast.Starred):
            self.target(node.value, None)

    def _definition(self, node: _Definition) -> None:
        # Decorators are evaluated here, before the name is bound.
        for expr in node.decorator_list:
            self.visit(expr)
        self.bind(node.name)
        self.children.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass  # its own scope, and it cannot hold a decorated definition

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self.target(target, node.value)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:  # a bare annotation binds nothing
            self.target(node.target, node.value)
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.target(node.target, None)
        self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.target(node.target, None)
        self.visit(node.value)

    def visit_For(self, node: ast.For) -> None:
        self.target(node.target, None)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.target(node.target, None)
        self.generic_visit(node)

    def visit_withitem(self, node: ast.withitem) -> None:
        if node.optional_vars is not None:
            self.target(node.optional_vars, None)
        self.visit(node.context_expr)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bind(node.name)
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self.target(target, None)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            # ``import a.b`` binds ``a``.
            self.bind(alias.asname or alias.name.split(".")[0], _Binding(node, alias))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.bind(alias.asname or alias.name, _Binding(node, alias))

    def visit_Global(self, node: ast.Global) -> None:
        self.scope.declared_global.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.scope.declared_nonlocal.update(node.names)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.bind(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.bind(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.bind(node.rest)
        self.generic_visit(node)


def _scopes(tree: ast.Module) -> tuple[_Scope, dict[ast.AST, _Scope]]:
    """The module's scope, and the scope of every function and class in it."""
    by_node: dict[ast.AST, _Scope] = {}

    def build(node: ast.Module | _Definition, kind: str, parent: _Scope | None) -> _Scope:
        scope = _Scope(kind, parent)
        binder = _Binder(scope)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = node.args
            for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
                if arg is not None:
                    binder.bind(arg.arg)
        for statement in node.body:
            binder.visit(statement)
        by_node[node] = scope
        for child in binder.children:
            build(child, "class" if isinstance(child, ast.ClassDef) else "function", scope)
        return scope

    module = build(tree, "module", None)
    # A name declared global or nonlocal is bound in the scope it names.
    for scope in by_node.values():
        for name in sorted(scope.declared_global | scope.declared_nonlocal):
            moved = scope.bindings.pop(name, [])
            home = module if name in scope.declared_global else _enclosing(scope, name)
            if home is not None and moved:
                home.bindings.setdefault(name, []).extend(moved)
    return module, by_node


def _enclosing(scope: _Scope, name: str) -> _Scope | None:
    """The function scope a ``nonlocal name`` in ``scope`` refers to."""
    outer = scope.parent
    while outer is not None and (outer.kind != "function" or name not in outer.bindings):
        outer = outer.parent
    return outer


@dataclass
class _Module:
    path: str
    tree: ast.Module

    def __post_init__(self) -> None:
        self.scope, self.scopes = _scopes(self.tree)

    def package(self, module: str) -> list[str]:
        """The package a relative import in this module is resolved against."""
        parts = module.split(".") if module else []
        return parts if self.path.endswith("__init__.py") else parts[:-1]

    def exports(self, module: str, exported: dict[str, dict[str, str]]) -> dict[str, str]:
        """Module-level names bound to a ``Donkey`` instance, or ambiguously to one."""
        resolver = _Resolver(self.package(module), exported)
        found = {name: resolver.kind(self.scope, name) for name in sorted(self.scope.bindings)}
        return {name: kind for name, kind in found.items() if kind in (_INSTANCE, _AMBIGUOUS)}


class _Resolver:
    """What a name means in a scope, by Python's lexical rules, statically.

    A name is the ``Donkey`` class, the ``donkey_kit`` module or a ``Donkey``
    instance only when *every* binding of it in the scope that owns it says so.
    A name bound both to one of those and to something else is ambiguous: which
    one a decorator sees depends on control flow a static scan does not run.
    """

    def __init__(self, package: list[str], exported: dict[str, dict[str, str]]) -> None:
        self.package = package
        self.exported = exported
        self._active: set[tuple[int, str]] = set()
        self._memo: dict[tuple[int, str], str | None] = {}

    @staticmethod
    def _visible(scope: _Scope, name: str) -> Iterator[_Scope]:
        """The scopes a lookup of ``name`` in ``scope`` searches, innermost first.

        A class body is visible to its own statements, not to nested scopes,
        and ``global name`` sends the lookup straight to the module.
        """
        current: _Scope | None = scope
        first = True
        while current is not None:
            if name in current.declared_global:
                while current.parent is not None:
                    current = current.parent
                yield current
                return
            if first or current.kind != "class":
                yield current
            first = False
            current = current.parent

    def lookup(self, scope: _Scope, name: str) -> tuple[str | None, str | None]:
        """``(what name is in scope, what an outer binding it shadows is)``.

        The second is the kind of the nearest outer Donkey-like binding, and is
        set only when the visible binding is not Donkey-like: a parameter named
        ``donkey`` hiding the module's ``donkey = Donkey()`` gives
        ``(None, "instance")``.
        """
        owners = (s for s in self._visible(scope, name) if name in s.bindings)
        owner = next(owners, None)
        if owner is None:
            return None, None
        kind = self.kind(owner, name)
        if kind is not None:
            return kind, None
        for outer in owners:
            shadowed = self.kind(outer, name)
            if shadowed in _DONKEYISH:
                return None, shadowed
        return None, None

    def kind(self, scope: _Scope, name: str) -> str | None:
        key = (id(scope), name)
        if key in self._memo:
            return self._memo[key]
        if key in self._active:  # D = D.from_env(): a name defined by itself
            return _AMBIGUOUS
        self._active.add(key)
        try:
            kinds = {self._binding(scope, b) for b in scope.bindings.get(name, [])}
        finally:
            self._active.discard(key)
        result = None if kinds <= {None} else kinds.pop() if len(kinds) == 1 else _AMBIGUOUS
        self._memo[key] = result
        return result

    def _binding(self, scope: _Scope, binding: _Binding) -> str | None:
        node, alias = binding.node, binding.alias
        if isinstance(node, ast.Import) and alias is not None:
            bound = alias.name if alias.asname else alias.name.split(".")[0]
            return _MODULE if bound == "donkey_kit" else None
        if isinstance(node, ast.ImportFrom) and alias is not None:
            source = self._source(node)
            if not node.level and alias.name == "Donkey" and source.split(".")[0] == "donkey_kit":
                return _CLASS
            return self.exported.get(source, {}).get(alias.name)
        if binding.value is not None and self.is_donkey_call(scope, binding.value):
            return _INSTANCE
        return None

    def _source(self, node: ast.ImportFrom) -> str:
        if not node.level:  # from donkey_kit import Donkey
            return node.module or ""
        package = self.package  # from .gov import donkey
        anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
        return ".".join([*anchor, node.module] if node.module else anchor)

    def class_ref(self, scope: _Scope, node: ast.expr) -> str | None:
        """``_CLASS`` when ``node`` is the ``Donkey`` class (``Donkey`` or
        ``donkey_kit.Donkey``), ``_AMBIGUOUS`` when it may be, else ``None``."""
        if isinstance(node, ast.Name):
            kind, _ = self.lookup(scope, node.id)
            return kind if kind in (_CLASS, _AMBIGUOUS) else None
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "Donkey"
            and isinstance(node.value, ast.Name)
        ):
            kind, _ = self.lookup(scope, node.value.id)
            return {_MODULE: _CLASS, _AMBIGUOUS: _AMBIGUOUS}.get(kind or "")
        return None

    def is_donkey_call(self, scope: _Scope, node: ast.expr) -> bool:
        """``Donkey(...)`` or ``Donkey.from_env(...)``, through any import alias."""
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if isinstance(func, ast.Attribute) and self.class_ref(scope, func) is None:
            func = func.value
        return self.class_ref(scope, func) == _CLASS


class _Collector(ast.NodeVisitor):
    def __init__(self, module: _Module, resolver: _Resolver) -> None:
        self.path = module.path
        self.module = module
        self.resolver = resolver
        self.chain: list[_Scope] = [module.scope]
        self.scope: list[str] = []
        self.agents: list[ScannedAgent] = []
        self.tools: list[ScannedTool] = []
        self.issues: list[ScanIssue] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.chain.append(self.module.scopes[node])
        self.generic_visit(node)
        self.chain.pop()
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass  # not scoped by _Binder, and it cannot hold a decorated definition

    def _function(self, node: _FuncDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        for decorator in node.decorator_list:
            found = self._marker(decorator, qualname, node.lineno)
            if found is None:
                continue
            kind, call = found
            kwargs = self._arguments(kind, call, qualname, node.lineno)
            if kwargs is None:
                continue
            if kind == "tool":
                self._tool(node, qualname, kwargs)
            else:
                self._agent(node, qualname, kwargs)
        self.scope.extend([node.name, "<locals>"])
        self.chain.append(self.module.scopes[node])
        self.generic_visit(node)
        self.chain.pop()
        del self.scope[-2:]

    def _marker(
        self, decorator: ast.expr, qualname: str, line: int
    ) -> tuple[str, ast.Call | None] | None:
        """``("tool" | "agent", call-or-None)`` when ``decorator`` is a marker.

        The receiver must statically be a ``Donkey``. One the scan cannot be
        sure of is reported and skipped, never guessed: a name bound both to a
        ``Donkey`` and to something else, or a parameter or local that hides an
        outer ``Donkey`` of the same name.
        """
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call else decorator
        if not isinstance(target, ast.Attribute) or target.attr not in ("tool", "agent"):
            return None
        recv, scope = target.value, self.chain[-1]
        kind = self.resolver.class_ref(scope, recv)
        shadowed: str | None = None
        if kind is None and isinstance(recv, ast.Name):
            kind, shadowed = self.resolver.lookup(scope, recv.id)
        if kind in (_CLASS, _INSTANCE):
            return target.attr, call
        if kind == _AMBIGUOUS or shadowed is not None:
            name = ast.unparse(recv)
            why = (
                "is bound both to a Donkey and to something else"
                if kind == _AMBIGUOUS
                else "is bound here to something else (a parameter, a local or an import) "
                "that hides an outer Donkey of the same name"
            )
            self._issue(
                "ambiguous-receiver",
                qualname,
                line,
                f"{name!r} {why}, so a static scan cannot tell whether "
                f"@{name}.{target.attr} is a marker; decorate with a Donkey instance "
                "bound once, at module level",
            )
        return None

    def _issue(self, kind: str, subject: str, line: int, detail: str) -> None:
        self.issues.append(ScanIssue(kind, subject, self.path, line, detail))

    def _arguments(
        self, kind: str, call: ast.Call | None, qualname: str, line: int
    ) -> dict[str, object] | None:
        """The marker's literal keyword arguments, or ``None`` when one is
        something the decorator rejects or the scan cannot read. The marker is
        then reported and not recorded, never half-read."""
        if call is None:
            return {}
        what = f"@donkey.{kind}"
        ok = True
        if call.args:
            self._issue("invalid-argument", qualname, line, f"{what} takes keyword arguments only")
            ok = False
        allowed = _TOOL_KEYWORDS if kind == "tool" else _AGENT_KEYWORDS
        values: dict[str, object] = {}
        for kw in call.keywords:
            if kw.arg is None:
                self._issue(
                    "non-literal-argument",
                    qualname,
                    line,
                    f"**{ast.unparse(kw.value)} expands keyword arguments a static scan "
                    f"cannot read; pass {what}'s arguments as literal keywords",
                )
                ok = False
            elif kw.arg not in allowed:
                self._issue(
                    "invalid-argument",
                    qualname,
                    line,
                    f"{what} has no {kw.arg}= argument; it takes {', '.join(sorted(allowed))}",
                )
                ok = False
            else:
                try:
                    values[kw.arg] = ast.literal_eval(kw.value)
                except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                    if kw.arg in _RECORDED:
                        self._issue(
                            "non-literal-argument",
                            qualname,
                            line,
                            f"{kw.arg}= is not a literal, so a static scan cannot read it",
                        )
                        ok = False
        valid = self._valid_tool if kind == "tool" else self._valid_agent
        return values if valid(values, qualname, line) and ok else None

    def _valid_tool(self, values: dict[str, object], qualname: str, line: int) -> bool:
        """The checks ``Donkey.tool`` runs at import time (donkey.py)."""
        problems: list[str] = []
        approval, risk = values.get("approval"), values.get("risk")
        if approval is not None and approval != "required":
            problems.append(f'approval must be "required" or None, not {approval!r}')
        if risk is not None and (not isinstance(risk, str) or not risk.strip()):
            problems.append(f"risk must be a non-empty label such as 'financial', not {risk!r}")
        if "approval_timeout" in values:
            try:
                parse_timeout(values["approval_timeout"])  # type: ignore[arg-type]
            except (ValueError, TypeError) as exc:
                problems.append(str(exc))
        for problem in problems:
            self._issue("invalid-argument", qualname, line, problem)
        server = values.get("server")
        server_ok = server is None or self._service_id(server, "server", qualname, line)
        return not problems and bool(server_ok)

    def _valid_agent(self, values: dict[str, object], qualname: str, line: int) -> bool:
        """The checks ``Donkey.agent`` runs at import time (donkey.py)."""
        ok = True
        if "name" not in values:
            self._issue("invalid-service-id", qualname, line, "@donkey.agent needs a literal name=")
            ok = False
        elif self._service_id(values["name"], "name", qualname, line) is None:
            ok = False
        skills = values.get("skills", ())
        if (
            not isinstance(skills, list | tuple)
            or not all(isinstance(s, str) and s.strip() for s in skills)
            or len(set(skills)) != len(skills)
        ):
            self._issue(
                "invalid-skills",
                qualname,
                line,
                f"skills must be a list of distinct skill IDs, not {skills!r}",
            )
            ok = False
        return ok

    def _service_id(self, value: object, what: str, qualname: str, line: int) -> str | None:
        try:
            return validate_service_id(value, what=what)
        except ValueError as exc:
            self._issue("invalid-service-id", qualname, line, str(exc))
            return None

    def _tool(self, node: _FuncDef, qualname: str, kwargs: dict[str, object]) -> None:
        server, approval, risk = kwargs.get("server"), kwargs.get("approval"), kwargs.get("risk")
        self.tools.append(
            ScannedTool(
                name=node.name,
                qualname=qualname,
                path=self.path,
                line=node.lineno,
                description=ast.get_docstring(node),
                is_async=isinstance(node, ast.AsyncFunctionDef),
                server=server if isinstance(server, str) else None,
                approval=approval if isinstance(approval, str) else None,
                risk=risk if isinstance(risk, str) else None,
                parameters=_parameters(
                    node, in_class=bool(self.scope) and self.scope[-1] != "<locals>"
                ),
            )
        )

    def _agent(self, node: _FuncDef, qualname: str, kwargs: dict[str, object]) -> None:
        skills = kwargs.get("skills", ())
        assert isinstance(skills, list | tuple)  # checked by _valid_agent
        self.agents.append(
            ScannedAgent(
                name=str(kwargs["name"]),
                qualname=qualname,
                path=self.path,
                line=node.lineno,
                description=ast.get_docstring(node),
                is_async=isinstance(node, ast.AsyncFunctionDef),
                skills=tuple(str(s) for s in skills),
            )
        )


def _parameters(node: _FuncDef, *, in_class: bool) -> tuple[ScannedParameter, ...]:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    if in_class and positional and positional[0].arg in ("self", "cls"):
        positional = positional[1:]
    params = [(a.arg, a) for a in positional]
    if args.vararg:
        params.append(("*" + args.vararg.arg, args.vararg))
    params.extend((a.arg, a) for a in args.kwonlyargs)
    if args.kwarg:
        params.append(("**" + args.kwarg.arg, args.kwarg))
    return tuple(
        ScannedParameter(name, ast.unparse(a.annotation) if a.annotation else None)
        for name, a in params
    )


def _servers(tools: list[ScannedTool]) -> tuple[ScannedMcpServer, ...]:
    by_server: dict[str, list[str]] = {}
    for t in tools:
        if t.server is not None:
            by_server.setdefault(t.server, []).append(t.name)
    return tuple(
        ScannedMcpServer(sid, tuple(sorted(names))) for sid, names in sorted(by_server.items())
    )


class _Declarations:
    """The ``[donkey.mcp_servers]`` and ``[donkey.agents]`` tables beside the
    scanned code (#1193, #1260).

    The project file is read first and the local file merged over it key by
    key, as ``DonkeyConfig`` merges ``[donkey]`` (#727). Only these two files
    count: a user-wide file says nothing about one repository's services.
    Everything wrong with a declaration is a :class:`ScanIssue`, never a raise.
    """

    def __init__(self, directory: Path, *, local_overlay: bool = True) -> None:
        self.issues: list[ScanIssue] = []
        # Table -> service ID -> key -> (value, the file that set it).
        self.entries: dict[str, dict[str, dict[str, tuple[object, str]]]] = {
            "mcp_servers": {},
            "agents": {},
        }
        # Table -> service ID -> the first file that declares it.
        self.declared_in: dict[str, dict[str, str]] = {"mcp_servers": {}, "agents": {}}
        for name in (TOML_NAME, LOCAL_TOML_NAME) if local_overlay else (TOML_NAME,):
            path = directory / name
            if path.is_file():
                self._read(path, name)

    def _issue(self, kind: str, subject: str, path: str, detail: str) -> None:
        self.issues.append(ScanIssue(kind, subject, path, 0, detail))

    def _read(self, path: Path, name: str) -> None:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            self._issue("unreadable", name, name, str(exc))
            return
        except tomllib.TOMLDecodeError as exc:
            self._issue("syntax-error", name, name, str(exc))
            return
        donkey = data.get("donkey", {})
        if not isinstance(donkey, dict):
            return
        self._read_table(donkey, name, "mcp_servers", "invalid-mcp-server", "mcp server")
        self._read_table(donkey, name, "agents", "invalid-agent", "agent")

    def _read_table(
        self, donkey: dict[str, Any], name: str, table_name: str, invalid: str, what: str
    ) -> None:
        keys = _SERVER_KEYS if table_name == "mcp_servers" else _AGENT_KEYS
        listed = " and ".join(sorted(keys))
        table = donkey.get(table_name, {})
        if not isinstance(table, dict):
            self._issue(
                invalid,
                f"donkey.{table_name}",
                name,
                f"[donkey.{table_name}] must be a table of service IDs",
            )
            return
        for sid, entry in table.items():
            subject = f"{what} {sid}"
            try:
                validate_service_id(sid, what=f"an [donkey.{table_name}] key")
            except ValueError as exc:
                self._issue("invalid-service-id", subject, name, str(exc))
                continue
            if not isinstance(entry, dict):
                self._issue(
                    invalid,
                    subject,
                    name,
                    f"[donkey.{table_name}.{sid}] must be a table with {listed}",
                )
                continue
            merged = self.entries[table_name].setdefault(sid, {})
            self.declared_in[table_name].setdefault(sid, name)
            for key, value in entry.items():
                if key in keys:
                    merged[key] = (value, name)
                else:
                    self._issue(
                        invalid, subject, name, f"unknown key {key!r}; the keys are {listed}"
                    )

    def apply(self, servers: tuple[ScannedMcpServer, ...]) -> tuple[ScannedMcpServer, ...]:
        """``servers`` with what is declared for each, plus the declaration issues."""
        named = {s.id for s in servers}
        for sid in sorted(self.entries["mcp_servers"]):
            if sid not in named:
                self._issue(
                    "unknown-server",
                    f"mcp server {sid}",
                    self.declared_in["mcp_servers"][sid],
                    f"no @donkey.tool names server={sid!r}, so this declaration describes "
                    "nothing; fix the ID or remove the table",
                )
        return tuple(self._declared(s) for s in servers)

    def apply_agents(self, agents: list[ScannedAgent]) -> list[ScannedAgent]:
        """``agents`` with the protocol and endpoint declared for each (#1260)."""
        named = {a.name for a in agents}
        for sid in sorted(self.entries["agents"]):
            if sid not in named:
                self._issue(
                    "unknown-agent",
                    f"agent {sid}",
                    self.declared_in["agents"][sid],
                    f"no @donkey.agent is named {sid!r}, so this declaration describes "
                    "nothing; fix the name or remove the table",
                )
        return [self._declared_agent(a) for a in agents]

    def _declared(self, server: ScannedMcpServer) -> ScannedMcpServer:
        entry = self.entries["mcp_servers"].get(server.id, {})
        subject = f"mcp server {server.id}"
        transport: str | None = None
        if "transport" in entry:
            value, source = entry["transport"]
            if isinstance(value, str) and value in MCP_TRANSPORTS:
                transport = value
            else:
                self._issue(
                    "invalid-transport",
                    subject,
                    source,
                    f"transport must be one of {', '.join(MCP_TRANSPORTS)}, not {value!r}",
                )
        endpoint: str | None = None
        if "endpoint" in entry:
            value, source = entry["endpoint"]
            problem = _endpoint_problem(value, transport)
            if problem is None:
                endpoint = str(value)
            else:
                self._issue("invalid-endpoint", subject, source, problem)
        return ScannedMcpServer(server.id, server.tools, transport, endpoint)

    def _declared_agent(self, agent: ScannedAgent) -> ScannedAgent:
        entry = self.entries["agents"].get(agent.name, {})
        if not entry:
            return agent
        subject = f"agent {agent.name}"
        protocol: str | None = None
        if "protocol" in entry:
            value, source = entry["protocol"]
            if isinstance(value, str) and value in AGENT_PROTOCOLS:
                protocol = value
            else:
                hint = (
                    "; a2a is A2A 0.3, and donkey.serve() speaks A2A 1.0, which is a2a_v1"
                    if value == "a2a"
                    else ""
                )
                self._issue(
                    "invalid-protocol",
                    subject,
                    source,
                    f"protocol must be one of {', '.join(AGENT_PROTOCOLS)}, not {value!r}{hint}",
                )
        endpoint: str | None = None
        if "endpoint" in entry:
            value, source = entry["endpoint"]
            problem = _endpoint_problem(value, None)
            if problem is None:
                endpoint = str(value)
            else:
                self._issue("invalid-endpoint", subject, source, problem)
        elif protocol == "a2a_v1":
            self._issue(
                "invalid-agent",
                subject,
                entry["protocol"][1],
                f"an a2a_v1 agent needs an endpoint: set endpoint in [donkey.agents.{agent.name}] "
                "to the URL it is served at, which its agent card names",
            )
        return replace(agent, protocol=protocol, endpoint=endpoint)


def _endpoint_problem(value: object, transport: str | None) -> str | None:
    """Why ``value`` is not a usable endpoint, or ``None`` when it is."""
    if transport == "stdio":
        return "a stdio server is started as a process and has no endpoint; remove it"
    if not isinstance(value, str):
        return f"endpoint must be an http or https URL, not {value!r}"
    # The credential check comes first, and its reason never holds the value: the
    # endpoint is published verbatim, and a malformed URL may still carry a
    # credential, so neither may be echoed (#1750).
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return (
            "endpoint must be an http or https URL, and this one does not parse "
            "(it is not shown, in case it carries a credential)"
        )
    credential = url_credential_problem(value)
    if credential is not None:
        return (
            f"endpoint {credential}; the governed transport supplies credentials, "
            "so remove them from the URL"
        )
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"endpoint must be an http or https URL, not {value!r}"
    return None


def _description_issues(
    agents: list[ScannedAgent], tools: list[ScannedTool]
) -> Iterator[ScanIssue]:
    # One description check for every target: the bundle builders reuse these
    # results rather than re-checking (#1104, #1105).
    entries = [(f"agent {a.name}", a.name, a.description, a.path, a.line) for a in agents]
    entries += [(f"tool {t.qualname}", t.name, t.description, t.path, t.line) for t in tools]
    for subject, name, description, path, line in entries:
        for found in check_description_quality([(name, description)]):
            yield ScanIssue(found.kind, subject, path, line, found.detail)


def _duplicate_issues(
    agents: list[ScannedAgent], tools: list[ScannedTool], servers: tuple[ScannedMcpServer, ...]
) -> Iterator[ScanIssue]:
    # A service ID names one service, agent or MCP server, so a repeat would
    # register two things under one permanent key (#1104, #1107).
    ids = Counter([a.name for a in agents] + [s.id for s in servers])
    for a in agents:
        if ids[a.name] > 1:
            yield ScanIssue(
                "duplicate-service-id",
                f"agent {a.name}",
                a.path,
                a.line,
                f"service ID {a.name!r} also names another agent or an MCP server",
            )
    seen: Counter[tuple[str, str]] = Counter(
        (t.server, t.name) for t in tools if t.server is not None
    )
    for t in tools:
        if t.server is not None and seen[(t.server, t.name)] > 1:
            yield ScanIssue(
                "duplicate-tool",
                f"tool {t.qualname}",
                t.path,
                t.line,
                f"server {t.server!r} has two tools named {t.name!r}",
            )
