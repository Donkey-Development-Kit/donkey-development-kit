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
* Per-framework verification status (#1081), owned by ``docs/verified-apis.md``:
  the §8 Status column rates each factory, the §8.4 roster maps each
  ``ADAPTERS`` factory to its §8 row and page, and ``conformance_tested`` on
  the roster gives its depth. From those: the ``framework-status`` table and
  the ``framework-status-summary`` sentences of ``README.md``, the summary in
  ``ARCHITECTURE.md``, the status table and card badges of
  ``website/content/frameworks/index.mdx``, the frontmatter ``status:`` of
  every ``frameworks/*.mdx`` page, the framework row of ``quickstart.mdx`` and
  the framework card badge of ``feature-overview.mdx``. A §8 status with no
  docs label, a factory with no roster row, or a page with no factory fails.
* Framework version compatibility (#1068): ``docs/framework-support.md`` and
  ``website/content/frameworks/compatibility.mdx``, written whole. Per adapter
  package: the floor from its extra in ``pyproject.toml``, the version the PR
  ``adapter-contract`` leg tests from its lock in ``python/constraints/``, and
  the open ``docs/verified-apis.md`` §8.1 rows that name it. An unknown §8.1
  status, a requirement with no ``>=`` floor, or a missing lock or pin fails.

A region sits between two marker comments, ``BEGIN GENERATED: <name>`` and
``END GENERATED: <name>``; everything between them is replaced.
Frontmatter, card badges and single table rows are rewritten in place.

    python scripts/generate_docs.py           # rewrite every target
    python scripts/generate_docs.py --check   # exit 1 and list stale targets

A target whose optional dependency is not installed is skipped and named.
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
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

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

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
    from donkey_kit.core import _verify, config

    defaults = {f.name: _default_text(f.default) for f in dataclasses.fields(config.DonkeyConfig)}
    # ``None`` on a header field means "send the built-in name", not "unset".
    defaults.update((key, f"`{name}`") for key, name in config._HEADER_KEYS)  # noqa: SLF001
    defaults["base_url"] = "from `region`"
    # The ``as_user()`` header (docs/verified-apis.md §13).
    defaults["user_token_header"] = f"`{_verify.USER_TOKEN_HEADER}`"
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
        if getattr(param, "secondary_opts", None):
            flags += " / " + ", ".join(f"`{opt}`" for opt in param.secondary_opts)
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


# --- framework verification status (#1081) ----------------------------------------

LEDGER = REPO / "docs" / "verified-apis.md"
_LEDGER_REF = "docs/verified-apis.md"
FRAMEWORK_PAGES = REPO / "website" / "content" / "frameworks"

#: The ledger's §8 Status values a docs label is derived from, by prefix. Any
#: other value is refused rather than mapped: a new status needs a human to
#: decide what the docs may claim for it (§0.3).
_LEDGER_STATUSES: tuple[tuple[str, str], ...] = (
    ("VERIFIED (LIVE)", "live-verified"),
    ("VERIFIED (CLI)", "live-verified"),
    # Read from the gateway policy's source, never observed live (ADR 0016): not `live`.
    ("VERIFIED (POLICY SOURCE)", "policy-source"),
    ("UNVERIFIED (signature confirmed", "signature-confirmed"),
)

#: How each docs status reads: the inline badge text, its tone and the prose phrase.
_LABELS = {
    "conformance-tested": ("Conformance-tested", "accent", "conformance-tested"),
    "live-verified": ("Live-verified", "live", "live-verified"),
    "policy-source": ("Policy-source", "neutral", "policy-source"),
    "signature-confirmed": ("Signature-confirmed", "neutral", "signature-confirmed offline"),
}
_PAGE_STATUS = {
    "live-verified": "live",
    "policy-source": "offline-verified",
    "signature-confirmed": "offline-verified",
}
#: The page badge of each frontmatter status (``STATUSES`` in ``website/lib/status.mjs``).
_PAGE_BADGE = {"live": ("Live", "live"), "offline-verified": ("Offline-verified", "neutral")}
#: Strongest first. A page that covers several factories is rated by its weakest.
_VERIFICATION_ORDER = ("live-verified", "policy-source", "signature-confirmed")

_PIPE = re.compile(r"(?<!\\)\|")
_FACTORY_CELL = re.compile(r"^`donkey\.(\w+)\.(\w+)\(\)`$")
_ROUTE_CELL = re.compile(r"^`(/frameworks/[\w-]+)`$")


def _table_rows(lines: Iterable[str]) -> list[list[str]]:
    """The body rows of the first Markdown table in ``lines``, as stripped cells."""
    rows: list[list[str]] = []
    started = False
    for line in lines:
        if not line.startswith("|"):
            if started:
                break
            continue
        started = True
        cells = [c.strip() for c in _PIPE.split(line.strip())[1:-1]]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append(cells)
    return rows[1:]  # drop the header row


def ledger_framework_statuses(ledger: str) -> dict[str, str]:
    """The §8 table of the ledger as ``{Framework column: Status column}``."""
    lines = ledger.splitlines()
    start = [i for i, line in enumerate(lines) if line.startswith("## 8. ")]
    if len(start) != 1:
        raise GenerationError(f"{_LEDGER_REF}: expected one '## 8. ' heading")
    out: dict[str, str] = {}
    for cells in _table_rows(lines[start[0] + 1 :]):
        if len(cells) < 3:
            raise GenerationError(f"{_LEDGER_REF} §8: a row has fewer than 3 cells: {cells}")
        name, status = cells[0], cells[2]
        if name in out:
            raise GenerationError(f"{_LEDGER_REF} §8: row {name!r} appears twice")
        out[name] = status
    return out


def verification_of(status: str) -> str:
    """The docs label for one §8 Status value; refuses one it does not know."""
    for prefix, label in _LEDGER_STATUSES:
        if status.startswith(prefix):
            return label
    raise GenerationError(
        f"{_LEDGER_REF} §8: no docs label for status {status!r}; extend _LEDGER_STATUSES "
        f"in {_SCRIPT} and the §8.4 mapping table together"
    )


@dataclass(frozen=True)
class FactoryStatus:
    """One SDK factory's status, from the ledger and the adapter roster."""

    framework: str
    attr: str
    factory: str
    route: str
    ledger_row: str
    #: ``live-verified``, ``policy-source`` or ``signature-confirmed``, from the §8 Status column.
    verification: str
    #: ``AdapterSpec.conformance_tested`` of the factory's adapter.
    conformance_tested: bool
    #: Whether this is the adapter's default factory (the first in ``factories``).
    default: bool
    #: How many factories the adapter declares.
    siblings: int

    @property
    def call(self) -> str:
        return f"donkey.{self.attr}.{self.factory}()"

    @property
    def headline(self) -> str:
        """The badge a card or list shows: depth first, then verification."""
        return "conformance-tested" if self.conformance_tested else self.verification

    @property
    def short_name(self) -> str:
        """The framework, plus the factory when the adapter has more than one."""
        if self.siblings > 1:
            return f"{self.framework} `{self.factory}()`"
        return self.framework


def framework_statuses(ledger: str | None = None) -> list[FactoryStatus]:
    """Every SDK factory's status, in roster order (``docs/verified-apis.md`` §8.4).

    The roster maps each factory to its §8 row and docs page; the status is
    that row's Status column, and the depth is the adapter's
    ``conformance_tested``. Raises :class:`GenerationError` when the roster and
    ``ADAPTERS`` list different factories, or a row names a §8 row or page that
    does not exist.
    """
    from donkey_kit import integrations

    text = LEDGER.read_text(encoding="utf-8") if ledger is None else ledger
    statuses = ledger_framework_statuses(text)
    lines = text.splitlines()
    begin = [i for i, line in enumerate(lines) if "BEGIN ROSTER: framework-status" in line]
    end = [i for i, line in enumerate(lines) if "END ROSTER: framework-status" in line]
    if len(begin) != 1 or len(end) != 1 or end[0] < begin[0]:
        raise GenerationError(f"{_LEDGER_REF}: expected one framework-status roster (§8.4)")

    factories: dict[tuple[str, str], tuple[Any, bool, int]] = {}
    for spec in integrations.ADAPTERS.values():
        module = importlib.import_module(spec.module, integrations.__name__)
        names = list(getattr(module, spec.cls).factories)
        for index, name in enumerate(names):
            factories[(spec.attr, name)] = (spec, index == 0, len(names))

    out: list[FactoryStatus] = []
    for cells in _table_rows(lines[begin[0] + 1 : end[0]]):
        if len(cells) != 4:
            raise GenerationError(f"{_LEDGER_REF} §8.4: a roster row needs 4 cells: {cells}")
        framework, call, row, route_cell = cells
        call_match = _FACTORY_CELL.match(call)
        route_match = _ROUTE_CELL.match(route_cell)
        if call_match is None or route_match is None:
            raise GenerationError(f"{_LEDGER_REF} §8.4: unreadable roster row {cells}")
        key = (call_match.group(1), call_match.group(2))
        if key not in factories:
            raise GenerationError(f"{_LEDGER_REF} §8.4: {call} is not a factory in ADAPTERS")
        if row not in statuses:
            raise GenerationError(f"{_LEDGER_REF} §8.4: {call} names no §8 row {row!r}")
        route = route_match.group(1)
        if not (REPO / "website" / "content" / f"{route.lstrip('/')}.mdx").is_file():
            raise GenerationError(f"{_LEDGER_REF} §8.4: {call} names a missing page {route}")
        spec, default, siblings = factories[key]
        out.append(
            FactoryStatus(
                framework=framework,
                attr=key[0],
                factory=key[1],
                route=route,
                ledger_row=row,
                verification=verification_of(statuses[row]),
                conformance_tested=spec.conformance_tested,
                default=default,
                siblings=siblings,
            )
        )
    seen = [(s.attr, s.factory) for s in out]
    if len(seen) != len(set(seen)):
        raise GenerationError(f"{_LEDGER_REF} §8.4: a factory is listed twice")
    missing = sorted(f"donkey.{a}.{f}()" for a, f in set(factories) - set(seen))
    if missing:
        raise GenerationError(f"{_LEDGER_REF} §8.4: no roster row for {', '.join(missing)}")
    for attr in {s.attr for s in out}:
        rows = [s for s in out if s.attr == attr]
        if len({(s.framework, s.route) for s in rows}) != 1:
            raise GenerationError(
                f"{_LEDGER_REF} §8.4: donkey.{attr} factories name different frameworks or pages"
            )
    return out


def _weakest(verifications: Iterable[str]) -> str:
    return max(verifications, key=_VERIFICATION_ORDER.index)


def page_statuses(rows: list[FactoryStatus]) -> dict[str, str]:
    """Each framework page's frontmatter ``status:``, by route.

    A framework page is rated by its adapter's default factory, the surface
    its main code uses; the ``/frameworks`` overview by the weakest factory.
    """
    out = {s.route: _PAGE_STATUS[s.verification] for s in rows if s.default}
    out["/frameworks"] = _PAGE_STATUS[_weakest(s.verification for s in rows)]
    return out


def _badge(label: str) -> str:
    text, tone, _ = _LABELS[label]
    return f'<Badge tone="{tone}">{text}</Badge>'


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def framework_status_table(*, mdx: bool) -> str:
    """One row per SDK factory: its depth and its ledger status."""
    rows = framework_statuses()
    ledger_link = (
        f"[ledger §8]({REPO_URL}/blob/{DOCS_REF}/{_LEDGER_REF})"
        if mdx
        else f"[`{_LEDGER_REF}` §8]({_LEDGER_REF})"
    )

    def label(name: str) -> str:
        return _badge(name) if mdx else f"**{_LABELS[name][0]}**"

    body = [
        (
            s.framework if not mdx else f"[{s.framework}]({s.route})",
            f"`{s.call}`",
            label("conformance-tested") if s.conformance_tested else "`connection_kwargs()`",
            label(s.verification),
        )
        for s in rows
    ]
    intro = (
        f"Each factory's status is its constructor row in the {ledger_link}; its\n"
        "depth is the adapter roster's `conformance_tested`.\n\n"
    )
    return intro + _table(("Framework", "Factory", "Depth", "Ledger status"), body)


def framework_status_summary() -> str:
    """The per-factory statuses as two sentences of prose."""
    rows = framework_statuses()
    deep = list(dict.fromkeys(s.framework for s in rows if s.conformance_tested))
    if deep:
        verb = "is" if len(deep) == 1 else "are"
        first = f"{_join(deep)} {verb} conformance-tested against the simulator in CI."
    else:
        first = "No adapter is conformance-tested against the simulator."
    groups = {v: [s for s in rows if s.verification == v] for v in _VERIFICATION_ORDER}
    groups = {v: g for v, g in groups.items() if g}
    common = max(groups, key=lambda v: len(groups[v]))
    others = [v for v in groups if v != common]
    second = (
        f"Each adapter factory's constructor row in §8 of the ledger reads **{_LABELS[common][2]}**"
    )
    if others:
        parts = []
        for v in others:
            names = [
                f"{s.framework}'s `{s.factory}()`" if s.siblings > 1 else s.framework
                for s in groups[v]
            ]
            verb = "which is" if len(names) == 1 else "which are"
            parts.append(f"{_join(names)}, {verb} **{_LABELS[v][2]}**")
        second += ", except " + "; and ".join(parts)
    return f"{first}\n{second}."


def _frontmatter_status(text: str, status: str, where: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise GenerationError(f"{where}: no frontmatter")
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            break
        if line.startswith("status:"):
            lines[i] = f"status: {status}\n"
            return "".join(lines)
    raise GenerationError(f"{where}: no 'status:' line in the frontmatter")


_CARD = re.compile(r'(<Card\b[^>]*?\bhref="(?P<href>[^"]+)"[^>]*>)')
_CARD_BADGE = re.compile(r'\bbadge="[^"]*"(\s+)badgeTone="[^"]*"')


def _card_badges(text: str, badges: dict[str, tuple[str, str]], where: str) -> str:
    """``text`` with the ``badge``/``badgeTone`` of each ``<Card href=...>`` in ``badges``."""
    seen: list[str] = []

    def card(match: re.Match[str]) -> str:
        href = match.group("href")
        if href not in badges:
            return match.group(0)
        seen.append(href)
        badge, tone = badges[href]
        tag, count = _CARD_BADGE.subn(
            lambda m: f'badge="{badge}"{m.group(1)}badgeTone="{tone}"', match.group(0)
        )
        if count != 1:
            raise GenerationError(f'{where}: the {href} card needs badge="…" badgeTone="…"')
        return tag

    out = _CARD.sub(card, text)
    for href in badges:
        if seen.count(href) != 1:
            raise GenerationError(f'{where}: expected one <Card href="{href}">')
    return out


def _replace_row(text: str, prefix: str, row: str, where: str) -> str:
    lines = text.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if len(hits) != 1:
        raise GenerationError(f"{where}: expected one table row starting {prefix!r}")
    lines[hits[0]] = row + "\n"
    return "".join(lines)


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO))


def framework_page(path: Path) -> str:
    """A framework page with its frontmatter ``status:`` from its factories."""
    route = f"/frameworks/{path.stem}" if path.stem != "index" else "/frameworks"
    statuses = page_statuses(framework_statuses())
    if route not in statuses:
        raise GenerationError(
            f"{_rel(path)}: no factory in {_LEDGER_REF} §8.4 names {route}; add its roster row"
        )
    return _frontmatter_status(path.read_text(encoding="utf-8"), statuses[route], _rel(path))


def frameworks_index() -> str:
    """``frameworks/index.mdx``: its status, the cards' badges and the status table."""
    path = FRAMEWORK_PAGES / "index.mdx"
    rows = framework_statuses()
    badges = {}
    for s in rows:
        if s.default:
            text, tone, _ = _LABELS[s.headline]
            badges[s.route] = (text, tone)
    text = replace_region(
        framework_page(path), "framework-status", framework_status_table(mdx=True)
    )
    return _card_badges(text, badges, _rel(path))


_QUICKSTART_ROW = "| Framework adapters for "


def quickstart() -> str:
    """``quickstart.mdx`` with its framework-adapters row from the factories' statuses."""
    path = REPO / "website" / "content" / "quickstart.mdx"
    rows = framework_statuses()
    adapters = len({s.attr for s in rows})
    groups: list[tuple[str, list[FactoryStatus]]] = []
    for label in ("conformance-tested", *_VERIFICATION_ORDER):
        members = [s for s in rows if s.headline == label]
        if members:
            groups.append((label, members))
    largest = max(range(len(groups)), key=lambda i: len(groups[i][1]))
    parts = []
    for index, (label, members) in enumerate(groups):
        if len(groups) == 1:
            names = "all of them"
        elif index == largest:
            names = "the rest"
        else:
            names = _join(list(dict.fromkeys(s.short_name for s in members)))
        parts.append(f"{_badge(label)} {names}")
    if largest != len(groups) - 1:
        parts.append(parts.pop(largest))
    row = (
        f"{_QUICKSTART_ROW}{adapters} frameworks | {' · '.join(parts)} | "
        "[Frameworks](/frameworks#supported-frameworks) |"
    )
    return _replace_row(path.read_text(encoding="utf-8"), _QUICKSTART_ROW, row, _rel(path))


def feature_overview() -> str:
    """``feature-overview.mdx`` with its framework card badge from the overview page's status."""
    path = REPO / "website" / "content" / "feature-overview.mdx"
    status = page_statuses(framework_statuses())["/frameworks"]
    text = path.read_text(encoding="utf-8")
    return _card_badges(text, {"/frameworks": _PAGE_BADGE[status]}, _rel(path))


def _with_region(path: Path, region: str, render: Callable[[], str]) -> Callable[[], str]:
    def rendered() -> str:
        return replace_region(path.read_text(encoding="utf-8"), region, render())

    return rendered


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


#: Signatures are not runnable Python, so the snippet check skips them (#797).
_SIGNATURE_FENCE = "```python nocheck"


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
    lines = ["", f"### {heading}", "", _SIGNATURE_FENCE, code, "```", ""]
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
            _SIGNATURE_FENCE,
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
    # An SDK instance with methods, e.g. ``classify`` (#210); typing aliases
    # are callable too, so the test is the instance's own type.
    if type(obj).__module__.startswith("donkey_kit.") and callable(obj):
        return "callable object"
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
        "status: live",
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


# --- framework version compatibility (#1068) ---------------------------------------

PYPROJECT = REPO / "python" / "pyproject.toml"
CONSTRAINTS = REPO / "python" / "constraints"
FRAMEWORK_SUPPORT = REPO / "docs" / "framework-support.md"
COMPATIBILITY_PAGE = FRAMEWORK_PAGES / "compatibility.mdx"
#: The Python the PR ``adapter-contract`` legs run on, so the lock they install.
CONTRACT_PYTHON = "3.12"
_NIGHTLY = f"{REPO_URL}/actions/workflows/nightly-matrix.yml"
_NIGHTLY_ISSUES = f"{REPO_URL}/issues?q=is%3Aissue%20is%3Aopen%20label%3Anightly-failure"
_SECTION_81 = "#81-known-upstream-incompatibilities-floors-not-ceilings"
_FLOOR = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?>=([^\s,;]+)$")
_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$")
_PACKAGE_SPAN = re.compile(r"`([A-Za-z][A-Za-z0-9._-]*)")

#: The ledger's §8.1 Status values, by prefix, and whether a row with that status
#: is a current incompatibility. Any other value is refused, as for §8 above.
_INCOMPATIBILITY_STATUSES: tuple[tuple[str, bool], ...] = (("OPEN", True), ("MITIGATED", False))


def _canonical(name: str) -> str:
    """A distribution name in its PEP 503 normal form."""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass(frozen=True)
class Incompatibility:
    """One row of the ledger's §8.1 table."""

    dependency: str
    breaks_at: str
    status: str
    #: Normalised names of the packages its Dependency and Breaks-at cells name.
    packages: frozenset[str]

    @property
    def current(self) -> bool:
        """Whether the row still breaks the SDK (it is not mitigated)."""
        for prefix, current in _INCOMPATIBILITY_STATUSES:
            if self.status.startswith(prefix):
                return current
        raise GenerationError(
            f"{_LEDGER_REF} §8.1: unknown status {self.status!r}; extend "
            f"_INCOMPATIBILITY_STATUSES in {_SCRIPT}"
        )


def incompatibilities(ledger: str) -> list[Incompatibility]:
    """The ledger's §8.1 rows, in order."""
    lines = ledger.splitlines()
    start = [i for i, line in enumerate(lines) if line.startswith("### 8.1 ")]
    if len(start) != 1:
        raise GenerationError(f"{_LEDGER_REF}: expected one '### 8.1 ' heading")
    out: list[Incompatibility] = []
    for cells in _table_rows(lines[start[0] + 1 :]):
        if len(cells) != 5:
            raise GenerationError(f"{_LEDGER_REF} §8.1: a row needs 5 cells: {cells[:2]}")
        dependency, breaks_at, _, status, _ = cells
        names = _PACKAGE_SPAN.findall(dependency + " " + breaks_at)
        row = Incompatibility(dependency, breaks_at, status, frozenset(map(_canonical, names)))
        row.current  # noqa: B018  (refuses an unknown status here, not at render time)
        out.append(row)
    return out


def contract_lock(extra: str) -> dict[str, str]:
    """The exact pins the PR ``adapter-contract`` leg for ``extra`` installs."""
    path = CONSTRAINTS / f"adapter-contract-{extra}-py{CONTRACT_PYTHON}.txt"
    if not path.is_file():
        raise GenerationError(f"{path.name} is missing; run: python scripts/compile_constraints.py")
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _PIN.match(line.strip())
        if match:
            pins[_canonical(match.group(1))] = match.group(2)
    return pins


@dataclass(frozen=True)
class PackageVersions:
    """One package of a framework extra: its floor and the version PR CI tests."""

    name: str
    minimum: str
    tested: str


@dataclass(frozen=True)
class FrameworkVersions:
    """The versions of one adapter's framework that the SDK supports and tests."""

    framework: str
    route: str
    extra: str
    conformance_tested: bool
    packages: tuple[PackageVersions, ...]
    #: The current §8.1 rows that name one of ``packages``.
    incompatible: tuple[Incompatibility, ...]


def framework_versions(ledger: str | None = None) -> list[FrameworkVersions]:
    """Every adapter's supported and tested versions, in roster order.

    The minimum is the ``>=`` floor of each requirement in the adapter's extra
    (``pyproject.toml``), the tested version is that package's pin in the
    extra's ``adapter-contract`` lock, and the known incompatibilities are the
    §8.1 rows that are not mitigated. The framework name, page and depth come
    from :func:`framework_statuses`. Raises :class:`GenerationError` when an
    extra, a lock or a pin is missing, or a requirement has no plain floor.
    """
    from donkey_kit import integrations

    text = LEDGER.read_text(encoding="utf-8") if ledger is None else ledger
    pages = {s.attr: (s.framework, s.route) for s in framework_statuses(text) if s.default}
    current = [row for row in incompatibilities(text) if row.current]
    extras = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    out: list[FrameworkVersions] = []
    for spec in integrations.ADAPTERS.values():
        if spec.extra not in extras:
            raise GenerationError(f"pyproject.toml has no extra {spec.extra!r}")
        pins = contract_lock(spec.extra)
        packages: list[PackageVersions] = []
        for raw in extras[spec.extra]:
            match = _FLOOR.match(raw.replace(" ", ""))
            if match is None:
                raise GenerationError(
                    f"pyproject.toml [{spec.extra}]: {raw!r} is not a plain floor (name>=version)"
                )
            name, minimum = match.groups()
            tested = pins.get(_canonical(name))
            if tested is None:
                raise GenerationError(
                    f"adapter-contract-{spec.extra}-py{CONTRACT_PYTHON}.txt pins no {name}; "
                    "run: python scripts/compile_constraints.py"
                )
            packages.append(PackageVersions(name, minimum, tested))
        names = {_canonical(p.name) for p in packages}
        framework, route = pages[spec.attr]
        out.append(
            FrameworkVersions(
                framework=framework,
                route=route,
                extra=spec.extra,
                conformance_tested=spec.conformance_tested,
                packages=tuple(packages),
                incompatible=tuple(row for row in current if row.packages & names),
            )
        )
    return out


def framework_versions_table(*, mdx: bool) -> str:
    """One row per package of each framework extra: minimum, tested, known incompatible."""

    def ledger_text(text: str) -> str:
        return _outside_code(text, _escape_mdx) if mdx else text

    rows: list[tuple[str, ...]] = []
    for fw in framework_versions():
        if fw.conformance_tested:
            tier = _badge("conformance-tested") if mdx else "**Conformance-tested**"
        else:
            tier = "`connection_kwargs()`"
        known = "; ".join(ledger_text(row.breaks_at) for row in fw.incompatible) or "None known"
        for index, package in enumerate(fw.packages):
            first = index == 0
            rows.append(
                (
                    (f"[{fw.framework}]({fw.route})" if mdx else fw.framework) if first else "",
                    tier if first else "",
                    f"`[{fw.extra}]`" if first else "",
                    f"`{package.name}`",
                    package.minimum,
                    package.tested,
                    known if first else "",
                )
            )
    header = (
        "Framework",
        "Tier",
        "Extra",
        "Package",
        "Minimum supported",
        "Tested on every PR",
        "Known incompatible",
    )
    return _table(header, rows)


def _support_policy(*, ledger_link: str) -> str:
    return (
        "**Supported** means any release at or above the minimum, except a version\n"
        "listed as known incompatible. The extras set no upper bounds, so a fresh\n"
        "install takes each framework's newest release.\n\n"
        "- **Tested on every PR** is the exact version every pull request installs\n"
        f"  and tests the adapter against, on Python {CONTRACT_PYTHON}.\n"
        "- **Minimum supported** is tested every night: CI installs each extra at\n"
        "  its minimum versions on the oldest supported Python and runs the tests.\n"
        "- **Newer releases** are tested every night: CI installs each framework's\n"
        "  newest release and runs the same tests. A failing night opens a public\n"
        f"  [`nightly-failure` issue]({_NIGHTLY_ISSUES}).\n"
        "- **Known incompatible** lists the open rows of the\n"
        f"  {ledger_link}. A break is fixed in the SDK or recorded there, never\n"
        "  pinned away.\n\n"
        f"[![Nightly matrix]({_NIGHTLY}/badge.svg)]({_NIGHTLY})"
    )


def framework_support_md() -> str:
    """``docs/framework-support.md``: the contributor reference, generated whole."""
    policy = _support_policy(
        ledger_link=f"[`verified-apis.md` §8.1](verified-apis.md{_SECTION_81})"
    )
    recorded = _table(
        ("Dependency", "Breaks at", "Status"),
        ((row.dependency, row.breaks_at, row.status) for row in incompatibilities(_ledger())),
    )
    lines = [
        "# Framework support",
        "",
        "<!-- GENERATED FILE: do not edit by hand. Refresh with",
        "     `python scripts/generate_docs.py` from python/ (#1068). -->",
        "",
        "Which versions of each agent framework `donkey-kit` supports, and how CI",
        "tests them. The site's [version compatibility]"
        f"({SITE}/frameworks/compatibility) page shows",
        "the same table to SDK users. Both are written from the sources below by",
        "`python/scripts/generate_docs.py`, and `tests/unit/test_generated_docs.py`",
        "fails when either is stale.",
        "",
        "## Supported versions",
        "",
        policy,
        "",
        framework_versions_table(mdx=False),
        "",
        "## Where each column comes from",
        "",
        _table(
            ("Column", "Source"),
            (
                ("Tier", "`conformance_tested` on the adapter's `ADAPTERS` entry"),
                ("Extra, Package", "the adapter's extra in `python/pyproject.toml`"),
                (
                    "Minimum supported",
                    "the `>=` floor of each requirement in that extra (ADR 0007 rule 1). "
                    "[`verified-apis.md` §8.3](verified-apis.md#83-extras-floors-lowest-"
                    "verified-versions) records why each floor is where it is.",
                ),
                (
                    "Tested on every PR",
                    "the package's pin in `python/constraints/adapter-contract-<extra>-"
                    f"py{CONTRACT_PYTHON}.txt` (ADR 0007 rule 3)",
                ),
                (
                    "Known incompatible",
                    f"the [`verified-apis.md` §8.1](verified-apis.md{_SECTION_81}) rows "
                    "that name one of the extra's packages and are not `MITIGATED`",
                ),
            ),
        ),
        "",
        "## How CI tests each framework",
        "",
        _table(
            ("Job", "Workflow", "When", "What it installs"),
            (
                (
                    "`adapter-contract (<extra>)`",
                    "`ci.yml`",
                    "every PR",
                    f"the extra at its lock, on Python {CONTRACT_PYTHON}: the *Tested* column",
                ),
                (
                    "`framework-legs (<framework>)`",
                    "`nightly-matrix.yml`",
                    "daily",
                    "the extra resolved fresh: each framework's newest release",
                ),
                (
                    "`lowest-direct (<extra>)`",
                    "`nightly-matrix.yml`",
                    "daily",
                    "the extra at its floors (`uv --resolution lowest-direct`), on the "
                    "oldest supported Python: the *Minimum* column. "
                    "`scripts/check_floors.py` fails a floor the resolver could not reach.",
                ),
                (
                    "`co-installability`",
                    "`nightly-matrix.yml`",
                    "daily",
                    "every pair of extras; see [`co-installability.md`](co-installability.md)",
                ),
            ),
        ),
        "",
        "A red nightly opens or updates one issue labelled `nightly-failure`.",
        "After a green nightly on `develop`, `lock-refresh.yml` recompiles the lock",
        "and opens one PR that moves it, with these pages regenerated",
        "([`ci.md`](ci.md#lock-refreshyml-the-lock-bump)).",
        "",
        "## Changing a version",
        "",
        "A PR that moves a lock, an extra's floor or a §8.1 row regenerates both",
        "pages in the same commit. From `python/`:",
        "",
        "```bash",
        "python scripts/compile_constraints.py   # only when the lock moves",
        "python scripts/generate_docs.py         # this page and the site page",
        "(cd ../website && npm run generate:llms)   # the site's llms outputs",
        "```",
        "",
        "Never add an upper bound to make a version pass (`§8.4`): fix the",
        "adapter, or record the break as a §8.1 row.",
        "",
        "## Upstream changes on record",
        "",
        f"Every [`verified-apis.md` §8.1](verified-apis.md{_SECTION_81}) row,",
        "including the mitigated ones, which the SDK already handles:",
        "",
        recorded,
        "",
    ]
    return "\n".join(lines)


def compatibility_page() -> str:
    """``frameworks/compatibility.mdx``: the user-facing page, generated whole."""
    status = page_statuses(framework_statuses())["/frameworks"]
    ledger = f"{REPO_URL}/blob/{DOCS_REF}/{_LEDGER_REF}"
    reference = f"{REPO_URL}/blob/{DOCS_REF}/{_rel(FRAMEWORK_SUPPORT)}"
    coinstall = f"{REPO_URL}/blob/{DOCS_REF}/docs/co-installability.md"
    lines = [
        "---",
        "description: Which versions of each agent framework donkey-kit supports — the "
        "minimum, the version every PR tests, and known incompatibilities.",
        f"status: {status}",
        "---",
        "",
        "import { Badge } from '../../components'",
        "",
        "{/* Generated by python/scripts/generate_docs.py from pyproject.toml, the PR CI "
        "lock and the",
        "verification ledger. Do not edit by hand: change the source, then run the script. */}",
        "",
        "# Version compatibility",
        "",
        "Which versions of each framework `donkey-kit` supports. Install a framework",
        'with its extra, for example `pip install "donkey-kit[langgraph]"`; the extra',
        "declares the minimum versions below.",
        "",
        _support_policy(ledger_link=f"[known upstream incompatibilities]({ledger}{_SECTION_81})"),
        "",
        framework_versions_table(mdx=True),
        "",
        "## Installing several frameworks together",
        "",
        "Each extra installs on its own and next to `[all]`. Whether two framework",
        "extras resolve in one environment is re-checked every night; see the",
        f"[co-installability table]({coinstall}).",
        "",
        "## More detail",
        "",
        f"The [framework support reference]({reference}) in the repository has the",
        "same table, where each column comes from, which CI job tests each version,",
        "and the full list of recorded upstream changes, including those the SDK",
        "already handles.",
        "",
    ]
    return "\n".join(lines)


def _ledger() -> str:
    return LEDGER.read_text(encoding="utf-8")


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


_README = REPO / "README.md"
_ARCHITECTURE = REPO / "ARCHITECTURE.md"


def _framework_page_targets() -> tuple[Target, ...]:
    """One target per framework page; a page with no roster row fails its render."""
    if not FRAMEWORK_PAGES.is_dir():
        return ()
    return tuple(
        Target(
            f"framework-page-status-{path.stem}",
            path,
            None,
            functools.partial(framework_page, path),
        )
        for path in sorted(FRAMEWORK_PAGES.glob("*.mdx"))
        if path.stem not in ("index", COMPATIBILITY_PAGE.stem)
    )


TARGETS: tuple[Target, ...] = (
    # The README targets come first: pypi-readme is written from the result.
    Target(
        "framework-status-readme",
        _README,
        None,
        _with_region(_README, "framework-status", lambda: framework_status_table(mdx=False)),
    ),
    Target(
        "framework-status-summary-readme",
        _README,
        None,
        _with_region(_README, "framework-status-summary", framework_status_summary),
    ),
    Target(
        "framework-status-summary-architecture",
        _ARCHITECTURE,
        None,
        _with_region(_ARCHITECTURE, "framework-status-summary", framework_status_summary),
    ),
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
    Target("framework-status-index", FRAMEWORK_PAGES / "index.mdx", None, frameworks_index),
    *_framework_page_targets(),
    Target(
        "framework-status-quickstart",
        REPO / "website" / "content" / "quickstart.mdx",
        None,
        quickstart,
    ),
    Target(
        "framework-status-feature-overview",
        REPO / "website" / "content" / "feature-overview.mdx",
        None,
        feature_overview,
    ),
    Target("framework-support", FRAMEWORK_SUPPORT, None, framework_support_md),
    Target("framework-compatibility", COMPATIBILITY_PAGE, None, compatibility_page),
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
