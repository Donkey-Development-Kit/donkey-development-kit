"""Check the website's status badges against the ledger they claim to restate (#1482).

A ``<Badge>`` on a page is a verification claim: ``Live`` says the contract it
rests on was observed through a real gateway. ``docs/verified-apis.md`` is the
source of truth for that (§0.3), so every badge that claims something names the
ledger row it rests on, in a ``ledger`` prop::

    <Badge tone="live" ledger="§2 Streaming support">Live</Badge>
    <Badge tone="live" ledger="§2 Token accounting; §3 Gateway identity">Live</Badge>

A row is ``§<section> <first cell>``, the first cell as the ledger writes it,
or the start of it when that names one row only. Several rows are joined with
``;``, and each must support the badge.
The check fails when:

* a badge's claim is stronger than its row's Status column (``Live`` needs
  ``VERIFIED (LIVE)`` or ``VERIFIED (CLI)``, as ``website/lib/status.mjs`` and
  ``_LEDGER_STATUSES`` in ``generate_docs.py`` read it);
* a badge that claims something cites no row, and no :data:`ALLOWED` entry
  starts its line;
* a cited row does not exist, or the citation matches more than one;
* a badge's text is one this script does not know, or an :data:`ALLOWED` entry
  no longer matches a line.

``Roadmap`` and ``Not supported`` claim nothing and need no row. Badges that
``generate_docs.py`` writes from §8 are checked there, so a ``GENERATED``
region is skipped here, as are the pages and rows it writes (in :data:`ALLOWED`).

    python scripts/check_doc_badges.py [CONTENT_DIR LEDGER]

Another tree is checked with no allowlist.

Exits 1 and lists every problem when any is found.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_DEFAULT_CONTENT = REPO / "website" / "content"
_DEFAULT_LEDGER = REPO / "docs" / "verified-apis.md"

#: How strong a claim is, weakest first.
NONE, OFFLINE, LOCAL, LIVE = range(4)
_LEVEL_NAMES = ("no claim", "offline", "local round-trip", "live")

#: What each badge text claims. The labels are ``STATUSES`` in
#: ``website/lib/status.mjs`` and ``_LABELS`` in ``generate_docs.py``.
CLAIMS: dict[str, int] = {
    "Live": LIVE,
    "Live-verified": LIVE,
    "Local round-trip": LOCAL,
    "Offline-verified": OFFLINE,
    "Conformance-tested": OFFLINE,
    "Signature-confirmed": OFFLINE,
    "Policy-source": OFFLINE,
    "Roadmap": NONE,
    "Not supported": NONE,
}

#: What each ledger Status supports, by prefix; the first match wins.
_STATUSES: tuple[tuple[str, int], ...] = (
    ("VERIFIED (LIVE)", LIVE),
    ("VERIFIED (CLI)", LIVE),
    ("VERIFIED (local round-trip)", LOCAL),
    ("VERIFIED (local capture)", LOCAL),
    # Read from the gateway policy's source, never observed (ADR 0016).
    ("VERIFIED (POLICY SOURCE)", OFFLINE),
    # Read from the CLI plugin's source (§12), not from a call.
    ("VERIFIED (plugin)", OFFLINE),
    ("OFFLINE-VERIFIED", OFFLINE),
    ("UNVERIFIED (signature confirmed", OFFLINE),
    ("UNVERIFIED", NONE),
    ("UNSUPPORTED", NONE),
    ("PARTIAL", NONE),
)

#: Badges that claim something without a ledger row, by page and the start of
#: their line (stripped), with the reason.
_LEGEND = "the badge legend, which defines the labels"
_LOCAL = "runs in-process with no gateway contract of its own"
_GENERATED = "written by generate_docs.py from §8, and checked there"
ALLOWED: dict[tuple[str, str], str] = {
    ("hitl.mdx", "| Any Python code |"): (
        "the approval runs in the caller's process and has no gateway contract of its own"
    ),
    ("hitl.mdx", '| <Badge tone="live">Live</Badge> |'): _LEGEND,
    ("hitl.mdx", '| <Badge tone="neutral">Local round-trip</Badge> |'): _LEGEND,
    ("hitl.mdx", '| <Badge tone="neutral">Offline-verified</Badge> |'): _LEGEND,
    ("frameworks/index.mdx", '| <Badge tone="accent">Conformance-tested</Badge> |'): _LEGEND,
    ("frameworks/index.mdx", '| <Badge tone="live">Live-verified</Badge> |'): _LEGEND,
    ("frameworks/index.mdx", '| <Badge tone="neutral">Signature-confirmed</Badge> |'): _LEGEND,
    ("roadmap.mdx", '<Badge tone="live">Complete</Badge>'): (
        "the roadmap legend: milestone progress, not verification"
    ),
    ("roadmap.mdx", '<Badge tone="accent">In progress</Badge>'): (
        "the roadmap legend: milestone progress, not verification"
    ),
    ("roadmap.mdx", '<Badge tone="roadmap">Planned</Badge>'): (
        "the roadmap legend: milestone progress, not verification"
    ),
    ("quickstart.mdx", "| Local simulator & `simulate()` |"): _LOCAL,
    ("quickstart.mdx", "| Conformance testing with pytest |"): _LOCAL,
    ("quickstart.mdx", "## Test the refusal branch"): _LOCAL,
    ("quickstart.mdx", "| `donkey scan` "): "static analysis of local code; no gateway contract",
    ("publishing.mdx", "## `donkey scan` "): "static analysis of local code; no gateway contract",
    ("quickstart.mdx", "| Framework adapters for "): _GENERATED,
    ("frameworks/compatibility.mdx", "|"): _GENERATED,
}

_BADGE = re.compile(r"<Badge\b(?P<attrs>[^>]*)>(?P<text>.*?)</Badge>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_HEADING = re.compile(r"^#{2,3} (\d+(?:\.\d+)?)\.? ")
_CITE = re.compile(r"^§(\d+(?:\.\d+)?) (.+)$")


def _cells(line: str) -> list[str]:
    """A table line's cells. A ``|`` inside backticks or escaped is not a separator."""
    cells: list[str] = []
    cell: list[str] = []
    code = escaped = False
    for char in line.strip()[1:]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            code = not code
        elif char == "|" and not code:
            cells.append("".join(cell).strip())
            cell = []
            continue
        cell.append(char)
    return cells


@dataclass(frozen=True)
class Row:
    section: str
    name: str
    status: str


def ledger_rows(ledger: str) -> dict[tuple[str, str], list[Row]]:
    """Every ledger row with a Status column, by ``(section, first cell)``.

    A row is in the numbered section (``## 4.``, ``### 8.5``) above it; an
    unnumbered heading stays in its parent's section.
    """
    out: dict[tuple[str, str], list[Row]] = {}
    section = ""
    header: list[str] | None = None
    for line in ledger.splitlines():
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(1)
        if not line.startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if header is None:
            header = cells
            continue
        if all(set(c) <= set("-: ") for c in cells) or "Status" not in header:
            continue
        index = header.index("Status")
        if section and len(cells) > index:
            row = Row(section, cells[0], cells[index])
            out.setdefault((section, row.name), []).append(row)
    return out


def supports(status: str) -> int | None:
    """The strongest claim a Status supports, or None for one this script does not know."""
    for prefix, level in _STATUSES:
        if status.startswith(prefix):
            return level
    return None


def _lines(path: Path) -> Iterable[tuple[int, str]]:
    """``path``'s lines outside fenced code blocks and ``GENERATED`` regions."""
    fenced = generated = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif "BEGIN GENERATED:" in line:
            generated = True
        elif "END GENERATED:" in line:
            generated = False
        elif not (fenced or generated):
            yield lineno, line


def check(
    content: Path, ledger: str, allowed: dict[tuple[str, str], str] | None = None
) -> list[str]:
    """Every problem with the badges under ``content``, as ``path:line: message``.

    ``allowed`` defaults to :data:`ALLOWED`."""
    allowed = ALLOWED if allowed is None else allowed
    rows = ledger_rows(ledger)
    problems: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in sorted(content.rglob("*.mdx")):
        rel = path.relative_to(content).as_posix()
        for lineno, line in _lines(path):
            for badge in _BADGE.finditer(line):
                where = f"{rel}:{lineno}"
                text = badge.group("text").strip()
                attrs = dict(_ATTR.findall(badge.group("attrs")))
                hits = [k for k in allowed if k[0] == rel and line.strip().startswith(k[1])]
                used.update(hits)
                if hits:
                    continue
                if text not in CLAIMS:
                    problems.append(
                        f"{where}: unknown badge text {text!r}; add it to CLAIMS in "
                        "scripts/check_doc_badges.py"
                    )
                    continue
                claim = CLAIMS[text]
                cite = attrs.get("ledger")
                if cite is None:
                    if claim > NONE:
                        problems.append(
                            f"{where}: the {text!r} badge cites no ledger row; "
                            'add ledger="§N <row>"'
                        )
                    continue
                problems.extend(_check_cite(where, text, claim, cite, rows))
    for page, snippet in sorted(set(allowed) - used):
        problems.append(f"{page}: the ALLOWED entry {snippet!r} matches no badge line")
    return problems


def _check_cite(
    where: str, text: str, claim: int, cite: str, rows: dict[tuple[str, str], list[Row]]
) -> list[str]:
    problems: list[str] = []
    for part in (p.strip() for p in cite.split(";")):
        match = _CITE.match(part)
        if match is None:
            problems.append(f"{where}: unreadable ledger citation {part!r}; write §N <row>")
            continue
        section, name = match.groups()
        found = rows.get((section, name)) or [
            row
            for (s, n), same in rows.items()
            if s == section and n.startswith(name)
            for row in same
        ]
        if len(found) != 1:
            what = "no ledger row" if not found else f"{len(found)} ledger rows; cite more of it"
            problems.append(f"{where}: {part!r} names {what}")
            continue
        status = found[0].status
        level = supports(status)
        if level is None:
            problems.append(
                f"{where}: {part!r} has a Status this script does not rate ({status!r}); "
                "extend _STATUSES in scripts/check_doc_badges.py"
            )
        elif level < claim:
            problems.append(
                f"{where}: the {text!r} badge claims {_LEVEL_NAMES[claim]}, but {part!r} "
                f"reads {status!r} ({_LEVEL_NAMES[level]})"
            )
    return problems


def main(argv: list[str]) -> int:
    if argv:  # another tree: the site's allowlist does not apply to it
        content, ledger, allowed = Path(argv[0]), Path(argv[1]), {}
    else:
        content, ledger, allowed = _DEFAULT_CONTENT, _DEFAULT_LEDGER, ALLOWED
    problems = check(content, ledger.read_text(encoding="utf-8"), allowed)
    if not problems:
        return 0
    print(f"{len(problems)} status badge(s) do not match docs/verified-apis.md:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
