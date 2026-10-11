"""Generate the docs that restate facts the code owns (#795).

Each class of fact has one owner. Where a page restates a fact the code
already holds, this script writes that part of the page from the code, and a
unit test (``tests/unit/test_generated_docs.py``) fails when the committed
page and the code disagree, the same way the ``docs-llms-drift`` CI job fails
on stale ``website/public`` files.

Targets:

* ``python/README.md`` — the PyPI description, written whole from the
  repository ``README.md`` with every relative link made absolute (PyPI does
  not resolve them). Edit the root ``README.md``, never this copy.
* ``website/content/reference/configuration.mdx`` — the ``settings-index``
  region: every ``DonkeyConfig`` field with its env var, config-file key and
  default, from the field table in ``core/config.py``.
* ``website/content/cli.mdx`` — the ``cli-reference`` region: every visible
  ``donkey`` command and option, from the typer app. Needs the ``[cli]`` extra.
* ``website/content/frameworks/index.mdx`` — the ``adapter-capabilities``
  region: each adapter's extra and each factory's ``capabilities()``, from the
  ``ADAPTERS`` roster and the adapter classes.
* ``website/content/reference/api.mdx`` — the Python API reference, written
  whole from the public signatures and the first paragraph of each docstring.

A region sits between two marker comments, ``BEGIN GENERATED: <name>`` and
``END GENERATED: <name>``; everything between them is replaced.

    python scripts/generate_docs.py           # rewrite every target
    python scripts/generate_docs.py --check   # exit 1 and list stale targets

A target whose optional dependency is not installed is skipped and named.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import importlib.util
import inspect
import re
import sys
import typing
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
REPO_URL = "https://github.com/Donkey-Development-Kit/donkey-development-kit"
# Reader-facing links point at the released branch (website/lib/repo.mjs).
DOCS_REF = "main"
SITE = "https://docs.donkey-kit.dev"

_SCRIPT = "python/scripts/generate_docs.py"


# --- regions -----------------------------------------------------------------


class GenerationError(Exception):
    """A target file is missing its markers or cannot be rendered."""


def replace_region(text: str, name: str, body: str) -> str:
    """``text`` with the lines between the ``name`` markers replaced by ``body``.

    The marker lines themselves are kept as written, so a page chooses its own
    comment syntax (``<!-- -->`` in Markdown, ``{/* */}`` in MDX).
    """
    lines = text.splitlines(keepends=True)
    begin = [i for i, line in enumerate(lines) if f"BEGIN GENERATED: {name} " in line]
    end = [i for i, line in enumerate(lines) if f"END GENERATED: {name} " in line]
    if len(begin) != 1 or len(end) != 1 or end[0] < begin[0]:
        raise GenerationError(
            f"expected one 'BEGIN GENERATED: {name}' marker followed by one "
            f"'END GENERATED: {name}' marker"
        )
    return "".join([*lines[: begin[0] + 1], body.rstrip("\n") + "\n", *lines[end[0] :]])


# --- Markdown helpers ----------------------------------------------------------

_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_ROLE = re.compile(r":[a-z]+:`(~?)([^`<]+?)(?:\s*<([^`>]+)>)?`")
_REF = (
    r"(?:`?BG §[\d.]+`?|§[\d.]+|#\d+(?:/#\d+)*|ADR \d+|ADR-\d+|"
    r"docs/[\w./-]+(?: §[\d.]+)?|upstream gap #\d+|AC\d+|hazard #\d+|"
    r"config resolution|the conformance kit|Phase \d+(?:\.\d+)?)"
)
# A parenthetical that holds nothing but references: "(BG §1.1, #195)".
_REFS = re.compile(rf"\s*\(\s*{_REF}(?:\s*[,;]\s*{_REF})*\s*\)")
# A reference closing a parenthetical that says more: "(..., #352)".
_TRAILING_REF = re.compile(rf"(?:\s*[,;]\s*{_REF})+(?=\))")
# An inline pointer into the ledger: "a governed response's docs/verified-apis.md §3 headers".
_LEDGER_MENTION = re.compile(r"\s*docs/[\w./-]+\.md §[\d.]+")
_DOCS_LINK = re.compile(rf"^Docs:\s*({re.escape(SITE)}\S*)\s*$", re.MULTILINE)


def _outside_code(text: str, fn: Callable[[str], str]) -> str:
    """Apply ``fn`` to the parts of ``text`` that are not inline code spans."""
    out: list[str] = []
    last = 0
    for match in _CODE_SPAN.finditer(text):
        out.append(fn(text[last : match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(fn(text[last:]))
    return "".join(out)


def _escape_mdx(text: str) -> str:
    return re.sub(r"([{}<])", r"\\\1", text)


def rst_to_markdown(text: str) -> str:
    """One docstring paragraph as MDX-safe Markdown on a single line.

    Sphinx roles become code spans (``:class:`~a.B``` → ```B```), double
    backticks become single ones, reference-only parentheticals such as
    ``(BG §1.1, #195)`` are dropped, and ``{``, ``}`` and ``<`` outside code
    are escaped so MDX does not read them as JSX.
    """
    text = " ".join(text.split())

    def role(match: re.Match[str]) -> str:
        tilde, title, target = match.groups()
        name = (target or title).strip()
        if target is None and tilde:
            name = name.rsplit(".", 1)[-1]
        if target is not None:
            name = title.strip()
        return f"`{name}`"

    text = _REFS.sub("", text)
    text = _TRAILING_REF.sub("", text)
    text = _LEDGER_MENTION.sub("", text)
    text = _ROLE.sub(role, text)
    text = re.sub(r"``(.+?)``", r"`\1`", text)
    # A paragraph that introduces an example ends "...::" or "...:".
    text = re.sub(r":+$", ".", text)
    return _outside_code(text, _escape_mdx)


def _cell(text: str) -> str:
    """``text`` made safe for a Markdown table cell."""
    return text.replace("|", "\\|")


def _table(header: Iterable[str], rows: Iterable[Iterable[str]]) -> str:
    head = list(header)
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _summary(obj: object) -> str:
    """The first paragraph of ``obj``'s docstring, as Markdown."""
    doc = inspect.getdoc(obj) or ""
    first = doc.split("\n\n", 1)[0].strip()
    return rst_to_markdown(first) if first else ""


def _docs_route(obj: object) -> str | None:
    """The site route a ``Docs: https://docs.donkey-kit.dev/...`` line names."""
    match = _DOCS_LINK.search(inspect.getdoc(obj) or "")
    if match is None:
        return None
    route = match.group(1)[len(SITE) :]
    return route or "/"


# --- README.md -> python/README.md ----------------------------------------------

_MD_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")
_HTML_ATTR = re.compile(r"""(\s(?:src|href)=")([^"]+)(")""")
_ABSOLUTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#)", re.I)
_RAW_URL = "https://raw.githubusercontent.com/Donkey-Development-Kit/donkey-development-kit"


def _absolute(target: str, *, image: bool) -> str:
    if _ABSOLUTE.match(target):
        return target
    path = target.removeprefix("./")
    if image:
        return f"{_RAW_URL}/{DOCS_REF}/{path}"
    kind = "tree" if path.split("#", 1)[0].endswith("/") else "blob"
    return f"{REPO_URL}/{kind}/{DOCS_REF}/{path}"


def pypi_readme(root_readme: str) -> str:
    """The PyPI description: the repository README with absolute links.

    Inline Markdown links and HTML ``src``/``href`` attributes are rewritten;
    images go to ``raw.githubusercontent.com`` so PyPI can load them. Links in
    fenced code blocks are left alone.
    """
    out: list[str] = []
    fenced = False
    for line in root_readme.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced:
            line = _MD_LINK.sub(
                lambda m: (
                    f"{m.group(1)}[{m.group(2)}]({_absolute(m.group(3), image=bool(m.group(1)))})"
                ),
                line,
            )
            line = _HTML_ATTR.sub(
                lambda m: (
                    m.group(1) + _absolute(m.group(2), image="src=" in m.group(1)) + m.group(3)
                ),
                line,
            )
        out.append(line)
    text = "".join(out)
    return text.rstrip("\n") + (
        "\n\n<!-- Generated from the repository README.md by "
        f"{_SCRIPT}; edit that file, then run the script. -->\n"
    )


# --- configuration: settings index ----------------------------------------------


def _default_text(value: object) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    return f"`{value}`"


def settings_index() -> str:
    """Every ``DonkeyConfig`` field from the field table, in its order."""
    from donkey_kit.core import config

    defaults = {f.name: _default_text(f.default) for f in dataclasses.fields(config.DonkeyConfig)}
    # ``None`` on a header field means "send the built-in name", not "unset".
    defaults.update((key, f"`{name}`") for key, name in config._HEADER_KEYS)  # noqa: SLF001
    defaults["base_url"] = "from `region`"
    rows = [
        (
            f"`{spec.env}`",
            f"`{spec.name}`",
            f"`{spec.toml_key}`",
            defaults[spec.name],
        )
        for spec in config._FIELDS  # noqa: SLF001  (the one config table, as its test reads it)
    ]
    intro = (
        "Every `DonkeyConfig` field except `cost` (set per dimension, see\n"
        "[Cost-attribution tags](#cost-attribution-tags)), in the order the SDK reads\n"
        f"them. The config-file key goes in the `[donkey]` table of `{config.TOML_NAME}`.\n"
        "This table is generated from the field table in `core/config.py`.\n"
    )
    header = ("Env var", "`DonkeyConfig` field", "Config-file key", "Default")
    return intro + "\n" + _table(header, rows)


# --- CLI reference -------------------------------------------------------------

_TRAILING_REFS = re.compile(r"\s*\([^()]*\)\.?$")


def _command_summary(help_text: str | None) -> str:
    first = (help_text or "").strip().split("\n\n", 1)[0]
    first = " ".join(first.split())
    first = _TRAILING_REFS.sub("", first).rstrip(".")
    return rst_to_markdown(first + ".") if first else ""


def _option_default(param: Any) -> str:  # noqa: ANN401  (a click.Parameter from typer)
    if getattr(param, "is_flag", False):
        return "off" if not param.default else "on"
    default = param.default
    if default is None or default == () or default == []:
        return "none"
    return f"`{default}`"


def _option_rows(params: Iterable[Any]) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for param in params:
        if getattr(param, "hidden", False) or not param.opts:
            continue
        flags = ", ".join(f"`{opt}`" for opt in param.opts)
        meaning = rst_to_markdown(" ".join((param.help or "").split()))
        meaning = meaning[:1].upper() + meaning[1:]
        if meaning and not meaning.endswith("."):
            meaning += "."
        if param.multiple and "repeatable" not in meaning.lower():
            meaning += " Repeatable."
        rows.append((flags, _option_default(param), meaning.strip()))
    return rows


def cli_reference() -> str:
    """Every visible command of ``donkey`` and its options, from the typer app."""
    import typer.main

    import donkey_kit.cli  # noqa: F401  (registers every command on the app)
    from donkey_kit.cli._app import app

    group = typer.main.get_command(app)
    parts = [
        "Generated from the CLI itself; `donkey <command> --help` prints the same.",
        "",
        "Global options go before the command (`donkey --json init`):",
        "",
        _table(("Option", "Default", "Meaning"), _option_rows(group.params)),
    ]
    commands = getattr(group, "commands", {})
    for name in sorted(commands):
        command = commands[name]
        if command.hidden:
            continue
        parts += ["", f"`donkey {name}`: {_command_summary(command.help)}"]
        rows = _option_rows(command.params)
        if rows:
            parts += ["", _table(("Option", "Default", "Meaning"), rows)]
        else:
            parts += ["", "No options of its own."]
    return "\n".join(parts)


# --- framework adapters ----------------------------------------------------------

_CAPABILITY_FIELDS = ("transport", "sync", "streaming", "typed_refusals", "observes_last_call")


def _capability(value: object) -> str:
    if isinstance(value, bool):
        return "✅" if value else "❌"
    return f"`{value}`"


def adapter_capabilities() -> str:
    """Each adapter factory's ``capabilities()``, from the adapter roster."""
    from donkey_kit import integrations

    rows: list[tuple[str, ...]] = []
    for spec in integrations.ADAPTERS.values():
        module = importlib.import_module(spec.module, integrations.__name__)
        cls = getattr(module, spec.cls)
        for factory, caps in cls.factories.items():
            rows.append(
                (
                    f"`donkey.{spec.attr}.{factory}()`",
                    f"`[{spec.extra}]`",
                    *(_capability(getattr(caps, field)) for field in _CAPABILITY_FIELDS),
                )
            )
    header = ("Factory", "Extra", *(f"`{field}`" for field in _CAPABILITY_FIELDS))
    return (
        "What each factory reports, generated from the adapter roster and each\n"
        "adapter's declared `factories`:\n\n" + _table(header, rows)
    )


# --- API reference -------------------------------------------------------------


def _annotation(value: object) -> str:
    if value is inspect.Parameter.empty:
        return ""
    if isinstance(value, str):
        return value
    return str(inspect.formatannotation(value))


def _default(value: object) -> str:
    text = repr(value)
    return "..." if " at 0x" in text else text


def _param(param: inspect.Parameter) -> str:
    text = param.name
    if param.kind is inspect.Parameter.VAR_POSITIONAL:
        text = "*" + text
    elif param.kind is inspect.Parameter.VAR_KEYWORD:
        text = "**" + text
    annotation = _annotation(param.annotation)
    if annotation:
        text += f": {annotation}"
    if param.default is not param.empty:
        text += f" = {_default(param.default)}" if annotation else f"={_default(param.default)}"
    return text


def format_signature(call: str, func: Callable[..., Any], *, bound: bool) -> str:
    """``call(params) -> return``, one parameter per line when it runs long.

    A coroutine function is shown as ``async call(...)``, and a function
    wrapped by ``@contextmanager``/``@asynccontextmanager`` with the context
    manager it returns rather than its generator's annotation.
    ``bound`` drops the first parameter (``self`` or ``cls``). Annotations are
    printed as written in the source, so the output does not depend on which
    optional packages are installed.
    """
    signature = inspect.signature(func)
    params = list(signature.parameters.values())[1 if bound else 0 :]
    parts: list[str] = []
    star = False
    for index, param in enumerate(params):
        if param.kind is param.VAR_POSITIONAL:
            star = True
        if param.kind is param.KEYWORD_ONLY and not star:
            parts.append("*")
            star = True
        parts.append(_param(param))
        if param.kind is param.POSITIONAL_ONLY and (
            index + 1 == len(params) or params[index + 1].kind is not param.POSITIONAL_ONLY
        ):
            parts.append("/")
    returns = _annotation(signature.return_annotation)
    namespace = getattr(inspect.unwrap(func), "__globals__", {})
    parts = [_public_type_vars(part, namespace) for part in parts]
    returns = _public_type_vars(returns, namespace)
    wrapped = getattr(func, "__wrapped__", None)
    if inspect.isasyncgenfunction(wrapped):
        returns = _context_manager("AbstractAsyncContextManager", returns)
    elif inspect.isgeneratorfunction(wrapped):
        returns = _context_manager("AbstractContextManager", returns)
    if inspect.iscoroutinefunction(func):
        call = f"async {call}"
    tail = f" -> {returns}" if returns else ""
    one_line = f"{call}({', '.join(parts)}){tail}"
    if len(one_line) <= 88:
        return one_line
    inner = "".join(f"    {part},\n" for part in parts)
    return f"{call}(\n{inner}){tail}"


_PRIVATE_NAME = re.compile(r"\b_[A-Za-z]\w*")


def _public_type_vars(text: str, namespace: dict[str, Any]) -> str:
    """``text`` with private type-variable names shown without the underscore.

    ``Callable[_P, _R]`` reads as ``Callable[P, R]``; a private name that is not
    a ``TypeVar`` or ``ParamSpec`` in the function's module is left alone.
    """

    def public(match: re.Match[str]) -> str:
        name = match.group(0)
        is_var = isinstance(namespace.get(name), (typing.TypeVar, typing.ParamSpec))
        return name[1:] if is_var else name

    return _PRIVATE_NAME.sub(public, text)


_YIELDS = re.compile(r"^(?:[\w.]+\.)?(?:Async)?(?:Iterator|Generator)\[([^,\]]+)")


def _context_manager(kind: str, returns: str) -> str:
    """The type a ``@contextmanager`` function returns, from its generator annotation.

    ``inspect.signature`` follows ``__wrapped__`` to the generator, whose
    annotation (``AsyncIterator[None]``) is not what a caller gets back.
    """
    match = _YIELDS.match(returns)
    return f"{kind}[{match.group(1).strip() if match else 'Any'}]"


@dataclass(frozen=True)
class _ClassDoc:
    cls: type
    #: How code reaches an instance: ``donkey``, ``donkey.llm``, ...
    instance: str
    title: str
    intro: str = ""
    #: Members left out (documented elsewhere, or not for callers).
    skip: frozenset[str] = frozenset()
    #: Whether to show the constructor.
    constructor: bool = False


def _members(cls: type, skip: frozenset[str]) -> list[tuple[str, object]]:
    return [
        (name, obj)
        for name, obj in vars(cls).items()
        if not name.startswith("_") and name not in skip and not _is_data(obj)
    ]


def _is_data(obj: object) -> bool:
    return not (isinstance(obj, (property, classmethod, staticmethod)) or inspect.isfunction(obj))


def _member_section(doc: _ClassDoc, name: str, obj: object) -> list[str]:
    owner = doc.cls.__name__
    if isinstance(obj, property):
        assert obj.fget is not None
        returns = _annotation(inspect.signature(obj.fget).return_annotation)
        heading = f"`{doc.instance}.{name}`"
        code = f"{doc.instance}.{name}: {returns}" if returns else f"{doc.instance}.{name}"
        target: object = obj
    elif isinstance(obj, classmethod):
        heading = f"`{owner}.{name}()`"
        code = format_signature(f"{owner}.{name}", obj.__func__, bound=True)
        target = obj.__func__
    elif isinstance(obj, staticmethod):
        heading = f"`{doc.instance}.{name}()`"
        code = format_signature(f"{doc.instance}.{name}", obj.__func__, bound=False)
        target = obj.__func__
    else:
        assert callable(obj)
        heading = f"`{doc.instance}.{name}()`"
        code = format_signature(f"{doc.instance}.{name}", obj, bound=True)
        target = obj
    lines = ["", f"### {heading}", "", "```python", code, "```", ""]
    summary = _summary(target)
    if summary:
        lines.append(summary)
    route = _docs_route(target)
    if route:
        lines += ["", f"Guide: [`{route}`]({route})"]
    return lines


def _class_section(doc: _ClassDoc) -> list[str]:
    lines = ["", f"## {doc.title}", ""]
    summary = _summary(doc.cls)
    if summary:
        lines.append(summary)
    if doc.intro:
        lines += ["", doc.intro]
    if doc.constructor:
        init = vars(doc.cls)["__init__"]
        lines += [
            "",
            "```python",
            format_signature(doc.cls.__name__, init, bound=True),
            "```",
        ]
    for name, obj in _members(doc.cls, doc.skip):
        lines += _member_section(doc, name, obj)
    return lines


def _export_summary(obj: object) -> str:
    if typing.get_origin(obj) is typing.Literal:
        return "One of " + ", ".join(f"`{value}`" for value in typing.get_args(obj)) + "."
    return _summary(obj)


def _kind(obj: object) -> str:
    if inspect.isclass(obj):
        if issubclass(obj, BaseException):
            return "exception"
        return "class"
    if inspect.isfunction(obj):
        return "function"
    return "type alias"


def api_reference() -> str:
    """The whole ``reference/api.mdx`` page."""
    import donkey_kit
    from donkey_kit import Budget, Donkey, DonkeyConfig, LastCall, LLMClient, integrations

    docs = [
        _ClassDoc(
            Donkey,
            "donkey",
            "`Donkey`",
            intro=(
                "Each framework adapter is an attribute too, listed under\n"
                "[Framework adapters](#framework-adapters)."
            ),
            constructor=True,
        ),
        _ClassDoc(
            DonkeyConfig,
            "config",
            "`DonkeyConfig`",
            intro=(
                "Its fields are listed in [Configuration](/reference/configuration#all-settings)."
            ),
        ),
        _ClassDoc(LLMClient, "donkey.llm", "`LLMClient` (`donkey.llm`)"),
        _ClassDoc(Budget, "donkey.budget", "`Budget` (`donkey.budget`)"),
        _ClassDoc(
            LastCall,
            "donkey.last_call",
            "`LastCall` (`donkey.last_call`)",
            intro="Its fields are listed in [`last_call` fields](/reference/last-call).",
        ),
    ]

    lines = [
        "---",
        "description: The public Python API of donkey-kit — Donkey, DonkeyConfig, the model "
        "client, the budget window, last_call, the adapters and every export — generated "
        "from the code.",
        "---",
        "",
        f"{{/* Generated by {_SCRIPT} from the package's signatures and docstrings. Do not",
        "edit by hand: change the docstring, then run the script. */}",
        "",
        "# Python API reference",
        "",
        "The public API of `donkey-kit`, generated from its",
        "signatures and docstrings. Each entry shows the first paragraph of the",
        "docstring; `help()` on the object prints the rest. Everything listed here is",
        "importable from `donkey_kit`.",
    ]
    for doc in docs:
        lines += _class_section(doc)

    lines += [
        "",
        "## Framework adapters",
        "",
        "`donkey.<attribute>` imports the adapter on first use. If its framework is",
        "not installed, the access raises `ImportError` with the `pip install` line.",
        "See [Frameworks](/frameworks) for what each factory returns.",
        "",
        _table(
            ("Attribute", "Install", "Factories"),
            (
                (
                    f"`donkey.{spec.attr}`",
                    f'`pip install "donkey-kit[{spec.extra}]"`',
                    ", ".join(
                        f"`{name}()`"
                        for name in getattr(
                            importlib.import_module(spec.module, integrations.__name__),
                            spec.cls,
                        ).factories
                    ),
                )
                for spec in integrations.ADAPTERS.values()
            ),
        ),
    ]

    exports = [(name, getattr(donkey_kit, name)) for name in donkey_kit.__all__]
    errors = [(n, o) for n, o in exports if inspect.isclass(o) and issubclass(o, BaseException)]
    others = [(n, o) for n, o in exports if (n, o) not in errors and not n.startswith("_")]
    lines += [
        "",
        "## Exceptions",
        "",
        "Every typed refusal and SDK error. See [Typed refusals](/errors) for when each",
        "is raised.",
        "",
        _table(
            ("Exception", "Base", "Meaning"),
            ((f"`{n}`", f"`{o.__mro__[1].__name__}`", _summary(o)) for n, o in sorted(errors)),
        ),
        "",
        "## Other exports",
        "",
        _table(
            ("Name", "Kind", "Meaning"),
            ((f"`{n}`", _kind(o), _export_summary(o)) for n, o in sorted(others)),
        ),
    ]
    return "\n".join(lines) + "\n"


# --- targets ---------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """One generated file or region."""

    name: str
    path: Path
    #: The region's marker name, or ``None`` when the whole file is generated.
    region: str | None
    render: Callable[[], str]
    #: Modules the renderer imports; a missing one skips the target.
    needs: tuple[str, ...] = ()

    def available(self) -> bool:
        return all(importlib.util.find_spec(module) is not None for module in self.needs)

    def expected(self) -> str:
        if self.region is None:
            return self.render()
        current = self.path.read_text(encoding="utf-8")
        return replace_region(current, self.region, self.render())


def _pypi_readme() -> str:
    return pypi_readme((REPO / "README.md").read_text(encoding="utf-8"))


TARGETS: tuple[Target, ...] = (
    Target("pypi-readme", REPO / "python" / "README.md", None, _pypi_readme),
    Target(
        "settings-index",
        REPO / "website" / "content" / "reference" / "configuration.mdx",
        "settings-index",
        settings_index,
    ),
    Target(
        "cli-reference",
        REPO / "website" / "content" / "cli.mdx",
        "cli-reference",
        cli_reference,
        needs=("typer",),
    ),
    Target(
        "adapter-capabilities",
        REPO / "website" / "content" / "frameworks" / "index.mdx",
        "adapter-capabilities",
        adapter_capabilities,
    ),
    Target(
        "api-reference",
        REPO / "website" / "content" / "reference" / "api.mdx",
        None,
        api_reference,
    ),
)


def stale(targets: Iterable[Target] = TARGETS) -> list[str]:
    """The names of the targets whose committed text differs from the code."""
    out: list[str] = []
    for target in targets:
        if not target.available():
            continue
        current = target.path.read_text(encoding="utf-8") if target.path.is_file() else ""
        if current != target.expected():
            out.append(target.name)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if any target is stale; write nothing"
    )
    args = parser.parse_args(argv)
    if not (REPO / "website").is_dir():
        print("generate_docs: no website/ next to python/; run it from a checkout")
        return 2
    failed: list[str] = []
    for target in TARGETS:
        rel = target.path.relative_to(REPO)
        if not target.available():
            print(f"skipped {target.name} ({rel}): needs {', '.join(target.needs)}")
            continue
        expected = target.expected()
        current = target.path.read_text(encoding="utf-8") if target.path.is_file() else ""
        if current == expected:
            continue
        if args.check:
            failed.append(f"{target.name} ({rel})")
        else:
            target.path.write_text(expected, encoding="utf-8")
            print(f"wrote {target.name} ({rel})")
    if failed:
        print("Stale generated docs; run: python scripts/generate_docs.py")
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
