"""Reject tracked docs that link to untracked paths, and committed plan files (#785).

A fresh clone only has what git tracks. A Markdown link into a gitignored path
(``CLAUDE.md``, ``.claude/skills/...``) works on a maintainer's machine, where the
agent config is symlinked in, and is broken for everyone else. Plan content lives
in the GitHub issue, not in committed ``docs/**/plans/`` files (CONTRIBUTING.md,
"The issue is the plan").

Rejected:

* a relative Markdown link (inline ``[text](path)`` or reference ``[id]: path``)
  in a tracked ``*.md`` file whose target is neither a tracked file nor a
  directory holding one;
* any tracked file under a ``plans/`` directory inside ``docs/``.

URLs, ``#anchors``, site routes (``/...``) and links inside fenced code blocks
are not checked.

    python scripts/check_doc_links.py [ROOT]

``ROOT`` defaults to this checkout's repository root; the tracked set comes from
``git ls-files``. Exits 1 and lists every problem when any is found.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]

_INLINE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.I)


def tracked_files(root: Path) -> set[str]:
    """Every path git tracks under ``root``, POSIX-style and relative to it."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return {p for p in out.split("\0") if p}


def _links(text: str) -> Iterable[tuple[int, str]]:
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _INLINE.finditer(line):
            yield lineno, match.group(1)
        ref = _REFERENCE.match(line)
        if ref:
            yield lineno, ref.group(1)


def find_problems(root: Path, tracked: set[str]) -> list[str]:
    """Every problem as ``path:line: kind: target`` (plan files as ``path: ...``)."""
    dirs = {posixpath.dirname(p) for p in tracked}
    known = set(tracked)
    for d in dirs:
        while d:
            known.add(d)
            d = posixpath.dirname(d)

    found: list[str] = []
    for rel in sorted(tracked):
        parts = rel.split("/")
        if parts[0] == "docs" and "plans" in parts[1:-1]:
            found.append(f"{rel}: committed plan file (put the plan in the issue)")
        if not rel.endswith(".md"):
            continue
        path = root / rel
        if not path.is_file():
            continue
        for lineno, target in _links(path.read_text(encoding="utf-8")):
            if _EXTERNAL.match(target):
                continue
            bare = target.split("#", 1)[0].split("?", 1)[0]
            if not bare:
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), bare))
            if resolved not in known:
                found.append(f"{rel}:{lineno}: link to untracked path: {target}")
    return found


def main(argv: list[str]) -> int:
    root = Path(argv[0]) if argv else _DEFAULT_ROOT
    found = find_problems(root, tracked_files(root))
    if not found:
        return 0
    print(
        f"{len(found)} doc problem(s). Link only to tracked files, and keep plans "
        "in the GitHub issue:",
        file=sys.stderr,
    )
    for entry in found:
        print(f"  {entry}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
