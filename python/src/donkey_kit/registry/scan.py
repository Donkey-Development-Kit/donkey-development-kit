"""``donkey scan``: a static, deterministic model of a repository's agents, tools
and MCP servers (BG §2.5, #217).

:func:`scan` walks a directory, parses every ``.py`` file with :mod:`ast` and
finds the ``@donkey.agent`` and ``@donkey.tool`` markers (``core/toolspec.py``).
It **never imports or executes user code**: a marker counts only when its
receiver is statically a ``Donkey`` (the class imported from ``donkey_kit``, an
instance built from it, or such an instance imported from another scanned
module), and decorator arguments are read only when they are literals.

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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ..core.config import LOCAL_TOML_NAME, TOML_NAME
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
    ``non-literal-argument``, ``duplicate-service-id``, ``duplicate-tool``,
    ``syntax-error`` or ``unreadable``. A ``[donkey.mcp_servers]`` declaration
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


def scan(root: str | Path) -> ScanResult:
    """Statically scan ``root`` (a directory or one ``.py`` file) for agents,
    tools and MCP servers (BG §2.5, #217). Never imports the scanned code.

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
    exported = {name: mod.instance_names() for name, mod in modules.items()}
    changed = True
    while changed:
        changed = False
        for name, mod in modules.items():
            imported = mod.receivers(name, exported).instances - exported[name]
            if imported:
                exported[name] |= imported
                changed = True

    agents: list[ScannedAgent] = []
    tools: list[ScannedTool] = []
    for name, mod in modules.items():
        found = _Collector(mod.path, mod.receivers(name, exported))
        found.visit(mod.tree)
        agents.extend(found.agents)
        tools.extend(found.tools)
        issues.extend(found.issues)

    agents.sort(key=lambda a: (a.path, a.line))
    tools.sort(key=lambda t: (t.path, t.line))
    declared = _Declarations(origin)
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


def _is_donkey_call(node: ast.expr, classes: set[str], aliases: set[str]) -> bool:
    """``Donkey(...)`` or ``Donkey.from_env(...)``, through any import alias."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and not _is_class_ref(func, aliases, classes):
        func = func.value
    return _is_class_ref(func, aliases, classes)


def _is_class_ref(node: ast.expr, aliases: set[str], classes: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in classes
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "Donkey"
        and isinstance(node.value, ast.Name)
        and node.value.id in aliases
    )


@dataclass
class _Module:
    path: str
    tree: ast.Module

    def _imports(self) -> tuple[set[str], set[str]]:
        """Local names bound to the ``Donkey`` class, and to the ``donkey_kit`` module."""
        classes: set[str] = set()
        aliases: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("donkey_kit"):
                classes.update(a.asname or a.name for a in node.names if a.name == "Donkey")
            elif isinstance(node, ast.Import):
                aliases.update(a.asname or a.name for a in node.names if a.name == "donkey_kit")
        return classes, aliases

    def instance_names(self) -> set[str]:
        """Names this module binds to a ``Donkey`` instance."""
        classes, aliases = self._imports()
        names: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign) and _is_donkey_call(node.value, classes, aliases):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
                and _is_donkey_call(node.value, classes, aliases)
            ):
                names.add(node.target.id)
        return names

    def receivers(self, module: str, exported: dict[str, set[str]]) -> _Receivers:
        """What ``X`` may be in a ``@X.tool`` / ``@X.agent`` decorator here."""
        classes, aliases = self._imports()
        instances = self.instance_names()
        package = (
            module.split(".")[:-1] if not self.path.endswith("__init__.py") else module.split(".")
        )
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:  # from .gov import donkey
                anchor = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                source = ".".join([*anchor, node.module] if node.module else anchor)
            else:
                source = node.module or ""
            for alias in node.names:
                if alias.name in exported.get(source, set()):
                    instances.add(alias.asname or alias.name)
        return _Receivers(classes, aliases, instances)


@dataclass(frozen=True)
class _Receivers:
    classes: set[str]
    aliases: set[str]
    instances: set[str]

    def marker(self, decorator: ast.expr) -> tuple[str, ast.Call | None] | None:
        """``("tool" | "agent", call-or-None)`` when ``decorator`` is a marker."""
        call = decorator if isinstance(decorator, ast.Call) else None
        target = call.func if call else decorator
        if not isinstance(target, ast.Attribute) or target.attr not in ("tool", "agent"):
            return None
        recv = target.value
        known = _is_class_ref(recv, self.aliases, self.classes) or (
            isinstance(recv, ast.Name) and recv.id in self.instances
        )
        return (target.attr, call) if known else None


class _Collector(ast.NodeVisitor):
    def __init__(self, path: str, receivers: _Receivers) -> None:
        self.path = path
        self.receivers = receivers
        self.scope: list[str] = []
        self.agents: list[ScannedAgent] = []
        self.tools: list[ScannedTool] = []
        self.issues: list[ScanIssue] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def _function(self, node: _FuncDef) -> None:
        qualname = ".".join([*self.scope, node.name])
        for decorator in node.decorator_list:
            found = self.receivers.marker(decorator)
            if found is None:
                continue
            kind, call = found
            kwargs = self._literal_kwargs(call, qualname, node.lineno)
            if kind == "tool":
                self._tool(node, qualname, kwargs)
            else:
                self._agent(node, qualname, kwargs)
        self.scope.extend([node.name, "<locals>"])
        self.generic_visit(node)
        del self.scope[-2:]

    def _issue(self, kind: str, subject: str, line: int, detail: str) -> None:
        self.issues.append(ScanIssue(kind, subject, self.path, line, detail))

    def _literal_kwargs(self, call: ast.Call | None, qualname: str, line: int) -> dict[str, object]:
        values: dict[str, object] = {}
        for kw in call.keywords if call else []:
            if kw.arg is None:
                continue
            try:
                values[kw.arg] = ast.literal_eval(kw.value)
            except (ValueError, TypeError, SyntaxError):
                self._issue(
                    "non-literal-argument",
                    qualname,
                    line,
                    f"{kw.arg}= is not a literal, so a static scan cannot read it",
                )
        return values

    def _service_id(self, value: object, what: str, qualname: str, line: int) -> str | None:
        try:
            return validate_service_id(value, what=what)
        except ValueError as exc:
            self._issue("invalid-service-id", qualname, line, str(exc))
            return None

    def _tool(self, node: _FuncDef, qualname: str, kwargs: dict[str, object]) -> None:
        server = kwargs.get("server")
        if server is not None:
            server = self._service_id(server, "server", qualname, node.lineno)
        approval, risk = kwargs.get("approval"), kwargs.get("risk")
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
        if "name" not in kwargs:
            self._issue(
                "invalid-service-id", qualname, node.lineno, "@donkey.agent needs a literal name="
            )
            return
        name = self._service_id(kwargs["name"], "name", qualname, node.lineno)
        skills = kwargs.get("skills", ())
        # The same rule @donkey.agent enforces at import time (donkey.py).
        if (
            not isinstance(skills, list | tuple)
            or not all(isinstance(s, str) and s.strip() for s in skills)
            or len(set(skills)) != len(skills)
        ):
            self._issue(
                "invalid-skills",
                qualname,
                node.lineno,
                f"skills must be a list of distinct skill IDs, not {skills!r}",
            )
            skills = ()
        if name is None:
            return
        self.agents.append(
            ScannedAgent(
                name=name,
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

    def __init__(self, directory: Path) -> None:
        self.issues: list[ScanIssue] = []
        # Table -> service ID -> key -> (value, the file that set it).
        self.entries: dict[str, dict[str, dict[str, tuple[object, str]]]] = {
            "mcp_servers": {},
            "agents": {},
        }
        # Table -> service ID -> the first file that declares it.
        self.declared_in: dict[str, dict[str, str]] = {"mcp_servers": {}, "agents": {}}
        for name in (TOML_NAME, LOCAL_TOML_NAME):
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
                "an a2a_v1 agent needs an endpoint: its agent card names the URL it is served at",
            )
        return replace(agent, protocol=protocol, endpoint=endpoint)


def _endpoint_problem(value: object, transport: str | None) -> str | None:
    """Why ``value`` is not a usable endpoint, or ``None`` when it is."""
    if transport == "stdio":
        return "a stdio server is started as a process and has no endpoint; remove it"
    if not isinstance(value, str):
        return f"endpoint must be an http or https URL, not {value!r}"
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        return f"endpoint must be an http or https URL, not {value!r}"
    if parts.username is not None or parts.password is not None:
        # Never echo the value: it carries a credential.
        return "endpoint must not carry credentials; the governed transport supplies them"
    for name, _ in parse_qsl(parts.query, keep_blank_values=True):
        if _looks_like_credential(name):
            # The endpoint is published verbatim, so a credential here would be public.
            return (
                f"endpoint must not carry credentials: its query parameter {name!r} looks "
                "like one, and the governed transport supplies them"
            )
    return None


# Query parameter names that carry a credential, compared lower-cased with "-"
# and "_" removed: an exact name, or a fragment anywhere in the name.
_CREDENTIAL_PARAMS = frozenset({"auth", "key", "pwd", "sig"})
_CREDENTIAL_FRAGMENTS = (
    "accesskey",
    "apikey",
    "authorization",
    "clientid",
    "credential",
    "passwd",
    "password",
    "secret",
    "signature",
    "token",
)


def _looks_like_credential(name: str) -> bool:
    key = name.lower().replace("-", "").replace("_", "")
    return key in _CREDENTIAL_PARAMS or any(f in key for f in _CREDENTIAL_FRAGMENTS)


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
