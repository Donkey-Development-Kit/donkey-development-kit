"""The #198 deep-adapter additions: callable sugar, the ``typed_refusals()``
bridge, and interrupt/refusal composition (BG §1.8).

Correlation-id propagation (AC2) is proven by ``test_langgraph_correlation.py``
and AC5 (conformance against the real example app) by
``tests/conformance/test_langgraph_conformance.py``; this file covers AC1
(callable form returns the native object), AC3 (a proxy refusal raised inside a
node surfaces typed), and AC4 (``interrupt()`` and a typed refusal compose).

Everything that constructs a native object ``importorskip``s its framework, so
the file is collected but its framework-dependent tests skip on the base path.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import PIIDetected
from donkey_kit.core.transport import build_http_client
from donkey_kit.integrations.langgraph import LangGraphAdapter, typed_refusals


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


# --- AC1: callable sugar returns the native ChatOpenAI --------------------------


def test_calling_the_adapter_returns_native_chat_model() -> None:
    pytest.importorskip("langchain_openai")
    adapter = LangGraphAdapter(_cfg(), build_http_client(_cfg(), None))

    model = adapter("gpt-4o", temperature=0.3)  # donkey.langgraph("gpt-4o")

    assert type(model).__name__ == "ChatOpenAI"  # native object, not a wrapper
    assert model.openai_api_base == "https://proxy"
    assert model.temperature == 0.3  # kwargs pass through, same as chat_model()


def test_call_matches_chat_model() -> None:
    pytest.importorskip("langchain_openai")
    adapter = LangGraphAdapter(_cfg(), build_http_client(_cfg(), None))

    called = adapter("gpt-4o")
    explicit = adapter.chat_model("gpt-4o")

    assert type(called) is type(explicit)
    assert called.model_name == explicit.model_name


# --- AC3: typed_refusals() bridges openai errors back to the taxonomy -----------


def _pii_response() -> httpx.Response:
    """A 403 shaped like the captured PII rejection classify() maps to
    PIIDetected (nested error object, ``type == "pii_detected"``)."""
    request = httpx.Request("POST", "https://proxy/chat/completions")
    return httpx.Response(
        403,
        json={"error": {"type": "pii_detected", "message": "blocked: [{\"pii_type\": \"EMAIL\"}]"}},
        request=request,
    )


def test_typed_refusals_converts_openai_status_error_to_typed() -> None:
    openai = pytest.importorskip("openai")
    err = openai.APIStatusError("blocked", response=_pii_response(), body=None)

    with pytest.raises(PIIDetected) as excinfo:
        with typed_refusals():
            raise err

    # The framework error is kept on .framework_error, not chained: its message
    # repeats the gateway text, which tracebacks would render.
    assert excinfo.value.framework_error is err
    assert excinfo.value.__cause__ is None
    assert "EMAIL" in excinfo.value.entities


def test_typed_refusals_passes_through_errors_without_a_response() -> None:
    """An error that is not an ``APIStatusError`` (no HTTP response — a plain
    bug, or a transport ``APIConnectionError``) is not a gateway refusal and must
    propagate untouched, never masked as a DonkeyError."""
    pytest.importorskip("openai")
    with pytest.raises(ValueError, match="boom"):
        with typed_refusals():
            raise ValueError("boom")


async def test_refusal_inside_a_node_surfaces_typed_out_of_the_graph() -> None:
    """End-to-end AC3: a PII refusal raised by ``model.ainvoke`` inside a node,
    wrapped in ``typed_refusals()``, propagates out of ``graph.ainvoke`` as
    PIIDetected — not the framework-wrapped ``OpenAIPermissionDeniedError``."""
    pytest.importorskip("langchain_openai")
    graph_mod = pytest.importorskip("langgraph.graph")
    StateGraph, END = graph_mod.StateGraph, graph_mod.END

    fab = Donkey(_cfg())
    model = fab.langgraph.chat_model("gpt-4o")

    async def call_model(state: dict) -> dict:
        with typed_refusals():
            await model.ainvoke(state["messages"])
        return {}

    builder = StateGraph(dict)
    builder.add_node("call_model", call_model)
    builder.set_entry_point("call_model")
    builder.add_edge("call_model", END)
    graph = builder.compile()

    try:
        with fab.simulate(PIIDetected):
            with pytest.raises(PIIDetected):
                await graph.ainvoke({"messages": [("user", "hi")]})
    finally:
        await fab.aclose()


# --- AC4: interrupt() and a typed refusal compose without swallowing ------------


async def test_interrupt_and_typed_refusal_compose() -> None:
    """A graph that pauses at ``interrupt()`` and then hits a refusal proves the
    two mechanisms don't swallow each other: the first invoke pauses cleanly
    (the refusal is never triggered mid-pause), and resuming surfaces the typed
    refusal from the model node."""
    pytest.importorskip("langchain_openai")
    graph_mod = pytest.importorskip("langgraph.graph")
    StateGraph, END = graph_mod.StateGraph, graph_mod.END
    MemorySaver = pytest.importorskip("langgraph.checkpoint.memory").MemorySaver
    types_mod = pytest.importorskip("langgraph.types")
    interrupt, Command = types_mod.interrupt, types_mod.Command

    fab = Donkey(_cfg())
    model = fab.langgraph.chat_model("gpt-4o")

    async def gate(_state: dict) -> dict:
        decision = interrupt("approve this run?")
        return {"approved": decision}

    async def call_model(_state: dict) -> dict:
        with typed_refusals():
            await model.ainvoke([("user", "hi")])
        return {}

    builder = StateGraph(dict)
    builder.add_node("gate", gate)
    builder.add_node("call_model", call_model)
    builder.set_entry_point("gate")
    builder.add_edge("gate", "call_model")
    builder.add_edge("call_model", END)
    graph = builder.compile(checkpointer=MemorySaver())
    thread = {"configurable": {"thread_id": "compose-1"}}

    try:
        # First invoke pauses at the interrupt — no refusal surfaces yet.
        paused = await graph.ainvoke({}, thread)
        assert "__interrupt__" in paused

        # Resume: the model node now runs and the armed refusal comes back typed.
        with fab.simulate(PIIDetected):
            with pytest.raises(PIIDetected):
                await graph.ainvoke(Command(resume="yes"), thread)
    finally:
        await fab.aclose()
