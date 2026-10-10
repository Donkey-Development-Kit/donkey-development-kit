"""The declared support matrix is the tested one (#769, ADR 0007 rule 1).

Three CI pieces keep the declared contract honest, and each has a list that
must match the adapter roster or pyproject.toml:

* the nightly ``lowest-direct`` job installs every floor and runs
  ``scripts/check_floors.py``, whose floor logic is tested here offline;
* the ``openai-1x`` PR job holds ``[llm]`` to openai 1.x;
* ``scripts/coinstall_matrix.py`` generates ``docs/co-installability.md``,
  whose classification is tested here with a fake resolver (no network).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.version import Version

from donkey_kit.integrations import ADAPTERS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the 3.10 floor
    import tomli as tomllib

_PYTHON = Path(__file__).resolve().parents[2]
_REPO = _PYTHON.parent
_PYPROJECT = _PYTHON / "pyproject.toml"
_SCRIPTS = _PYTHON / "scripts"
_NIGHTLY = _REPO / ".github" / "workflows" / "nightly-matrix.yml"
_CI = _REPO / ".github" / "workflows" / "ci.yml"
_COINSTALL_DOC = _REPO / "docs" / "co-installability.md"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"ddk_{name}", _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module's string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


floors_mod = _load("check_floors")
coinstall = _load("coinstall_matrix")


def _pyproject() -> dict[str, Any]:
    with _PYPROJECT.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return data


def _adapter_extras() -> list[str]:
    return sorted(s.extra for s in ADAPTERS.values())


# --- scripts/check_floors.py -------------------------------------------------


def test_highest_floor_wins_and_markers_are_honoured() -> None:
    reqs = [
        Requirement("openai>=1.66"),
        Requirement("openai>=2.45"),
        Requirement("tomli>=2.0; python_version < '3'"),
        Requirement("httpx>=0.27,<1"),
        Requirement("rich"),
    ]
    assert floors_mod.floors(reqs) == {"openai": Version("2.45"), "httpx": Version("0.27")}


def test_an_installed_version_above_its_floor_fails() -> None:
    installed = {"httpx": "0.27.0", "openai": "2.45.1"}
    results = floors_mod.check(
        {"httpx": Version("0.27"), "openai": Version("2.45"), "absent": Version("1")},
        installed.get,
    )
    assert {r.name: r.ok for r in results} == {"absent": False, "httpx": True, "openai": False}
    assert "NOT AT FLOOR" in floors_mod.render(results)


def test_self_referencing_extras_are_expanded() -> None:
    pyproject = {
        "project": {
            "dependencies": ["httpx>=0.27"],
            "optional-dependencies": {
                "llm": ["openai>=1.66"],
                "cli": ["typer>=0.15.4"],
                "all": ["donkey-kit[llm,cli]"],
            },
        },
        "dependency-groups": {"dev": ["pytest>=8.0", {"include-group": "x"}]},
    }
    reqs = floors_mod.selected_requirements(pyproject, base=True, dev_group=True, extras=["all"])
    assert sorted(r.name for r in reqs) == ["httpx", "openai", "pytest", "typer"]


def test_an_unknown_extra_is_an_error() -> None:
    with pytest.raises(SystemExit):
        floors_mod.selected_requirements(_pyproject(), base=False, dev_group=False, extras=["x"])


def test_every_declared_dependency_has_a_floor() -> None:
    """A requirement without `>=` has nothing for the lowest-direct job to check."""
    pyproject = _pyproject()
    extras = [e for e in pyproject["project"]["optional-dependencies"] if e != "all"]
    reqs = floors_mod.selected_requirements(pyproject, base=True, dev_group=True, extras=extras)
    unfloored = [str(r) for r in reqs if not any(s.operator == ">=" for s in r.specifier)]
    assert unfloored == []


# --- scripts/coinstall_matrix.py ---------------------------------------------


def test_coinstall_framework_extras_are_the_roster() -> None:
    assert sorted(coinstall.framework_extras(_pyproject())) == _adapter_extras()


def test_combinations_cover_singles_pairs_all_and_everything() -> None:
    combos = coinstall.combinations(["a", "b", "c"])
    assert combos == [
        ("a",),
        ("b",),
        ("c",),
        ("a", "b"),
        ("a", "c"),
        ("b", "c"),
        ("a", "all"),
        ("b", "all"),
        ("c", "all"),
        ("a", "b", "c", "all"),
    ]


def test_only_an_explicit_conflict_is_recorded_as_no() -> None:
    assert coinstall.classify(0, "", ("a",)) == "yes"
    assert coinstall.classify(1, "  × No solution found when resolving", ("a", "b")) == "no"
    with pytest.raises(coinstall.ResolveError):
        coinstall.classify(2, "error: Failed to fetch: https://pypi.org/simple/x/", ("a",))


def test_render_is_symmetric_and_resolve_errors_propagate() -> None:
    def resolver(combo: Sequence[str]) -> tuple[int, str]:
        return (1, "No solution found") if {"a", "b"} <= set(combo) else (0, "")

    results = coinstall.resolve_all(coinstall.combinations(["a", "b"]), resolver, workers=1)
    table = coinstall.render(["a", "b"], results, "3.12")
    assert "| `a` | yes | no | yes |" in table
    assert "| `b` | no | yes | yes |" in table
    assert "together with `[all]`: **no**" in table

    def broken(combo: Sequence[str]) -> tuple[int, str]:
        return 2, "network down"

    with pytest.raises(coinstall.ResolveError):
        coinstall.resolve_all(coinstall.combinations(["a"]), broken, workers=1)


def test_the_committed_table_lists_exactly_the_framework_extras() -> None:
    text = _COINSTALL_DOC.read_text(encoding="utf-8")
    header = next(line for line in text.splitlines() if line.startswith("|  |"))
    columns = [c.strip().strip("`") for c in header.strip("|").split("|")][1:]
    assert columns == [*coinstall.framework_extras(_pyproject()), "all"]
    rows = [
        line.split("|")[1].strip().strip("`")
        for line in text.splitlines()
        if line.startswith("| `")
    ]
    assert rows == coinstall.framework_extras(_pyproject())


# --- the workflows ----------------------------------------------------------


def _nightly_job(name: str) -> dict[str, Any]:
    job: dict[str, Any] = yaml.safe_load(_NIGHTLY.read_text())["jobs"][name]
    return job


def test_nightly_lowest_direct_legs_match_adapters() -> None:
    job = _nightly_job("lowest-direct")
    include = job["strategy"]["matrix"]["include"]
    legs = {entry["leg"]: entry for entry in include}
    assert set(legs) == {"base", "framework-free", *_adapter_extras()}
    assert legs["base"]["install"] == ""
    assert legs["framework-free"]["install"] == "llm,cli,local,otel,test"
    for extra in _adapter_extras():
        assert legs[extra] == {
            "leg": extra,
            "install": f"local,{extra}",
            "floors": extra,
            "contract": extra,
        }
    assert job["env"]["DONKEY_CONTRACT_EXTRA"] == "${{ matrix.contract }}"
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "--resolution lowest-direct" in runs
    assert "scripts/check_floors.py --base --dev-group" in runs
    assert "tests/conformance/test_adapter_contract.py" in runs
    python = [s["with"]["python-version"] for s in job["steps"] if "with" in s]
    floor = _pyproject()["project"]["requires-python"].removeprefix(">=")
    assert python == [floor]


def test_nightly_co_installability_checks_the_committed_table() -> None:
    runs = [str(s.get("run", "")) for s in _nightly_job("co-installability")["steps"]]
    assert any("scripts/coinstall_matrix.py --check" in r for r in runs)


def test_openai_1x_job_pins_openai_below_2() -> None:
    job = yaml.safe_load(_CI.read_text())["jobs"]["openai-1x"]
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert '"openai<2"' in runs
    assert "constraints/openai-1x-py3.12.txt" in runs
