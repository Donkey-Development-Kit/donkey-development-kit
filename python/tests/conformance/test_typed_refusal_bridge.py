"""The ``typed_refusal_bridged`` scenario: a refusal reaches user code typed (#724).

Inside ``donkey.run()`` and ``@donkey.governed``, a governance refusal or a
transport failure must reach the caller as its typed
:class:`~donkey_kit.core.errors.DonkeyError`, whatever framework sat between the
call and the caller (BG §1.2, ADR 0002). Without the bridge each surface below
raises its own generic error: ``openai`` and ``anthropic`` an
``APIConnectionError`` (the typed error hidden on ``__cause__``) or a
``PermissionDeniedError``, and LangChain its ``OpenAIConnectionError`` /
``OpenAIPermissionDeniedError`` subclasses.

Three surfaces (the raw OpenAI client, the Anthropic client, a LangGraph graph)
times three errors, each raised by the real transport:

* ``PIIDetected`` — ``donkey.simulate(PIIDetected)`` serves the captured 403.
* ``ModelSubstituted`` — with ``on_model_substitution="raise"``, a 200 whose
  served-model header names another model.
* ``GatewayUnavailable`` — the transport cannot connect.

Each runs in both scopes. ``importorskip``-guarded per framework.
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

# Sibling data module (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS

from donkey_kit import (
    Donkey,
    DonkeyConfig,
    DonkeyError,
    GatewayUnavailable,
    ModelSubstituted,
    PIIDetected,
)
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import _wire

Call = Callable[[Donkey], Awaitable[None]]


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        on_model_substitution="raise",
        max_retries=0,
    )


# --- the three surfaces -----------------------------------------------------


async def _openai(donkey: Donkey) -> None:
    pytest.importorskip("openai")
    client = donkey.openai()
    await client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
    )


async def _anthropic(donkey: Donkey) -> None:
    pytest.importorskip("anthropic")
    client = donkey.anthropic.client()
    await client.messages.create(
        model="claude-sonnet-4-5", max_tokens=16, messages=[{"role": "user", "content": "hi"}]
    )


async def _langgraph(donkey: Donkey) -> None:
    pytest.importorskip("langchain_openai")
    graph_mod = pytest.importorskip("langgraph.graph")
    model = donkey.langgraph.chat_model("gpt-4o")

    async def call_model(state: dict[str, Any]) -> dict[str, Any]:
        await model.ainvoke(state["messages"])
        return {}

    builder = graph_mod.StateGraph(dict)
    builder.add_node("call_model", call_model)
    builder.set_entry_point("call_model")
    builder.add_edge("call_model", graph_mod.END)
    await builder.compile().ainvoke({"messages": [("user", "hi")]})


SURFACES: dict[str, Call] = {"openai": _openai, "anthropic": _anthropic, "langgraph": _langgraph}


# --- the three errors, each raised by the real transport ---------------------


def _substituted(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, headers={_wire.LLM_MODEL_HEADER: "some-other-model"}, json={"model": "x"}
    )


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _arm(donkey: Donkey, error: type[DonkeyError]) -> Any:
    """Make the next call on ``donkey`` fail as ``error``. Returns the context
    manager to hold open for the call, or ``None``."""
    if error is PIIDetected:
        return donkey.simulate(PIIDetected)
    handler = _substituted if error is ModelSubstituted else _unreachable
    for client in http_clients(donkey):
        swap_transport(client, httpx.MockTransport(handler))
    return None


ERRORS = [PIIDetected, ModelSubstituted, GatewayUnavailable]


async def _in_run(donkey: Donkey, call: Call) -> None:
    async with donkey.run():
        await call(donkey)


async def _in_governed(donkey: Donkey, call: Call) -> None:
    @donkey.governed
    async def handle() -> None:
        await call(donkey)

    await handle()


SCOPES = {"run": _in_run, "governed": _in_governed}


def test_scenario_is_registered() -> None:
    assert "typed_refusal_bridged" in CONFORMANCE_SCENARIOS
    # Only the adapters whose framework owns the transport are exempt: no
    # response or transport error of theirs ever passes through the SDK.
    exempt = {a for a, limits in KNOWN_LIMITATIONS.items() if "typed_refusal_bridged" in limits}
    assert exempt == {"adk", "crewai"}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
@pytest.mark.parametrize("surface", SURFACES)
async def test_refusal_reaches_user_code_typed(
    surface: str, error: type[DonkeyError], scope: str
) -> None:
    donkey = Donkey(_cfg())
    try:
        armed = _arm(donkey, error)
        if armed is not None:
            armed.__enter__()
        try:
            with pytest.raises(error) as excinfo:
                await SCOPES[scope](donkey, SURFACES[surface])
        finally:
            if armed is not None:
                armed.__exit__(None, None, None)
    finally:
        await donkey.aclose()

    # The exact typed class, with the framework's own error kept for inspection.
    assert type(excinfo.value) is error
    assert excinfo.value.framework_error is not None
    assert not isinstance(excinfo.value.framework_error, DonkeyError)


@pytest.mark.parametrize("surface", SURFACES)
async def test_without_the_bridge_the_framework_error_reaches_user_code(surface: str) -> None:
    """The control: ``typed_refusals=False`` hands back the framework's own
    error, so the test above proves the bridge, not a typed error the SDK
    already raised."""
    donkey = Donkey(_cfg())
    try:
        with donkey.simulate(PIIDetected), pytest.raises(Exception) as excinfo:
            async with donkey.run(typed_refusals=False):
                await SURFACES[surface](donkey)
    finally:
        await donkey.aclose()
    assert not isinstance(excinfo.value, DonkeyError)


@pytest.mark.parametrize("error", ERRORS, ids=lambda e: e.__name__)
def test_sync_run_and_governed_bridge_the_blocking_client(error: type[DonkeyError]) -> None:
    """The sync scope forms: ``with donkey.run()`` and a sync ``@governed``."""
    pytest.importorskip("openai")
    # A sync call records donkey.last_call in this thread's context; run in a
    # copy so the record does not leak into later tests.
    contextvars.copy_context().run(_sync_bridge_case, error)


def _sync_bridge_case(error: type[DonkeyError]) -> None:
    donkey = Donkey(_cfg())
    client = donkey.openai(sync=True)

    def ask() -> None:
        client.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])

    try:
        armed = _arm(donkey, error)
        if armed is not None:
            armed.__enter__()
        try:
            with pytest.raises(error), donkey.run():
                ask()
            if error is PIIDetected:  # simulate() serves its fixture once
                armed.__exit__(None, None, None)
                armed = _arm(donkey, error)
                armed.__enter__()
            with pytest.raises(error):
                donkey.governed(ask)()
        finally:
            if armed is not None:
                armed.__exit__(None, None, None)
    finally:
        donkey.close()
