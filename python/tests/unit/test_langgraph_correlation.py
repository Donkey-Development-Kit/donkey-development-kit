"""LangGraph correlation-id propagation (#195, BG §1.1).

Proves the #195 acceptance criterion "propagation reaches every LangGraph node
via contextvar (no threading through state)": a run id bound once with
``donkey.run(id=…)`` is visible inside every node the graph runs, without being
passed through the graph's state channel. LangGraph runs nodes on tasks that
copy the current context at creation, so a contextvar-bound id reaches them for
free — nothing is threaded through call arguments or state.

Skipped where ``langgraph`` is not installed (the ``langgraph`` extra); runs in
any env with the extra present, including the nightly matrix.
"""

from __future__ import annotations

import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.telemetry import current_correlation_id


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


async def test_run_id_reaches_every_langgraph_node() -> None:
    graph_mod = pytest.importorskip("langgraph.graph")
    StateGraph = graph_mod.StateGraph
    END = graph_mod.END

    # Each node reads the contextvar directly and records what it saw. The run id
    # is NEVER written into the graph state (the nodes return no state update), so
    # observing it proves contextvar propagation — not state threading.
    observed: list[str | None] = []

    async def first(_state: dict) -> dict:
        observed.append(current_correlation_id())
        return {}

    async def second(_state: dict) -> dict:
        observed.append(current_correlation_id())
        return {}

    builder = StateGraph(dict)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.set_entry_point("first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    graph = builder.compile()

    fab = Donkey(_cfg())
    with fab.run(id="run-lg"):
        await graph.ainvoke({})

    assert observed == ["run-lg", "run-lg"]  # every node saw the bound run id
    assert current_correlation_id() is None  # nothing leaked past the block
