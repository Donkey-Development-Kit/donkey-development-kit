"""The README "Conformance exemptions" table is tested against the code (#749).

The table appears twice: in the repo ``README.md`` (GitHub) and in
``python/README.md`` (the PyPI description). Both are checked.

Each row names a framework and a scenario that the code records as an asserted
exemption. Most rows come from ``KNOWN_LIMITATIONS`` in
``tests/conformance/suite.py``, the internal matrix. The one row on a scenario
the internal matrix lacks comes from ``examples/crewai/main.py``, whose
``KNOWN_LIMITATIONS`` the public plugin reads. Both directions are checked: a
row with no exemption in code fails, and so does an internal exemption that the
README does not publish.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from suite import KNOWN_LIMITATIONS

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_READMES = [_PYTHON_ROOT.parent / "README.md", _PYTHON_ROOT / "README.md"]
_IDS = ["repo", "pypi"]

# Table label -> scenario name. Every label names an internal matrix scenario
# except "budget refusal not retried", which names a public plugin scenario.
_SCENARIOS = {
    "correlation ID propagated": "correlation_id_propagated",
    "gateway identity observed": "gateway_identity_observed",
    "JWT refreshed per send": "jwt_token_refreshed",
    "budget refusal not retried": "retries_token_budget",
    "typed refusal bridged": "typed_refusal_bridged",
}
_PUBLIC_ONLY = {"retries_token_budget"}

# Table label -> the ADAPTERS keys the row names.
_FRAMEWORKS = {
    "CrewAI": ("crewai",),
}


def _published(readme: Path) -> set[tuple[str, str]]:
    """The ``(framework, scenario)`` pairs the README table publishes."""
    text = readme.read_text()
    start = text.index("## Conformance exemptions")
    end = text.find("\n## ", start + 1)
    section = text[start : end if end != -1 else len(text)]
    rows = [line for line in section.splitlines() if line.startswith("| ")][2:]
    assert rows, f"no data rows under '## Conformance exemptions' in {readme}"
    pairs: set[tuple[str, str]] = set()
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        assert len(cells) == 3, f"expected 3 cells, got {cells!r}"
        framework, scenario = cells[0], cells[1]
        assert scenario in _SCENARIOS, f"README scenario {scenario!r} is not in _SCENARIOS"
        assert framework in _FRAMEWORKS, f"README framework {framework!r} is not in _FRAMEWORKS"
        pairs.update((fw, _SCENARIOS[scenario]) for fw in _FRAMEWORKS[framework])
    return pairs


def _crewai_example_limits() -> dict[str, str]:
    """``KNOWN_LIMITATIONS`` from ``examples/crewai/main.py``, loaded by path.

    Plain ``pytest`` (unlike ``python -m pytest``) does not put ``python/`` on
    ``sys.path``, so ``examples`` is not importable as a package.
    """
    path = _PYTHON_ROOT / "examples" / "crewai" / "main.py"
    spec = importlib.util.spec_from_file_location("_crewai_example_main", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    limits: dict[str, str] = module.KNOWN_LIMITATIONS
    return limits


@pytest.mark.parametrize("readme", _READMES, ids=_IDS)
def test_every_readme_row_is_an_exemption_in_code(readme: Path) -> None:
    example = _crewai_example_limits()
    for framework, scenario in _published(readme):
        if scenario in _PUBLIC_ONLY:
            assert framework == "crewai", (framework, scenario)
            assert scenario in example, f"examples/crewai/main.py does not exempt {scenario!r}"
        else:
            assert scenario in KNOWN_LIMITATIONS.get(framework, {}), (
                f"README exempts {framework!r} from {scenario!r}, but "
                f"tests/conformance/suite.py's KNOWN_LIMITATIONS does not"
            )


@pytest.mark.parametrize("readme", _READMES, ids=_IDS)
def test_every_internal_exemption_is_in_the_readme(readme: Path) -> None:
    asserted = {
        (framework, scenario)
        for framework, limits in KNOWN_LIMITATIONS.items()
        for scenario in limits
    }
    missing = asserted - _published(readme)
    assert not missing, f"KNOWN_LIMITATIONS entries missing from the README table: {missing}"
