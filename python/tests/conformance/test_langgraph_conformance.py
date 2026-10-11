"""AC5: the customer-facing conformance suite runs green against a real
LangGraph app (BG §1.8, #198).

This is the in-process customer harness (``donkey_kit.conformance``, #191) — the
same one a team runs against its own agent with
``pytest --donkey-conformance --donkey-agent=...`` — pointed at the shipped example's
``build`` factory (``examples/langgraph/main.py``). It exercises the actual
compiled ``StateGraph`` (correlation node + a ``typed_refusals()``-wrapped model
node), not a bare model call, so a green run here is the acceptance evidence
that the deep adapter passes conformance end to end.

No docker and no network: the harness swaps a fixture transport onto the
``Donkey`` it builds the agent against, replaying the same captured refusal
bytes ``classify()`` is tested against. Skipped where the ``langgraph`` extra is
absent.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from donkey_kit.conformance import run_conformance

# The four public scenarios, all expected to pass for the deep adapter (no
# KNOWN_LIMITATIONS: full-transport injection means nothing is exempt).
_EXPECTED_SCENARIOS = {
    "retries_token_budget",
    "swallows_pii_as_generic",
    "correlation_id_propagated",
    "works_without_budget_headers",
}


def _load_example_build() -> Any:
    """Import the shipped example's ``build`` factory by file path (examples are
    not an installed package), so conformance runs against the real example."""
    example = Path(__file__).resolve().parents[2] / "examples" / "langgraph" / "main.py"
    spec = importlib.util.spec_from_file_location("ddk_example_langgraph", example)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the graph's TypedDict state resolves its annotations
    # via get_type_hints (which reads sys.modules[__name__].__dict__) — the same
    # as the real `--donkey-agent=module:build` import path.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.build


async def test_langgraph_example_passes_conformance() -> None:
    pytest.importorskip("langchain_openai")
    pytest.importorskip("langgraph.graph")
    build = _load_example_build()

    results = await run_conformance(build)

    by_name = {r.scenario: r for r in results}
    assert set(by_name) == _EXPECTED_SCENARIOS
    failures = [f"{r.scenario}: {r.detail}" for r in results if r.status != "pass"]
    assert not failures, "conformance scenarios did not pass:\n" + "\n".join(failures)
