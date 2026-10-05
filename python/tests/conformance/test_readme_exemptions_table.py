"""The published README "Conformance exemptions" table is tested against the
executable suite it claims to summarize (#749).

The table is credibility: it tells a reader which scenarios a framework is
exempt from and why, "never a silent skip" (the conformance kit). Nothing
previously checked that the table's rows still match ``KNOWN_LIMITATIONS`` in
``tests/conformance/suite.py`` (the internal matrix) or the public plugin's
``KNOWN_LIMITATIONS`` in ``examples/crewai/main.py`` (the one example that
records any) — so the table could drift silently as either changed. This pins
every row to its source, by framework + scenario, so a row that no longer
matches an asserted exemption in code fails here rather than publishing a
claim nobody verified.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from suite import KNOWN_LIMITATIONS

_README = Path(__file__).resolve().parents[2] / "README.md"

# Human-phrased table label -> the scenario slug it names. One row ("budget
# refusal not retried") names a PUBLIC plugin scenario (the internal matrix
# has no such name); every other row names an INTERNAL matrix scenario.
_LABEL_TO_SCENARIO = {
    "correlation ID propagated": "correlation_id_propagated",
    "gateway identity observed": "gateway_identity_observed",
    "JWT refreshed per send": "jwt_token_refreshed",
    "budget refusal not retried": "retries_token_budget",
    "typed refusal bridged": "typed_refusal_bridged",
}

_PUBLIC_SCENARIOS = {"retries_token_budget"}

# Human-phrased table label -> the framework key(s) in ADAPTERS/KNOWN_LIMITATIONS
# the row names. A row may name more than one framework (comma-separated,
# backticked factory names stripped).
_FRAMEWORK_ALIASES = {
    "CrewAI": "crewai",
    "ADK `model()`, CrewAI": ("adk", "crewai"),
}


def _read_table_rows() -> list[tuple[str, str]]:
    """Parse the "## Conformance exemptions" table's data rows as
    ``(framework_cell, scenario_cell)``, stripped of markdown emphasis."""
    text = _README.read_text()
    start = text.index("## Conformance exemptions")
    try:
        end = text.index("\n## ", start + 1)
    except ValueError:
        end = len(text)
    section = text[start:end]
    rows = [
        line
        for line in section.splitlines()
        if line.startswith("| ") and "---" not in line and "Why it's exempt" not in line
    ]
    assert rows, "no data rows found under '## Conformance exemptions' in README.md"
    parsed = []
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 3, f"expected 3 cells, got {cells!r}"
        parsed.append((cells[0], cells[1]))
    return parsed


def test_every_readme_exemption_row_matches_an_asserted_exemption_in_code() -> None:
    for framework_cell, scenario_cell in _read_table_rows():
        scenario_label = re.sub(r"`[^`]*`", lambda m: m.group(0).strip("`"), scenario_cell)
        assert scenario_label in _LABEL_TO_SCENARIO, (
            f"README row scenario {scenario_cell!r} has no mapping in "
            f"_LABEL_TO_SCENARIO — add one and verify it against KNOWN_LIMITATIONS"
        )
        scenario = _LABEL_TO_SCENARIO[scenario_label]

        frameworks = _FRAMEWORK_ALIASES.get(framework_cell)
        assert frameworks is not None, (
            f"README row framework {framework_cell!r} has no mapping in "
            f"_FRAMEWORK_ALIASES — add one and verify it against KNOWN_LIMITATIONS"
        )
        if isinstance(frameworks, str):
            frameworks = (frameworks,)

        if scenario in _PUBLIC_SCENARIOS:
            # The one public-scenario row today: sourced from the shipped
            # CrewAI example's own KNOWN_LIMITATIONS, not the internal matrix.
            module = importlib.import_module("examples.crewai.main")
            example_limits = getattr(module, "KNOWN_LIMITATIONS", {})
            for fw in frameworks:
                assert fw == "crewai", "only crewai ships a public-scenario exemption example"
                assert scenario in example_limits, (
                    f"README claims {fw!r} is exempt from {scenario!r}, but "
                    f"examples/crewai/main.py's KNOWN_LIMITATIONS has no such entry"
                )
        else:
            for fw in frameworks:
                assert scenario in KNOWN_LIMITATIONS.get(fw, {}), (
                    f"README claims {fw!r} is exempt from {scenario!r}, but "
                    f"tests/conformance/suite.py's KNOWN_LIMITATIONS[{fw!r}] has no such entry"
                )


def test_every_asserted_internal_exemption_is_published_in_the_readme() -> None:
    # The converse: an exemption KNOWN_LIMITATIONS asserts but the README never
    # publishes is credibility nobody can see (the conformance kit's own
    # "exemptions are published in the README" rule).
    published = set()
    for framework_cell, scenario_cell in _read_table_rows():
        scenario_label = re.sub(r"`[^`]*`", lambda m: m.group(0).strip("`"), scenario_cell)
        scenario = _LABEL_TO_SCENARIO.get(scenario_label)
        if scenario is None or scenario in _PUBLIC_SCENARIOS:
            continue
        frameworks = _FRAMEWORK_ALIASES.get(framework_cell, ())
        frameworks = (frameworks,) if isinstance(frameworks, str) else frameworks
        for fw in frameworks:
            published.add((fw, scenario))

    asserted = {
        (framework, scenario)
        for framework, limits in KNOWN_LIMITATIONS.items()
        for scenario in limits
    }
    missing = asserted - published
    assert not missing, f"KNOWN_LIMITATIONS entries missing from the README table: {missing}"
