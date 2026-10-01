"""Reject verification-status claims in ``src/`` outside ``core/_verify.py`` (#718).

``docs/verified-apis.md`` is the source of truth for what is verified, when, and
how. Docstrings and comments that copy that status drift from it, and users read
the stale copy in their IDE. So code under ``src/`` describes behaviour and cites
``docs/verified-apis.md §N``. Only ``core/_verify.py``, which holds the values
themselves, may record a status or a date.

Rejected outside ``_verify.py``:

* an ISO date (``2026-09-22``) — a verification date belongs in the ledger;
* ``LIVE-VERIFIED`` / ``live-verified`` / ``verified live``;
* a ledger status label: ``VERIFIED (LIVE)``, ``VERIFIED (plugin)``,
  ``VERIFIED-NEGATIVE``, ``VERIFIED-SHAPE-ONLY``, … and ``(LIVE, …)``.

``UNVERIFIED`` stays allowed: it marks a guard that is still in place.

    python scripts/check_verification_claims.py [ROOT]

``ROOT`` defaults to this checkout's ``src/donkey_kit``. Exits 1 and lists every
match when any is found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[1] / "src" / "donkey_kit"
_ALLOWED = Path("core") / "_verify.py"

_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dated claim", re.compile(r"\b20\d\d-\d\d-\d\d\b")),
    ("LIVE-verified claim", re.compile(r"\blive[- ]verified\b|\bverified[- ]live\b", re.I)),
    ("ledger status label", re.compile(r"\bVERIFIED(?: \(|-NEGATIVE\b|-SHAPE-ONLY\b)|\(LIVE\b")),
)


def find_claims(root: Path) -> list[str]:
    """Every claim under ``root`` as ``path:line: kind: text``, in file order."""
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel == _ALLOWED:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for kind, pattern in _CLAIMS:
                if pattern.search(line):
                    found.append(f"{rel}:{lineno}: {kind}: {line.strip()}")
                    break
    return found


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else _DEFAULT_ROOT
    found = find_claims(root)
    if not found:
        return 0
    print(
        f"{len(found)} verification claim(s) outside core/_verify.py. Describe the "
        "behaviour and cite docs/verified-apis.md §N instead:",
        file=sys.stderr,
    )
    for entry in found:
        print(f"  {entry}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
