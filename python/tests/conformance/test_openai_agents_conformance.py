"""The customer-facing conformance suite runs green against a real OpenAI Agents
SDK app (BG §1.8, #223).

The in-process customer harness (``donkey_kit.conformance``, #191) — the same
one a team runs against its own agent with
``pytest --donkey-conformance --donkey-agent=...`` — pointed at the shipped
example's ``build`` factory (``examples/openai_agents/main.py``). It drives a
real ``agents.Agent`` through ``Runner.run`` (dynamic instructions that log the
correlation id + a ``typed_refusals()``-wrapped run), not a bare model call, so
a green run here is the acceptance evidence that the second deep adapter passes
conformance end to end.

No docker and no network: the harness swaps a fixture transport onto the
``Donkey`` it builds the agent against, replaying the same captured refusal
bytes ``classify()`` is tested against. Skipped where the ``openai-agents``
extra is absent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from donkey_kit.conformance import run_conformance

# The four public scenarios, all expected to pass: full-transport injection
# means none is exempt. The adapter's one asserted exemption,
# approval_round_trip, is internal-only (tests/conformance/suite.py).
_EXPECTED_SCENARIOS = {
    "retries_token_budget",
    "swallows_pii_as_generic",
    "correlation_id_propagated",
    "works_without_budget_headers",
}


def _load_example_build() -> Any:
    """Import the shipped example's ``build`` factory by file path (examples are
    not an installed package), so conformance runs against the real example."""
    example = Path(__file__).resolve().parents[2] / "examples" / "openai_agents" / "main.py"
    spec = importlib.util.spec_from_file_location("ddk_example_openai_agents", example)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec, the same as the real `--donkey-agent=module:build`
    # import path.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.build


async def test_openai_agents_example_passes_conformance() -> None:
    pytest.importorskip("agents")
    build = _load_example_build()

    results = await run_conformance(build)

    by_name = {r.scenario: r for r in results}
    assert set(by_name) == _EXPECTED_SCENARIOS
    failures = [f"{r.scenario}: {r.detail}" for r in results if r.status != "pass"]
    assert not failures, "conformance scenarios did not pass:\n" + "\n".join(failures)
