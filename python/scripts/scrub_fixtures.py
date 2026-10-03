"""Scrub tenant identifiers from tracked files, and reject any that remain (#821).

Live captures carry the capturing tenant's organisation, environment and asset
UUIDs, its gateway and identity-provider hostnames, and the upstream provider's
account ids. Some fixtures ship in the wheel, and PyPI releases are immutable,
so none of that may be committed. The capture procedure is: capture, run this
script, then relock (``python -m donkey_kit.simulator.fixtures --relock``).
See ``tests/fixtures/README.md``.

Rewritten, deterministically (the same real value always maps to the same
placeholder, and a placeholder is left alone, so a re-run is a no-op):

* every UUID not in ``ALLOWED_UUIDS`` →
  ``00000000-0000-4000-8000-<12 hex of its sha256>``;
* a tenant hostname (``<name>-<suffix>.….cloudhub.io`` / ``….herokuapp.com``) →
  ``<name>.example.invalid``;
* an upstream provider account header value (``openai-organization``,
  ``openai-project``, ``anthropic-workspace-id``) → a same-length
  ``<prefix>example<hex>`` placeholder.

Bytes are preserved apart from the replacements (CRLF, no trailing newline), so
byte-exact captures stay byte-exact.

    python scripts/scrub_fixtures.py [PATH ...]           # rewrite in place
    python scripts/scrub_fixtures.py --check [PATH ...]   # CI: exit 1 on any finding

``PATH`` is a file, a directory, or a built ``.whl``; it defaults to every file
git tracks in this checkout. ``--check`` also rejects any ``.cloudhub.io`` or
``.herokuapp.com`` host, whatever its shape. A finding prints only the first
eight characters of the value.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]

#: UUIDs that are public, not tenant data. Add one only with a reason.
ALLOWED_UUIDS = frozenset(
    {
        # MuleSoft's public Exchange organisation: the groupId of every stock
        # policy asset, published in the policy docs.
        "68ef9520-24e9-4cf2-b2f5-620025690913",
    }
)

_PLACEHOLDER_PREFIX = "00000000-0000-4000-8000-"
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# <name>-<random suffix>[.<label>...].cloudhub.io — the shape CloudHub and Heroku
# give a deployed app; the suffix is what identifies the tenant's deployment.
_TENANT_HOST = re.compile(
    r"\b(?P<name>[a-z][a-z0-9-]*?)-[a-z0-9]{6,12}(?:\.[a-z0-9-]+)*\.(?:cloudhub\.io|herokuapp\.com)\b",
    re.IGNORECASE,
)
# --check only: any host on those platforms, whatever its shape.
_PLATFORM_HOST = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:cloudhub\.io|herokuapp\.com)\b", re.I)
_PLACEHOLDER_HOST = re.compile(r"\.example\.invalid\b", re.I)
_PROVIDER_HEADER = re.compile(
    r"(?im)^(?P<name>openai-organization|openai-project|anthropic-workspace-id):[ \t]*"
    r"(?P<prefix>[a-z]+[-_])(?P<rest>[A-Za-z0-9]+)"
)
_PROVIDER_PLACEHOLDER = "example"


def _digest(value: str) -> str:
    return hashlib.sha256(value.lower().encode()).hexdigest()


def is_placeholder_uuid(value: str) -> bool:
    """True for a UUID this script wrote (or one in ``ALLOWED_UUIDS``)."""
    v = value.lower()
    return v.startswith(_PLACEHOLDER_PREFIX) or v in ALLOWED_UUIDS


def _uuid(match: re.Match[str]) -> str:
    value = match.group(0)
    if is_placeholder_uuid(value):
        return value
    return _PLACEHOLDER_PREFIX + _digest(value)[:12]


def _host(match: re.Match[str]) -> str:
    return f"{match.group('name').lower()}.example.invalid"


def _provider(match: re.Match[str]) -> str:
    rest = match.group("rest")
    if rest.startswith(_PROVIDER_PLACEHOLDER):
        return match.group(0)
    fill = (_PROVIDER_PLACEHOLDER + _digest(rest))[: len(rest)]
    return match.group(0)[: match.start("rest") - match.start(0)] + fill


def scrub_text(text: str) -> str:
    """``text`` with every tenant identifier replaced by its placeholder."""
    text = _UUID.sub(_uuid, text)
    text = _TENANT_HOST.sub(_host, text)
    return _PROVIDER_HEADER.sub(_provider, text)


def find_in_text(text: str) -> Iterator[tuple[int, str, str]]:
    """Every remaining tenant identifier as ``(line, kind, first 8 chars)``."""

    def line(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    for m in _UUID.finditer(text):
        if not is_placeholder_uuid(m.group(0)):
            yield line(m.start()), "uuid", m.group(0)[:8]
    for m in _PLATFORM_HOST.finditer(text):
        if not _PLACEHOLDER_HOST.search(m.group(0)):
            yield line(m.start()), "platform host", m.group(0)[:8]
    for m in _PROVIDER_HEADER.finditer(text):
        if not m.group("rest").startswith(_PROVIDER_PLACEHOLDER):
            yield line(m.start()), f"{m.group('name').lower()} value", m.group("rest")[:8]


def _decode(data: bytes) -> str | None:
    if b"\0" in data:
        return None  # binary
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def tracked_files(root: Path) -> list[Path]:
    """Every regular file git tracks under ``root``."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return [root / p for p in out.split("\0") if p and (root / p).is_file()]


def _expand(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*") if p.is_file())
        else:
            yield path


def _members(path: Path) -> Iterator[tuple[str, str]]:
    """``(display name, text)`` for each text file at ``path`` (a ``.whl`` is opened)."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as wheel:
            for name in wheel.namelist():
                text = _decode(wheel.read(name))
                if text is not None:
                    yield f"{path}!{name}", text
        return
    text = _decode(path.read_bytes())
    if text is not None:
        yield str(path), text


def check(paths: Iterable[Path]) -> list[str]:
    """Every finding as ``path:line: kind: value-prefix…``."""
    return [
        f"{name}:{lineno}: {kind}: {prefix}…"
        for path in _expand(paths)
        for name, text in _members(path)
        for lineno, kind, prefix in find_in_text(text)
    ]


def scrub(paths: Iterable[Path]) -> list[Path]:
    """Rewrite each file in place; returns the files that changed."""
    changed: list[Path] = []
    for path in _expand(paths):
        if path.suffix == ".whl":
            raise SystemExit(f"{path}: a wheel can only be checked, not scrubbed")
        text = _decode(path.read_bytes())
        if text is None:
            continue
        new = scrub_text(text)
        if new != text:
            path.write_bytes(new.encode("utf-8"))
            changed.append(path)
    return changed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="report, don't rewrite; exit 1 on any finding")
    parser.add_argument("paths", nargs="*", type=Path, help="files, directories or wheels (default: tracked files)")
    args = parser.parse_args(argv)
    paths = args.paths or tracked_files(_DEFAULT_ROOT)

    if args.check:
        found = check(paths)
        if not found:
            return 0
        print(
            f"{len(found)} tenant identifier(s) found. Run python/scripts/scrub_fixtures.py, "
            "then relock (python -m donkey_kit.simulator.fixtures --relock):",
            file=sys.stderr,
        )
        for entry in found:
            print(f"  {entry}", file=sys.stderr)
        return 1

    changed = scrub(paths)
    for path in changed:
        print(f"scrubbed {path}")
    if changed:
        print("now relock: python -m donkey_kit.simulator.fixtures --relock")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
