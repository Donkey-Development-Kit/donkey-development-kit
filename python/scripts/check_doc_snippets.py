"""Check the Python snippets in the docs against the real SDK surface (#797).

Most doc bugs are a snippet that names something the SDK does not have: a
module that moved, a kwarg that was renamed, a method on the wrong object. This
script reads every Python fence in the READMEs, ``MIGRATION.md`` and the docs
site, and checks it statically against the installed ``donkey_kit``. Nothing in
a snippet is executed. Per fence it:

* imports every ``donkey_kit`` module the snippet imports, and looks up every
  name it imports from one;
* resolves attribute chains on ``donkey_kit`` objects, starting from imported
  names and from ``donkey`` (a :class:`~donkey_kit.Donkey` unless the snippet
  binds it to something else), following property and return annotations, so
  ``donkey.llm.client`` and ``donkey.langgraph.chat_model`` are both checked;
* binds each call's arguments to the resolved callable with
  ``inspect.signature(...).bind_partial``, so an unknown kwarg or one positional
  argument too many is reported;
* on a page whose frontmatter says ``status: live``, rejects a call to an API
  whose body unconditionally raises ``_verify.blocked(...)`` (§0.3): a live
  page must not show a call that can only raise ``NotImplementedError``.

Pages marked ``status: roadmap`` document planned API and are skipped. Anything
the checker cannot resolve (third-party objects, values it cannot type) is left
alone rather than guessed at, so a report is always a real mismatch. A fence
whose info string carries ``nocheck`` (```` ```python nocheck ````) is skipped;
use it only for deliberately invalid code, and say why next to it.

    python scripts/check_doc_snippets.py [ROOT]

``ROOT`` defaults to this checkout's repository root. Exits 1 and lists every
problem when any is found.
"""

from __future__ import annotations

import ast
import builtins
import functools
import importlib
import inspect
import re
import sys
import textwrap
import types
import typing
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]

#: The doc files whose Python fences are checked, relative to the repo root.
DOC_GLOBS = (
    "README.md",
    "MIGRATION.md",
    "python/README.md",
    "python/examples/*/README.md",
    "website/content/**/*.mdx",
    "website/components/*.mdx",
)

#: The page statuses (website frontmatter ``status:``), see website/README.md.
STATUSES = ("live", "offline-verified", "roadmap")

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_OPEN = re.compile(r"^(?P<indent>\s*)(?P<fence>```|~~~)\s*(?:python|py)\b(?P<info>[^\n]*)$")
_FLAGS = ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
#: Snippets may use syntax up to this version (``except*`` on the errors page);
#: an older interpreter cannot parse them, so it skips the fence instead.
_NEWEST_DOC_SYNTAX = (3, 11)


#: ``typing.Unpack`` / ``typing.Self`` are 3.11+; on 3.10 the SDK takes them from
#: ``typing_extensions``, so they are matched by name, not identity.
_UNPACK = "Unpack"
_SELF = "Self"


def frontmatter(text: str) -> dict[str, str]:
    """The ``key: value`` pairs of a page's YAML frontmatter (flat, string values)."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            out[key.strip()] = value.strip().strip("'\"")
    return out


@dataclass(frozen=True)
class Snippet:
    """One Python fence: where it is, its dedented code and its page's status."""

    path: str
    line: int
    code: str
    status: str | None


@dataclass(frozen=True)
class Problem:
    """A snippet that does not match the SDK."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def snippets(text: str, path: str) -> list[Snippet]:
    """Every checked Python fence in ``text`` (``nocheck`` fences are left out)."""
    status = frontmatter(text).get("status")
    lines = text.splitlines()
    found: list[Snippet] = []
    i = 0
    while i < len(lines):
        opened = _OPEN.match(lines[i])
        if not opened:
            i += 1
            continue
        fence, indent = opened.group("fence"), len(opened.group("indent"))
        body: list[str] = []
        j = i + 1
        while j < len(lines) and lines[j].strip() != fence:
            line = lines[j]
            body.append(line[indent:] if line[:indent].strip() == "" else line)
            j += 1
        if "nocheck" not in opened.group("info").split():
            found.append(Snippet(path, i + 2, textwrap.dedent("\n".join(body)), status))
        i = j + 1
    return found


def doc_files(root: Path) -> list[Path]:
    """The doc files under ``root`` matching :data:`DOC_GLOBS`, sorted."""
    return sorted({p for glob in DOC_GLOBS for p in root.glob(glob) if p.is_file()})


# --- the verification-blocked surface (§0.3) ---------------------------------


def _is_blocked_raise(node: ast.stmt) -> bool:
    if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
        return False
    func = node.exc.func
    return (isinstance(func, ast.Attribute) and func.attr == "blocked") or (
        isinstance(func, ast.Name) and func.id == "blocked"
    )


def blocked_surfaces(package: Path) -> frozenset[str]:
    """``module.qualname`` of every function whose body always raises ``_verify.blocked``.

    Only a ``raise _verify.blocked(...)`` at the top level of the function body
    counts: a guard inside an ``if`` blocks one configuration, not the API.
    """
    found: set[str] = set()

    def walk(body: list[ast.stmt], module: str, prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.body, module, f"{prefix}{node.name}.")
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if any(_is_blocked_raise(stmt) for stmt in node.body):
                    found.add(f"{module}.{prefix}{node.name}")

    for path in sorted(package.rglob("*.py")):
        parts = path.relative_to(package.parent).with_suffix("").parts
        module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        walk(ast.parse(path.read_text(encoding="utf-8")).body, module, "")
    return frozenset(found)


# --- static resolution of snippet names --------------------------------------


@dataclass(frozen=True)
class _Obj:
    """A real ``donkey_kit`` object: a module, a class or a function."""

    value: object


@dataclass(frozen=True)
class _Inst:
    """An instance of a ``donkey_kit`` class."""

    cls: type


@dataclass(frozen=True)
class _Bound:
    """A method looked up on an instance (its first parameter is bound)."""

    owner: type
    func: Callable[..., object]


_Val = _Obj | _Inst | _Bound | None


def _ours(value: object) -> bool:
    name = (
        value.__name__
        if isinstance(value, types.ModuleType)
        else getattr(value, "__module__", None)
    )
    return isinstance(name, str) and (name == "donkey_kit" or name.startswith("donkey_kit."))


def _wrap(value: object) -> _Val:
    if isinstance(value, types.ModuleType | type) or inspect.isroutine(value):
        return _Obj(value) if _ours(value) else None
    return None


@functools.cache
def _type_checking_names(module_name: str) -> dict[str, object]:
    """The ``donkey_kit`` names a module imports only under ``if TYPE_CHECKING:``.

    String annotations often name such a class (``-> LLMClient``); resolving
    them is what lets ``donkey.llm.client(...)`` be checked.
    """
    module = sys.modules.get(module_name)
    try:
        tree = ast.parse(inspect.getsource(module)) if module else None
    except (OSError, TypeError):
        tree = None
    names: dict[str, object] = {}
    package = module_name if hasattr(module, "__path__") else module_name.rpartition(".")[0]
    for block in tree.body if tree else []:
        if not (isinstance(block, ast.If) and "TYPE_CHECKING" in ast.unparse(block.test)):
            continue
        for node in block.body:
            if not isinstance(node, ast.ImportFrom) or not node.module and not node.level:
                continue
            source = "." * node.level + (node.module or "")
            try:
                imported = importlib.import_module(source, package)
            except ImportError:
                continue
            if not _ours(imported):
                continue
            for alias in node.names:
                if hasattr(imported, alias.name):
                    names[alias.asname or alias.name] = getattr(imported, alias.name)
    return names


def _evaluate_annotation(annotation: object, owner: object) -> object:
    if not isinstance(annotation, str):
        return annotation
    module_name = getattr(owner, "__module__", "") or ""
    module = sys.modules.get(module_name)
    namespace = {
        **vars(builtins),
        **vars(typing),
        **_type_checking_names(module_name),
        **(vars(module) if module else {}),
    }
    try:
        return eval(annotation, namespace)
    except Exception:  # noqa: BLE001 - a TYPE_CHECKING-only name is simply unresolvable
        return None


def _form_name(form: object) -> str | None:
    """The name of a ``typing``/``typing_extensions`` special form (``Self``, ``Unpack``)."""
    name = getattr(form, "_name", None) or getattr(form, "__name__", None)
    return name if isinstance(name, str) else None


def _returns(func: object, owner: type | None) -> _Val:
    """What calling ``func`` produces, from its return annotation (``None`` if unknown)."""
    if isinstance(func, type):
        return _Inst(func) if _ours(func) else None
    target = getattr(func, "__func__", func)
    annotation = getattr(target, "__annotations__", {}).get("return")
    if annotation is None:
        return None
    resolved = _evaluate_annotation(annotation, target)
    if _form_name(resolved) == _SELF:
        self_cls = getattr(func, "__self__", owner)
        resolved = self_cls if isinstance(self_cls, type) else owner
    if isinstance(resolved, type) and _ours(resolved):
        return _Inst(resolved)
    return None


def _expand_unpacked_kwargs(
    params: list[inspect.Parameter], func: object
) -> list[inspect.Parameter]:
    """Replace ``**kw: Unpack[SomeTypedDict]`` with the TypedDict's keys.

    ``Donkey.from_env`` and ``DonkeyConfig.resolve`` take their config fields
    this way, so without it any kwarg would bind.
    """
    if not params or params[-1].kind is not inspect.Parameter.VAR_KEYWORD:
        return params
    resolved = _evaluate_annotation(params[-1].annotation, getattr(func, "__func__", func))
    if _form_name(typing.get_origin(resolved)) != _UNPACK:
        return params
    (typed_dict,) = typing.get_args(resolved)
    keys = getattr(typed_dict, "__annotations__", None)
    if not isinstance(keys, dict):
        return params
    taken = {param.name for param in params}
    extra = [
        inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY, default=None)
        for key in keys
        if key not in taken
    ]
    return params[:-1] + extra


def _instance_attribute_known(cls: type, name: str) -> bool:
    """Whether some class in ``cls``'s MRO annotates or assigns ``self.<name>``."""
    for klass in cls.__mro__:
        if name in getattr(klass, "__annotations__", {}):
            return True
        if name in getattr(klass, "__slots__", ()):
            return True
        if not _ours(klass):
            continue
        try:
            source = textwrap.dedent(inspect.getsource(klass))
        except (OSError, TypeError):
            return True  # cannot read it, so cannot claim it is missing
        if re.search(rf"\bself\.{re.escape(name)}\s*(?::[^=\n]+)?=(?!=)", source):
            return True
    return False


def _has_dynamic_getattr(cls: type) -> bool:
    return any("__getattr__" in vars(klass) for klass in cls.__mro__ if klass is not object)


def _describe(value: _Val) -> str:
    if isinstance(value, _Inst):
        return f"a {value.cls.__name__}"
    if isinstance(value, _Bound):
        return f"`{value.owner.__name__}.{value.func.__name__}`"
    if isinstance(value, _Obj):
        name = getattr(value.value, "__qualname__", None) or getattr(value.value, "__name__", "")
        return f"`{name}`"
    return "it"


_SEEDED_NAMES = frozenset({"Donkey", "DonkeyConfig"})


@dataclass
class _Checker:
    snippet: Snippet
    blocked: frozenset[str]
    problems: list[Problem] = field(default_factory=list)
    env: dict[str, _Val] = field(default_factory=dict)
    _seen: set[int] = field(default_factory=set)

    def report(self, node: ast.AST, message: str) -> None:
        if id(node) in self._seen:
            return
        self._seen.add(id(node))
        line = self.snippet.line + getattr(node, "lineno", 1) - 1
        self.problems.append(Problem(self.snippet.path, line, message))

    # -- names ---------------------------------------------------------------

    def lookup(self, name: str) -> _Val:
        if name in self.env:
            return self.env[name]
        if name == "donkey":
            from donkey_kit import Donkey

            return _Inst(Donkey)
        if name in _SEEDED_NAMES:
            # Docs pages import the facade once and reuse it in later fences
            # (`donkey = Donkey.from_env()`), so resolve the two public
            # constructors even when this fence has no import. Error classes
            # and `classify` are not seeded on purpose: without the narrowing
            # an `except` or `isinstance` gives, they cause false positives.
            import donkey_kit

            return _wrap(getattr(donkey_kit, name))
        return None

    def bind_target(self, target: ast.expr, value: _Val) -> None:
        if isinstance(target, ast.Name):
            self.env[target.id] = value
        elif isinstance(target, ast.Tuple | ast.List):
            for element in target.elts:
                self.bind_target(element, None)
        elif isinstance(target, ast.Starred):
            self.bind_target(target.value, None)

    def bind_import(self, node: ast.Import | ast.ImportFrom) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = self.import_module(node, alias.name)
                if alias.asname:
                    self.env[alias.asname] = _Obj(module) if module else None
                else:
                    top = alias.name.split(".")[0]
                    self.env[top] = _wrap(sys.modules.get(top)) if module else None
            return
        if node.level or not node.module:
            for alias in node.names:
                self.env[alias.asname or alias.name] = None
            return
        module = self.import_module(node, node.module)
        for alias in node.names:
            bound = alias.asname or alias.name
            if module is None or alias.name == "*":
                self.env[bound] = None
                continue
            if hasattr(module, alias.name):
                self.env[bound] = _wrap(getattr(module, alias.name))
                continue
            sub = self.import_module(node, f"{node.module}.{alias.name}", quiet=True)
            if sub is None:
                self.report(node, f"cannot import name `{alias.name}` from `{node.module}`")
            self.env[bound] = _Obj(sub) if sub else None

    def import_module(
        self, node: ast.AST, name: str, *, quiet: bool = False
    ) -> types.ModuleType | None:
        if name.split(".")[0] != "donkey_kit":
            return None
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as exc:
            missing = exc.name or ""
            if not quiet and (missing == "donkey_kit" or missing.startswith("donkey_kit.")):
                self.report(node, f"no module named `{missing}`")
            return None
        except ImportError:  # an optional extra the snippet needs is not installed
            return None

    # -- expressions ---------------------------------------------------------

    def attribute(self, node: ast.Attribute, base: _Val) -> _Val:
        name = node.attr
        if isinstance(base, _Obj) and isinstance(base.value, types.ModuleType):
            module = base.value
            if hasattr(module, name):
                return _wrap(getattr(module, name))
            sub = self.import_module(node, f"{module.__name__}.{name}", quiet=True)
            if sub is None:
                self.report(node, f"module `{module.__name__}` has no attribute `{name}`")
            return _Obj(sub) if sub else None
        if isinstance(base, _Obj) and isinstance(base.value, type):
            cls = base.value
            try:
                inspect.getattr_static(cls, name)
            except AttributeError:
                if not _instance_attribute_known(cls, name) and not _has_dynamic_getattr(type(cls)):
                    self.report(node, f"`{cls.__name__}` has no attribute `{name}`")
                return None
            return _wrap(getattr(cls, name, None))
        if isinstance(base, _Inst):
            return self.instance_attribute(node, base.cls, name)
        return None

    def instance_attribute(self, node: ast.Attribute, cls: type, name: str) -> _Val:
        try:
            static = inspect.getattr_static(cls, name)
        except AttributeError:
            static = None
            if cls.__name__ == "Donkey" and _ours(cls):
                from donkey_kit.integrations import ADAPTERS

                spec = ADAPTERS.get(name)
                if spec is not None:
                    module = importlib.import_module(spec.module, "donkey_kit.integrations")
                    return _Inst(getattr(module, spec.cls))
                # Donkey.__getattr__ serves exactly ADAPTERS, so anything else is missing.
                dynamic = False
            else:
                dynamic = _has_dynamic_getattr(cls)
            if not _instance_attribute_known(cls, name) and not dynamic:
                self.report(node, f"`{cls.__name__}` has no attribute `{name}`")
            return None
        if isinstance(static, property):
            return _returns(static.fget, cls) if static.fget else None
        if isinstance(static, classmethod | staticmethod):
            return _wrap(getattr(cls, name))
        if inspect.isfunction(static):
            return _Bound(cls, static) if _ours(static) else None
        cached = getattr(static, "func", None)  # functools.cached_property
        if cached is not None and type(static).__name__ == "cached_property":
            return _returns(cached, cls)
        return None

    def evaluate(self, node: ast.expr) -> _Val:
        if isinstance(node, ast.Name):
            return self.lookup(node.id)
        if isinstance(node, ast.Attribute):
            return self.attribute(node, self.evaluate(node.value))
        if isinstance(node, ast.Await):
            return self.evaluate(node.value)
        if isinstance(node, ast.Call):
            return self.call(node)
        return None

    def call(self, node: ast.Call) -> _Val:
        callee = self.evaluate(node.func)
        if callee is None or (
            isinstance(callee, _Obj) and isinstance(callee.value, types.ModuleType)
        ):
            return None
        if isinstance(callee, _Inst):
            return None  # calling an instance: __call__, not worth guessing
        if isinstance(callee, _Bound):
            func: object = callee.func
            owner: type | None = callee.owner
        else:
            func = callee.value
            owner = func if isinstance(func, type) else None
        self.check_arguments(node, callee, func)
        self.check_blocked(node, func)
        return _returns(func, owner)

    def check_arguments(self, node: ast.Call, callee: _Val, func: object) -> None:
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
            kw.arg is None for kw in node.keywords
        ):
            return
        try:
            signature = inspect.signature(func)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        params = list(signature.parameters.values())
        if isinstance(callee, _Bound):
            params = params[1:]
        signature = signature.replace(parameters=_expand_unpacked_kwargs(params, func))
        args = [object()] * len(node.args)
        kwargs = {kw.arg: object() for kw in node.keywords if kw.arg}
        try:
            signature.bind_partial(*args, **kwargs)
        except TypeError as exc:
            self.report(node, f"call to {_describe(callee)} does not match its signature: {exc}")

    def check_blocked(self, node: ast.Call, func: object) -> None:
        if self.snippet.status != "live":
            return
        target = getattr(func, "__func__", func)
        qualname = getattr(target, "__qualname__", "")
        if f"{getattr(target, '__module__', '')}.{qualname}" in self.blocked:
            self.report(
                node,
                f"`{qualname}` raises `_verify.blocked` (§0.3), "
                "so a `status: live` page must not call it; mark the page "
                "`status: roadmap` or drop the call",
            )

    # -- statements ----------------------------------------------------------

    def run(self, tree: ast.Module) -> None:
        for node in _in_source_order(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                self.bind_import(node)
            elif isinstance(node, ast.Assign):
                value = self.evaluate(node.value)
                for target in node.targets:
                    self.bind_target(target, value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                self.bind_target(node.target, self.evaluate(node.value))
            elif isinstance(node, ast.With | ast.AsyncWith):
                for item in node.items:
                    value = self.evaluate(item.context_expr)
                    if item.optional_vars is not None:
                        self.bind_target(item.optional_vars, value)
            elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
                self.evaluate(node.iter)
                self.bind_target(node.target, None)
            elif isinstance(node, ast.arg):
                self.env[node.arg] = self.annotated(node.annotation)
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                self.env[node.name] = None
            elif isinstance(node, ast.ExceptHandler) and node.name:
                self.env[node.name] = None
            elif isinstance(node, ast.If | ast.IfExp | ast.Assert):
                self.narrow(node.test)
            elif isinstance(node, ast.Call):
                self.call(node)
            elif isinstance(node, ast.Attribute):
                self.evaluate(node)

    def narrow(self, test: ast.expr) -> None:
        """Rebind ``x`` after ``isinstance(x, Cls)``, as a type checker narrows it."""
        if (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "isinstance"
            and len(test.args) == 2
            and isinstance(test.args[0], ast.Name)
        ):
            self.env[test.args[0].id] = self.annotated(test.args[1])

    def annotated(self, annotation: ast.expr | None) -> _Val:
        if annotation is None:
            return None
        value = self.evaluate(annotation)
        if isinstance(value, _Obj) and isinstance(value.value, type):
            return _Inst(value.value)
        return None


def _in_source_order(tree: ast.AST) -> Iterator[ast.AST]:
    """Every node, parents before children, statements in source order.

    A binding statement (assignment, ``with``, import) yields before its body,
    so a name is bound before the code that uses it is checked.
    """
    for node in ast.iter_child_nodes(tree):
        yield node
        yield from _in_source_order(node)


def check_snippet(snippet: Snippet, blocked: frozenset[str]) -> list[Problem]:
    """The problems in one snippet (empty when it matches the SDK)."""
    if snippet.status == "roadmap":
        return []
    try:
        tree = compile(snippet.code, snippet.path, "exec", _FLAGS)
    except SyntaxError as exc:
        if sys.version_info < _NEWEST_DOC_SYNTAX:
            return []  # e.g. `except*` (3.11); the 3.11+ CI jobs judge the syntax
        line = snippet.line + (exc.lineno or 1) - 1
        return [Problem(snippet.path, line, f"not valid Python: {exc.msg}")]
    checker = _Checker(snippet, blocked)
    checker.run(tree)
    return checker.problems


def check(root: Path) -> list[Problem]:
    """Every problem in every Python fence of the docs under ``root``.

    Snippets are checked against the importable ``donkey_kit``, so run this
    with the checkout's package installed (``pip install -e python``).
    """
    package = importlib.import_module("donkey_kit")
    blocked = blocked_surfaces(Path(package.__file__ or "").parent)
    problems: list[Problem] = []
    for path in doc_files(root):
        rel = path.relative_to(root).as_posix()
        for snippet in snippets(path.read_text(encoding="utf-8"), rel):
            problems.extend(check_snippet(snippet, blocked))
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]).resolve() if args else _DEFAULT_ROOT
    problems = check(root)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} doc snippet problem(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
