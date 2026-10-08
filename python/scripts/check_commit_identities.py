"""Reject placeholder git identities in a PR's commits (#1048).

A squash merge turns every distinct commit author on the PR branch into a
``Co-authored-by:`` trailer on ``develop``, and GitHub credits whichever account
has claimed that email. Commits authored as ``x <x@x>`` once credited an
unrelated account that way, and the trailer can only be removed by rewriting
``develop``. This script catches the identity on the PR, before the merge.

For each commit in ``RANGE`` it checks the author, the committer and every
``Co-authored-by`` trailer, and fails on a placeholder identity:

* a name one character long, or empty;
* an email that is not ``local@domain``, whose domain has no dot, or whose
  top-level domain is not at least two letters (``x@x``, ``a@b.c``);
* an email on a reserved domain (RFC 2606/6761: ``example.com``, ``.test``,
  ``.invalid``, ``.localhost``, ...).

    python scripts/check_commit_identities.py BASE_SHA..HEAD_SHA

Problems print as GitHub annotations (``::error::``); exits 1 on any. Uses only
the standard library, so CI can run it without installing the package.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

_FIELD = "\x1f"
_RECORD = "\x00"
_TRAILER = "\x1e"
# git's own %xNN escapes: argv cannot carry a NUL byte.
_FORMAT = (
    "%x1f".join(
        [
            "%H",
            "%an",
            "%ae",
            "%cn",
            "%ce",
            "%(trailers:key=Co-authored-by,valueonly,separator=%x1e)",
        ]
    )
    + "%x00"
)
_TRAILER_VALUE = re.compile(r"^(?P<name>.*?)\s*<(?P<email>[^<>]*)>$")
_RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org"})
_RESERVED_TLDS = frozenset({"example", "invalid", "localhost", "test"})
_TLD = re.compile(r"^[a-z]{2,}$")

ReadLog = Callable[[str], str]


@dataclass(frozen=True)
class Identity:
    role: str
    name: str
    email: str


@dataclass(frozen=True)
class Commit:
    sha: str
    identities: tuple[Identity, ...]


def identity_problem(name: str, email: str) -> str | None:
    """Why ``name <email>`` is a placeholder identity, or ``None`` if it is not."""
    if len(name.strip()) <= 1:
        return f"name {name!r} is too short to be a real identity"
    local, at, domain = email.strip().lower().rpartition("@")
    if not at or not local or not domain:
        return f"email {email!r} is not local@domain"
    if "." not in domain:
        return f"email domain {domain!r} has no dot"
    if not _TLD.match(domain.rsplit(".", 1)[1]):
        return f"email domain {domain!r} has no real top-level domain"
    if domain in _RESERVED_DOMAINS or domain.rsplit(".", 1)[1] in _RESERVED_TLDS:
        return f"email domain {domain!r} is reserved for examples and tests"
    return None


def parse_log(log: str) -> list[Commit]:
    """Parse ``git log`` output written with :data:`_FORMAT`."""
    commits = []
    for record in log.split(_RECORD):
        record = record.strip("\n")
        if not record:
            continue
        sha, author, author_email, committer, committer_email, trailers = record.split(_FIELD)
        identities = [
            Identity("author", author, author_email),
            Identity("committer", committer, committer_email),
        ]
        for value in filter(None, (t.strip() for t in trailers.split(_TRAILER))):
            match = _TRAILER_VALUE.match(value)
            name, email = (match["name"], match["email"]) if match else (value, "")
            identities.append(Identity("Co-authored-by", name, email))
        commits.append(Commit(sha, tuple(identities)))
    return commits


def problems(commits: Iterable[Commit]) -> list[str]:
    """One message per placeholder identity, in commit order."""
    found = []
    for commit in commits:
        for identity in commit.identities:
            reason = identity_problem(identity.name, identity.email)
            if reason:
                found.append(
                    f"{commit.sha[:12]}: {identity.role} {identity.name} <{identity.email}>: "
                    f"{reason}. Fix the git identity and rewrite the branch's commits."
                )
    return found


def read_log(rev_range: str) -> str:
    """``git log`` for ``rev_range`` in :data:`_FORMAT`, one NUL-terminated record per commit."""
    return subprocess.run(
        ["git", "log", f"--format={_FORMAT}", rev_range],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def main(argv: list[str], log: ReadLog = read_log) -> int:
    if len(argv) != 1:
        print("usage: check_commit_identities.py BASE..HEAD", file=sys.stderr)
        return 2
    found = problems(parse_log(log(argv[0])))
    for message in found:
        print(f"::error::{message}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
