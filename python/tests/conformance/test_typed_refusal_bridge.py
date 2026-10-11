"""The ``typed_refusal_bridged`` scenario: a refusal reaches user code typed (#724).

Inside ``donkey.run()`` and ``@donkey.governed``, a governance refusal or a
transport failure must reach the caller as its typed
:class:`~donkey_kit.core.errors.DonkeyError`, whatever framework sat between the
call and the caller (BG §1.2, ADR 0002). Without the bridge each surface below
raises its own generic error: ``openai`` and ``anthropic`` an
``APIConnectionError`` (the typed error hidden on ``__cause__``) or a
``PermissionDeniedError``, LangChain its ``OpenAIConnectionError`` /
``OpenAIPermissionDeniedError`` subclasses, and google-genai (under ADK's
``Gemini``) a ``ClientError``. The OpenAI Agents SDK, LlamaIndex and Strands
sit on the ``openai`` client and raise its errors; Agent Framework wraps them in
a ``ChatClientException``. LiteLLM (under ADK's ``LiteLlm``) raises its own
``APIError`` or ``InternalServerError`` around a response it rebuilt.

Ten surfaces (the raw OpenAI client, the Anthropic client, a LangGraph graph,
an OpenAI Agents SDK ``Runner`` run, a LlamaIndex ``FunctionAgent`` run, an ADK
``LlmAgent`` on ``adk.gemini()`` (#955) and on ``adk.model()``, buffered and
streamed (#969), a Strands ``Agent`` and an Agent Framework ``Agent`` (#983))
times three errors, each raised by the real transport:

* ``PIIDetected`` — ``donkey.simulate(PIIDetected)`` serves the captured 403.
* ``ModelSubstituted`` — with ``on_model_substitution="raise"``, a 200 whose
  served-model header names another model.
* ``GatewayUnavailable`` — the transport cannot connect.

Each runs in both scopes. ``importorskip``-guarded per framework. CrewAI is
exempt (``KNOWN_LIMITATIONS``), as its ``capabilities().typed_refusals`` says
(#726).
"""

from __future__ import annotations

import contextvars
import importlib
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
from donkey_kit.integrations import ADAPTERS

Call = Callable[[Donkey], Awaitable[None]]


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        on_model_substitution="raise",
        max_retries=0,
    )


# --- the surfaces ------------------------------------------------------------


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


async def _openai_agents(donkey: Donkey) -> None:
    agents = pytest.importorskip("agents")
    agent = agents.Agent(name="bridge", model=donkey.openai_agents.model("gpt-4o"))
    # Tracing off: the Agents SDK would otherwise export spans to OpenAI.
    await agents.Runner.run(agent, "hi", run_config=agents.RunConfig(tracing_disabled=True))


async def _llamaindex(donkey: Donkey) -> None:
    pytest.importorskip("llama_index.llms.openai_like")
    workflow = pytest.importorskip("llama_index.core.agent.workflow")
    agent = workflow.FunctionAgent(tools=[], llm=donkey.llamaindex.llm("gpt-4o"))
    await agent.run(user_msg="hi")


async def _adk_run(model: Any, *, streaming: bool = False) -> None:
    """One ``LlmAgent`` turn on ``model`` through ADK's ``InMemoryRunner``."""
    adk_agents = pytest.importorskip("google.adk.agents")
    run_config = pytest.importorskip("google.adk.agents.run_config")
    runners = pytest.importorskip("google.adk.runners")
    types = pytest.importorskip("google.genai.types")
    agent = adk_agents.LlmAgent(name="bridge", model=model)
    runner = runners.InMemoryRunner(agent=agent, app_name="bridge")
    session = await runner.session_service.create_session(app_name="bridge", user_id="u")
    message = types.Content(role="user", parts=[types.Part(text="hi")])
    mode = run_config.StreamingMode.SSE if streaming else run_config.StreamingMode.NONE
    config = run_config.RunConfig(streaming_mode=mode)
    async for _ in runner.run_async(
        user_id="u", session_id=session.id, new_message=message, run_config=config
    ):
        pass


async def _adk_gemini(donkey: Donkey) -> None:
    pytest.importorskip("google.adk.models")
    await _adk_run(donkey.adk.gemini("gemini-2.5-flash"))


async def _adk_model(donkey: Donkey) -> None:
    pytest.importorskip("google.adk.models.lite_llm")
    await _adk_run(donkey.adk.model("gpt-4o"))


async def _adk_model_streamed(donkey: Donkey) -> None:
    # LiteLlm awaits the same llm_client.acompletion for a streamed turn (#969).
    pytest.importorskip("google.adk.models.lite_llm")
    await _adk_run(donkey.adk.model("gpt-4o"), streaming=True)


async def _strands(donkey: Donkey) -> None:
    strands = pytest.importorskip("strands")
    pytest.importorskip("strands.models.openai")
    # The default retry strategy, as a user's Agent has it (#951).
    agent = strands.Agent(model=donkey.strands.model("gpt-4o"), callback_handler=None)
    await agent.invoke_async("hi")


async def _agent_framework(donkey: Donkey) -> None:
    af = pytest.importorskip("agent_framework")
    pytest.importorskip("agent_framework.openai")
    # No policy_middleware(): the run()/@governed scope alone bridges the refusal.
    agent = af.Agent(client=donkey.agent_framework.chat_client("gpt-4o"))
    await agent.run("hi")


SURFACES: dict[str, Call] = {
    "openai": _openai,
    "anthropic": _anthropic,
    "langgraph": _langgraph,
    "openai_agents": _openai_agents,
    "llamaindex": _llamaindex,
    "adk.gemini": _adk_gemini,
    "adk.model": _adk_model,
    "adk.model.streamed": _adk_model_streamed,
    "strands": _strands,
    "agent_framework": _agent_framework,
}


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
    # Only the adapter whose framework owns the transport is exempt: no
    # response or transport error of its ever passes through the SDK.
    exempt = {a for a, limits in KNOWN_LIMITATIONS.items() if "typed_refusal_bridged" in limits}
    assert exempt == {"crewai"}


#: The adapter factory behind each surface; the raw OpenAI client is no adapter.
_FACTORIES = {
    "anthropic": ("anthropic", "client"),
    "langgraph": ("langgraph", "chat_model"),
    "openai_agents": ("openai_agents", "model"),
    "llamaindex": ("llamaindex", "llm"),
    "adk.gemini": ("adk", "gemini"),
    "adk.model": ("adk", "model"),
    "adk.model.streamed": ("adk", "model"),
    "strands": ("strands", "model"),
    "agent_framework": ("agent_framework", "chat_client"),
}


def test_bridged_surfaces_declare_typed_refusals() -> None:
    # The factories that declare typed_refusals=True are exactly the surfaces
    # proven below, and only the exempt CrewAI declares False (#726, #983,
    # #969), so the claim and the test cannot drift.
    assert set(_FACTORIES) == set(SURFACES) - {"openai"}
    declared: dict[tuple[str, str], bool] = {}
    for attr, spec in ADAPTERS.items():
        module = importlib.import_module(spec.module, package="donkey_kit.integrations")
        for factory, caps in getattr(module, spec.cls).factories.items():
            declared[(attr, factory)] = caps.typed_refusals
    assert {key for key, typed in declared.items() if typed} == set(_FACTORIES.values())
    assert {key for key, typed in declared.items() if not typed} == {("crewai", "llm")}


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

    # The exact typed class. When a framework wrapped it on the way out, the
    # framework's own error is kept for inspection. openai>=3 (and the LangChain
    # client on top of it) talks to our transport through the httpx2 bridge and
    # lets a transport-raised typed error (GatewayUnavailable, ModelSubstituted)
    # through as it is, so there is no wrapper to keep; under openai<3 the same
    # case arrives as an APIConnectionError and the bridge unwraps it.
    # google-genai (adk.gemini()) lets a transport-raised typed error through on
    # every version. LiteLLM (adk.model()) never does: every case arrives as its
    # own error, kept on framework_error.
    assert type(excinfo.value) is error
    framework_error = excinfo.value.framework_error
    if error is PIIDetected:
        assert framework_error is not None
    assert not isinstance(framework_error, DonkeyError)


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
