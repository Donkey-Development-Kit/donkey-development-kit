"""Fail a publish whose version, tag and built dists disagree (#767).

The package version is declared once, as ``__version__`` in
``src/donkey_kit/__init__.py``; ``pyproject.toml`` declares it dynamic and hatch
reads that line. A PyPI upload cannot be undone, so before either publish
workflow uploads anything this script checks that:

* the declared version is a normalised PEP 440 version on the release ladder
  (``X.Y.Z``, ``X.Y.Z.devN``, ``X.Y.ZaN``, ``X.Y.ZbN`` or ``X.Y.ZrcN``,
  docs/releasing.md "Versioning & naming");
* with ``--tag``, the git tag is exactly ``v<version>``;
* with ``--final``, the version is a final ``X.Y.Z`` (only a final release
  reaches production PyPI);
* every built dist given on the command line carries that same version, read
  from the dist's own metadata (``METADATA`` in a wheel, ``PKG-INFO`` in an
  sdist), not from its filename alone.

    python scripts/check_release_version.py [--tag TAG] [--final] [DIST ...]

Exits 1 and lists every problem when any is found. Standard library only, so
it runs in the publish workflows' bare build environment.
"""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from collections.abc import Sequence
from email.parser import HeaderParser
from pathlib import Path

INIT_PY = Path(__file__).resolve().parents[1] / "src" / "donkey_kit" / "__init__.py"

# The same line hatch's default version pattern reads ([tool.hatch.version]).
_DECLARED = re.compile(r"^__version__\s*=\s*[\"']([^\"']+)[\"']\s*$", re.MULTILINE)
# Normalised PEP 440 spellings only, so the tag, the filename and the PyPI
# version are byte-identical (no `0.1.0-alpha.1`, no `0.1.0.dev`).
_LADDER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.dev\d+|a\d+|b\d+|rc\d+)?$")
_FINAL = re.compile(r"^\d+\.\d+\.\d+$")


def declared_version(init_py: Path = INIT_PY) -> str:
    """The one ``__version__ = "..."`` line in ``init_py``."""
    found = _DECLARED.findall(init_py.read_text(encoding="utf-8"))
    if len(found) != 1:
        raise ValueError(f"{init_py}: expected one __version__ line, found {len(found)}")
    return str(found[0])


def _metadata_version(raw: bytes, source: str) -> str:
    version = HeaderParser().parsestr(raw.decode("utf-8")).get("Version")
    if not version:
        raise ValueError(f"{source}: no Version field")
    return str(version)


def dist_version(dist: Path) -> str:
    """The ``Version`` a built wheel or sdist declares in its own metadata."""
    if dist.suffix == ".whl":
        with zipfile.ZipFile(dist) as whl:
            names = [n for n in whl.namelist() if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)]
            if len(names) != 1:
                raise ValueError(f"{dist.name}: expected one .dist-info/METADATA, found {names}")
            return _metadata_version(whl.read(names[0]), f"{dist.name}:{names[0]}")
    if dist.name.endswith(".tar.gz"):
        with tarfile.open(dist, "r:gz") as sdist:
            members = [m for m in sdist.getmembers() if re.fullmatch(r"[^/]+/PKG-INFO", m.name)]
            if len(members) != 1:
                raise ValueError(f"{dist.name}: expected one top-level PKG-INFO")
            handle = sdist.extractfile(members[0])
            if handle is None:
                raise ValueError(f"{dist.name}: PKG-INFO is not a regular file")
            return _metadata_version(handle.read(), f"{dist.name}:{members[0].name}")
    raise ValueError(f"{dist.name}: not a wheel (.whl) or an sdist (.tar.gz)")


def problems(
    version: str, *, tag: str | None = None, final: bool = False, dists: Sequence[Path] = ()
) -> list[str]:
    """Every way ``version``, ``tag`` and ``dists`` fail to agree; empty when they do."""
    found: list[str] = []
    if not _LADDER.match(version):
        found.append(
            f"version {version!r} is not a normalised PEP 440 release-ladder version "
            "(X.Y.Z, X.Y.Z.devN, X.Y.ZaN, X.Y.ZbN or X.Y.ZrcN)"
        )
    if tag is not None and tag != f"v{version}":
        found.append(f"tag {tag!r} does not match the package version: expected 'v{version}'")
    if final and not _FINAL.match(version):
        found.append(f"version {version!r} is a pre-release; production PyPI takes only X.Y.Z")
    for dist in dists:
        try:
            built = dist_version(dist)
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            found.append(f"{dist.name}: cannot read its version ({exc})")
            continue
        if built != version:
            found.append(f"{dist.name} was built as {built!r}, not {version!r}")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dists", nargs="*", type=Path, help="built wheel/sdist files to check")
    parser.add_argument("--tag", help="the release's git tag; must equal v<version>")
    parser.add_argument("--final", action="store_true", help="require a final X.Y.Z version")
    args = parser.parse_args(argv)

    version = declared_version()
    found = problems(version, tag=args.tag, final=args.final, dists=args.dists)
    if found:
        for problem in found:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    checked = ", ".join(d.name for d in args.dists) or "no dists"
    tag = f"tag {args.tag}, " if args.tag else ""
    print(f"release version {version}: {tag}{checked} agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
