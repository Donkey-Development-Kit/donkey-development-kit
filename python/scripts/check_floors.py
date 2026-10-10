"""Check that every declared floor is the version actually installed (ADR 0007 rule 1, #769).

ADR 0007 rule 1 says a floor is the lowest release the SDK is verified
against. The nightly `lowest-direct` job keeps that claim honest: it installs
with `uv pip install --resolution lowest-direct`, which picks the lowest
version every direct requirement allows, runs the suites, then runs this
script. A floor that the resolver could not reach (another requirement in the
same install forces something newer) is a floor nothing tests, so this script
fails the leg rather than letting the suites pass against a newer release.

    python scripts/check_floors.py [--base] [--dev-group] [--extras a,b,...]

`--base` checks `[project].dependencies`, `--dev-group` the `dev` dependency
group, and `--extras` each named extra (a `donkey-kit[...]` self-reference is
expanded). Requirements whose environment marker does not hold here are
skipped. A package named more than once is held to its highest floor, the one
the resolver has to honour. Exits 1 on any installed version that is not
exactly its floor, or on a floored package that is not installed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = _PYTHON_ROOT / "pyproject.toml"
_SELF = "donkey-kit"

InstalledVersion = Callable[[str], "str | None"]


@dataclass(frozen=True)
class FloorResult:
    name: str
    floor: Version
    installed: str | None

    @property
    def ok(self) -> bool:
        return self.installed is not None and Version(self.installed) == self.floor


def _installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _extra_requirements(
    extras: Mapping[str, Sequence[str]], extra: str, seen: set[str]
) -> list[Requirement]:
    """``extra``'s requirements, with ``donkey-kit[...]`` self-references expanded."""
    if extra in seen:
        return []
    seen.add(extra)
    if extra not in extras:
        raise SystemExit(f"error: pyproject.toml has no extra named {extra!r}")
    out: list[Requirement] = []
    for raw in extras[extra]:
        req = Requirement(raw)
        if canonicalize_name(req.name) == _SELF:
            for inner in sorted(req.extras):
                out.extend(_extra_requirements(extras, inner, seen))
        else:
            out.append(req)
    return out


def selected_requirements(
    pyproject: Mapping[str, Any], *, base: bool, dev_group: bool, extras: Iterable[str]
) -> list[Requirement]:
    """The requirements a leg's install line declares, as written in pyproject."""
    project = pyproject["project"]
    reqs: list[Requirement] = []
    if base:
        reqs.extend(Requirement(r) for r in project.get("dependencies", []))
    if dev_group:
        group = pyproject.get("dependency-groups", {}).get("dev", [])
        reqs.extend(Requirement(r) for r in group if isinstance(r, str))
    seen: set[str] = set()
    for extra in extras:
        reqs.extend(_extra_requirements(project.get("optional-dependencies", {}), extra, seen))
    return reqs


def floors(requirements: Iterable[Requirement]) -> dict[str, Version]:
    """The binding ``>=`` floor per package: the highest one declared."""
    out: dict[str, Version] = {}
    for req in requirements:
        if req.marker is not None and not req.marker.evaluate({"extra": ""}):
            continue
        for spec in req.specifier:
            if spec.operator != ">=":
                continue
            name = canonicalize_name(req.name)
            version = Version(spec.version)
            if name not in out or version > out[name]:
                out[name] = version
    return out


def check(
    declared: Mapping[str, Version], installed: InstalledVersion = _installed_version
) -> list[FloorResult]:
    return [FloorResult(name, floor, installed(name)) for name, floor in sorted(declared.items())]


def render(results: Sequence[FloorResult]) -> str:
    lines = ["| Package | Floor | Installed | |", "| --- | --- | --- | --- |"]
    for r in results:
        mark = "ok" if r.ok else "NOT AT FLOOR"
        lines.append(f"| `{r.name}` | {r.floor} | {r.installed or 'missing'} | {mark} |")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument("--base", action="store_true", help="check [project].dependencies")
    parser.add_argument("--dev-group", action="store_true", help="check the dev group")
    parser.add_argument("--extras", default="", help="comma-separated extras to check")
    args = parser.parse_args(argv)

    pyproject = tomllib.loads(args.pyproject.read_text(encoding="utf-8"))
    extras = [e.strip() for e in args.extras.split(",") if e.strip()]
    reqs = selected_requirements(pyproject, base=args.base, dev_group=args.dev_group, extras=extras)
    results = check(floors(reqs))
    if not results:
        print("error: nothing to check; pass --base, --dev-group or --extras", file=sys.stderr)
        return 2
    print(render(results))
    bad = [r for r in results if not r.ok]
    for r in bad:
        print(
            f"::error::{r.name}: declared floor {r.floor}, lowest-direct installed "
            f"{r.installed or 'nothing'}. Raise the floor to the lowest version that "
            "resolves, or fix what forces it up (ADR 0007 rule 1)."
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
