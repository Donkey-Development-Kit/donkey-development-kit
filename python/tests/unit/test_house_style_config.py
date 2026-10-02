"""CONTRIBUTING §3 rules that live in packaging metadata, checked as tests (#721).

"The style guide is the config": each §3 rule is enforced by a tool or a test.
Ruff, mypy and import-linter cover the code rules; these cover the two that are
about what the package declares and ships.
"""

from __future__ import annotations

import sys
from pathlib import Path

from packaging.requirements import Requirement

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[2]

# A floor (`>=`) or an exclusion of one bad release (`!=`) never stops a user
# from installing a newer framework release; every other operator can.
_FLOOR_OPERATORS = frozenset({">=", "!="})


def _declared_requirements() -> list[str]:
    project = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())["project"]
    declared: list[str] = list(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        declared.extend(extra)
    return declared


def test_dependencies_are_floors_never_ceilings() -> None:
    """Extras are floors, never ceilings (§8.4): no `<`, `<=`, `==` or `~=` pins.

    Environment markers such as ``python_version < '3.12'`` are not version
    pins and are allowed.
    """
    ceilings = [
        raw
        for raw in _declared_requirements()
        for spec in Requirement(raw).specifier
        if spec.operator not in _FLOOR_OPERATORS
    ]
    assert ceilings == [], f"upper or exact pins in pyproject.toml: {ceilings}"


def test_package_ships_py_typed() -> None:
    """PEP 561 marker, so downstream users get the SDK's annotations."""
    assert (_PYTHON_ROOT / "src" / "donkey_kit" / "py.typed").is_file()
