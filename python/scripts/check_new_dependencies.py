"""Gate new direct dependencies on the allowlist and on PyPI (#936).

Every direct dependency (``dependencies``, every extra, every dependency group)
is a reviewed decision recorded in ``dependency_allowlist.toml``, with a reason
and a review date. ``tests/unit/test_house_style_config.py`` checks the two stay
in sync through :func:`allowlist_problems`.

This script adds the check a test cannot make offline. For each name declared
in ``pyproject.toml`` but not in the base branch's copy, it asks PyPI's JSON API
about the project, so a hallucinated or typosquatted name fails the PR before
anyone installs it:

* **error** if PyPI has no such project, or the project has no released files;
* **warning** if its first release is less than 90 days old, its last release is
  more than two years old, or its name is within edit distance 2 of another
  allowlisted name.

    python scripts/check_new_dependencies.py --base BASE_PYPROJECT \\
        [--pyproject PYPROJECT] [--allowlist ALLOWLIST]

A missing ``BASE_PYPROJECT`` counts as declaring nothing. Problems print as
GitHub annotations (``::error::`` / ``::warning::``); exits 1 on any error. Uses
only the standard library, so CI can run it without installing the package.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = _PYTHON_ROOT / "pyproject.toml"
DEFAULT_ALLOWLIST = _PYTHON_ROOT / "dependency_allowlist.toml"

_SELF = "donkey-kit"
_NAME = re.compile(r"^\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")
_YOUNG = timedelta(days=90)
_STALE = timedelta(days=2 * 365)
_LOOKALIKE_DISTANCE = 2

Fetch = Callable[[str], "dict[str, Any] | None"]


def canonicalize(name: str) -> str:
    """The PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(raw: str) -> str:
    """The canonical distribution name of a PEP 508 requirement string."""
    match = _NAME.match(raw)
    if match is None:
        raise ValueError(f"not a requirement: {raw!r}")
    return canonicalize(match.group(1))


def declared_names(pyproject: Mapping[str, Any]) -> set[str]:
    """Every direct dependency name, excluding this package's own extras."""
    project = pyproject.get("project", {})
    raw: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        raw.extend(extra)
    for group in pyproject.get("dependency-groups", {}).values():
        # `{include-group = "..."}` tables reuse another group's names.
        raw.extend(item for item in group if isinstance(item, str))
    return {requirement_name(r) for r in raw} - {_SELF}


def load_allowlist(path: Path) -> dict[str, dict[str, Any]]:
    """The ``[packages.<name>]`` entries, keyed by canonical name."""
    packages = tomllib.loads(path.read_text(encoding="utf-8")).get("packages", {})
    return {canonicalize(name): entry for name, entry in packages.items()}


def allowlist_problems(declared: set[str], allowlist: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Every mismatch between the declared names and the allowlist, sorted by name."""
    found: list[tuple[str, str]] = []
    for name in declared - allowlist.keys():
        found.append((name, "declared in pyproject.toml but not in dependency_allowlist.toml"))
    for name in allowlist.keys() - declared:
        found.append((name, "in dependency_allowlist.toml but no longer declared"))
    for name, entry in allowlist.items():
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            found.append((name, "'reason' must be a non-empty string"))
        if not isinstance(entry.get("reviewed"), date):
            found.append((name, "'reviewed' must be a TOML date (YYYY-MM-DD)"))
    return [f"{name}: {message}" for name, message in sorted(found)]


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance between two strings."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def fetch_project(name: str) -> dict[str, Any] | None:
    """PyPI's JSON metadata for ``name``, or ``None`` if PyPI has no such project."""
    url = f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "donkey-kit-dependency-check"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload: dict[str, Any] = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return payload


def _upload_dates(project: Mapping[str, Any]) -> list[date]:
    return [
        date.fromisoformat(file["upload_time_iso_8601"][:10])
        for files in project.get("releases", {}).values()
        for file in files
    ]


def assess(
    name: str, project: Mapping[str, Any] | None, others: Iterable[str], today: date
) -> tuple[list[str], list[str]]:
    """``(errors, warnings)`` for one newly declared dependency."""
    if project is None:
        return [f"{name}: not found on PyPI"], []
    uploads = _upload_dates(project)
    if not uploads:
        return [f"{name}: exists on PyPI but has no released files"], []
    warnings: list[str] = []
    first, last = min(uploads), max(uploads)
    if today - first < _YOUNG:
        warnings.append(f"{name}: first release {first} is less than 90 days old")
    if today - last > _STALE:
        warnings.append(f"{name}: last release {last} is more than 2 years old")
    for other in sorted(set(others) - {name}):
        if edit_distance(name, other) <= _LOOKALIKE_DISTANCE:
            warnings.append(
                f"{name}: name is within edit distance {_LOOKALIKE_DISTANCE} of {other!r}"
            )
    return [], warnings


def _names_in(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return declared_names(tomllib.loads(path.read_text(encoding="utf-8")))


def main(argv: list[str], fetch: Fetch = fetch_project, today: date | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True, help="base branch's pyproject.toml")
    parser.add_argument("--pyproject", type=Path, default=DEFAULT_PYPROJECT)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    args = parser.parse_args(argv)
    today = today or date.today()

    new = sorted(_names_in(args.pyproject) - _names_in(args.base))
    if not new:
        print("No new direct dependencies.")
        return 0
    others = load_allowlist(args.allowlist).keys() if args.allowlist.is_file() else set()

    errors: list[str] = []
    warnings: list[str] = []
    for name in new:
        try:
            project = fetch(name)
        except OSError as exc:
            errors.append(f"{name}: could not query PyPI ({exc})")
            continue
        found_errors, found_warnings = assess(name, project, others, today)
        errors.extend(found_errors)
        warnings.extend(found_warnings)

    print(f"New direct dependencies: {', '.join(new)}")
    for message in warnings:
        print(f"::warning::{message}")
    for message in errors:
        print(f"::error::{message}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
