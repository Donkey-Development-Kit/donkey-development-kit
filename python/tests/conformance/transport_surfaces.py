"""One governed model call per adapter factory that sends through the SDK's
transport (#1441). Not a test module.

The transport scenarios (``typed_refusal_bridged``,
``refusal_handler_cannot_unrefuse``, ``custom_policy_typed``,
``agent_killed_stops_cleanly``) run on every surface here, so the README's
"every other adapter" claims are backed by a run on each adapter rather than
on the deep ones only. :func:`transport_factories` is the set those claims are
about: every factory whose ``capabilities().transport`` is not ``"framework"``.
Each scenario file checks that its surfaces cover it, so an adapter added to
``ADAPTERS`` fails until it gets a surface or an asserted exemption.

Each surface is the framework's own call, made the way a developer would:

* the raw OpenAI client and the Anthropic client;
* a LangGraph graph, an OpenAI Agents SDK ``Runner`` run and a LlamaIndex
  ``FunctionAgent`` run;
* an ADK ``LlmAgent`` turn on ``adk.gemini()`` and on ``adk.model()``;
* a Strands ``Agent`` and an Agent Framework ``Agent``.

Every call ``importorskip``s its framework, so this module loads on a base
install. :func:`ok` answers a request with the contract gateway's route-shaped
success, so a handler's re-send parses on every framework.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

# Sibling module (tests/conformance/ is not a package).
from contract_gateway import success

from donkey_kit import Donkey
from donkey_kit.integrations import ADAPTERS

Call = Callable[[Donkey], Awaitable[None]]


async def raw_openai(donkey: Donkey) -> None:
    """The raw OpenAI client, ``donkey.openai()``."""
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
    agent = agents.Agent(name="conformance", model=donkey.openai_agents.model("gpt-4o"))
    # Tracing off: the Agents SDK would otherwise export spans to OpenAI.
    await agents.Runner.run(agent, "hi", run_config=agents.RunConfig(tracing_disabled=True))


async def _llamaindex(donkey: Donkey) -> None:
    pytest.importorskip("llama_index.llms.openai_like")
    workflow = pytest.importorskip("llama_index.core.agent.workflow")
    agent = workflow.FunctionAgent(tools=[], llm=donkey.llamaindex.llm("gpt-4o"))
    await agent.run(user_msg="hi")


async def adk_run(model: Any, *, streaming: bool = False) -> None:
    """One ``LlmAgent`` turn on ``model`` through ADK's ``InMemoryRunner``."""
    adk_agents = pytest.importorskip("google.adk.agents")
    run_config = pytest.importorskip("google.adk.agents.run_config")
    runners = pytest.importorskip("google.adk.runners")
    types = pytest.importorskip("google.genai.types")
    agent = adk_agents.LlmAgent(name="conformance", model=model)
    runner = runners.InMemoryRunner(agent=agent, app_name="conformance")
    session = await runner.session_service.create_session(app_name="conformance", user_id="u")
    message = types.Content(role="user", parts=[types.Part(text="hi")])
    mode = run_config.StreamingMode.SSE if streaming else run_config.StreamingMode.NONE
    config = run_config.RunConfig(streaming_mode=mode)
    async for _ in runner.run_async(
        user_id="u", session_id=session.id, new_message=message, run_config=config
    ):
        pass


async def _adk_gemini(donkey: Donkey) -> None:
    pytest.importorskip("google.adk.models")
    await adk_run(donkey.adk.gemini("gemini-3.8-flash"))


async def _adk_model(donkey: Donkey) -> None:
    pytest.importorskip("google.adk.models.lite_llm")
    await adk_run(donkey.adk.model("gpt-4o"))


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
    "openai": raw_openai,
    "anthropic": _anthropic,
    "langgraph": _langgraph,
    "openai_agents": _openai_agents,
    "llamaindex": _llamaindex,
    "adk.gemini": _adk_gemini,
    "adk.model": _adk_model,
    "strands": _strands,
    "agent_framework": _agent_framework,
}

#: The adapter factory behind each surface; the raw OpenAI client is no adapter.
FACTORIES: dict[str, tuple[str, str]] = {
    "anthropic": ("anthropic", "client"),
    "langgraph": ("langgraph", "chat_model"),
    "openai_agents": ("openai_agents", "model"),
    "llamaindex": ("llamaindex", "llm"),
    "adk.gemini": ("adk", "gemini"),
    "adk.model": ("adk", "model"),
    "strands": ("strands", "model"),
    "agent_framework": ("agent_framework", "chat_client"),
}

#: Surfaces whose request carries the model in a JSON body, so a
#: ``fallback(model=...)`` handler can re-send it. A native Gemini request
#: names the model in its URL path, so the refusal propagates instead.
MODEL_IN_BODY = frozenset(SURFACES) - {"adk.gemini"}


def transport_factories() -> set[tuple[str, str]]:
    """Every ``(adapter, factory)`` in ``ADAPTERS`` whose calls go through the
    SDK's transport: the factories the transport scenarios must each run."""
    shared: set[tuple[str, str]] = set()
    for attr, spec in ADAPTERS.items():
        module = importlib.import_module(spec.module, package="donkey_kit.integrations")
        for factory, caps in getattr(module, spec.cls).factories.items():
            if caps.transport != "framework":
                shared.add((attr, factory))
    return shared


def framework_transport_adapters() -> set[str]:
    """The adapters with a factory whose framework owns the transport: the
    only ones a transport scenario may exempt."""
    owned: set[str] = set()
    for attr, spec in ADAPTERS.items():
        module = importlib.import_module(spec.module, package="donkey_kit.integrations")
        if any(c.transport == "framework" for c in getattr(module, spec.cls).factories.values()):
            owned.add(attr)
    return owned


def ok(request: httpx.Request) -> httpx.Response:
    """A gateway that allows ``request``: the route-shaped success the contract
    gateway replays for its path, streamed when the body asks for a stream."""
    status, headers, payload = success(request.url.path, request.content)
    return httpx.Response(status, headers=headers, content=payload)
