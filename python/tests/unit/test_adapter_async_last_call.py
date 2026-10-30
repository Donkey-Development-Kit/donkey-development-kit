"""``donkey.last_call`` after one async call through each shared-transport adapter
(#899, follows #850).

``last_call`` is contextvar-scoped, so a framework that sends a call from a task
of its own records it in that task's copy of the context and the caller reads
``UNOBSERVED``. #893 bridges LangChain's ``ainvoke``. These tests check the other
adapters that hand the framework the SDK's shared client (``BG §1.1``,
``BG §1.8``), each through the framework's own entry point:

* The model-level calls (``OpenAIChatCompletionsModel.get_response``, Strands
  ``Agent.invoke_async``, ``AsyncAnthropic.messages.create``, ADK
  ``Gemini.generate_content_async``) await the request in the caller's task, so
  the caller reads ``OBSERVED``.
* The OpenAI Agents SDK ``Runner`` sends a run's first turn from a task of its
  own, and ADK's ``Runner`` runs the whole agent in one. Neither runs anything
  the adapter controls in the caller's context, so there is nowhere to open a
  :class:`~donkey_kit.core.lastcall.LastCallBridge` for the caller. Bridging the
  whole run instead would break hazard #2, since a run can fan out (agents as
  tools, ADK's ``ParallelAgent``). The gap is documented on the framework pages,
  with the in-task hook that reads the record (``RunHooks.on_llm_end``,
  ``after_model_callback``). The ``UNOBSERVED`` assertions below pin that
  documented behaviour: if one starts failing, the framework changed where it
  sends the call, and the page needs updating.
* From google-adk 2.13.0, ADK's flow iterates the model's response generator in
  a copy of its own context, so both ADK factories return a model subclass that
  bridges each model call back to the flow, where ``after_model_callback`` runs
  (#1207). The callback tests below pin that for both factories. The bridge
  closes before a complete response, so a task started on one (as ADK starts
  tool calls) records in its own context (hazard #2).

Framework-free at module level (the base-only job); each test skips unless its
framework is installed. CI runs this file in the jobs that install them.
"""

from __future__ import annotations

import asyncio
import json
import pickle
from pathlib import Path
from typing import Any

import httpx
import pytest
from _anthropic_wire import BASE_URL as ANTHROPIC_BASE_URL
from _anthropic_wire import BODY as ANTHROPIC_BODY
from _anthropic_wire import Chunks
from _anthropic_wire import success_response as anthropic_success

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.lastcall import LastCallStatus
from donkey_kit.simulator.fixtures import parse_headers

GEMINI_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "gemini_inbound"


def _completion(request_id: str, message: dict[str, Any], finish_reason: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": request_id},
        json={
            "id": request_id,
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def _answer(request: httpx.Request) -> httpx.Response:
    return _completion("rid-answer", {"role": "assistant", "content": "PONG"}, "stop")


def _tool_then_answer(request: httpx.Request) -> httpx.Response:
    """A tool call first, then the answer once the tool's result is sent back."""
    messages = json.loads(request.content).get("messages", [])
    if any(m.get("role") == "tool" for m in messages):
        return _answer(request)
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": "{}"},
    }
    return _completion(
        "rid-tool",
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        "tool_calls",
    )


def _gemini(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        content=(GEMINI_FIXTURES / "responses.success.body.json").read_bytes(),
        headers=parse_headers((GEMINI_FIXTURES / "responses.success.headers.txt").read_text()),
    )


def _donkey(handler: Any = _answer) -> Donkey:
    donkey = Donkey(
        DonkeyConfig(
            llm_proxy_url="https://proxy/p",
            llm_proxy_client_id="cid",
            llm_proxy_client_secret="csecret",
        )
    )
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(handler))
    return donkey


# --- OpenAI Agents SDK ----------------------------------------------------------


def _run_config() -> Any:
    from agents import RunConfig

    return RunConfig(tracing_disabled=True)


async def test_openai_agents_model_call_is_observed() -> None:
    pytest.importorskip("agents")
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    donkey = _donkey()
    async with donkey:
        await donkey.openai_agents.model("gpt-4o").get_response(
            system_instructions=None,
            input="hi",
            model_settings=ModelSettings(),
            tools=[],
            output_schema=None,
            handoffs=[],
            tracing=ModelTracing.DISABLED,
        )
        assert donkey.last_call.status is LastCallStatus.OBSERVED


async def test_openai_agents_runner_first_turn_is_unobserved_but_on_llm_end_sees_it() -> None:
    pytest.importorskip("agents")
    from agents import Agent, RunHooks, Runner

    donkey = _donkey()
    in_hook: list[LastCallStatus] = []

    class Hooks(RunHooks[Any]):
        async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
            in_hook.append(donkey.last_call.status)

    async with donkey:
        agent = Agent(name="a", model=donkey.openai_agents.model("gpt-4o"))
        await Runner.run(agent, "hi", hooks=Hooks(), run_config=_run_config())
        # Documented gap: the first turn runs in a task Runner spawns.
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED
    assert in_hook == [LastCallStatus.OBSERVED]


async def test_openai_agents_runner_reports_a_later_turn() -> None:
    pytest.importorskip("agents")
    from agents import Agent, Runner, function_tool

    @function_tool
    def get_weather() -> str:
        """The weather."""
        return "sunny"

    donkey = _donkey(_tool_then_answer)
    async with donkey:
        agent = Agent(name="a", model=donkey.openai_agents.model("gpt-4o"), tools=[get_weather])
        await Runner.run(agent, "hi", run_config=_run_config())
        # Turns after the first run in the caller's task.
        assert donkey.last_call.request_id == "rid-answer"


async def test_openai_agents_streamed_run_is_unobserved() -> None:
    pytest.importorskip("agents")
    from agents import Agent, Runner

    donkey = _donkey()
    async with donkey:
        agent = Agent(name="a", model=donkey.openai_agents.model("gpt-4o"))
        result = Runner.run_streamed(agent, "hi", run_config=_run_config())
        async for _ in result.stream_events():
            pass
        # Documented gap: run_streamed runs the whole loop in a task of its own.
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED


# --- Strands --------------------------------------------------------------------


async def test_strands_agent_invoke_async_is_observed() -> None:
    pytest.importorskip("strands")
    from strands import Agent

    donkey = _donkey()
    async with donkey:
        agent = Agent(model=donkey.strands.model("gpt-4o"), callback_handler=None)
        await agent.invoke_async("hi")
        assert donkey.last_call.status is LastCallStatus.OBSERVED


# --- Anthropic ------------------------------------------------------------------


async def test_anthropic_messages_create_is_observed() -> None:
    pytest.importorskip("anthropic")
    donkey = _donkey(lambda request: anthropic_success())
    async with donkey:
        client = donkey.anthropic.client(base_url=ANTHROPIC_BASE_URL)
        await client.messages.create(**ANTHROPIC_BODY)
        assert donkey.last_call.status is LastCallStatus.OBSERVED


# --- ADK gemini() -----------------------------------------------------------------


def _gemini_message() -> Any:
    from google.genai import types

    return types.Content(role="user", parts=[types.Part(text="hi")])


async def test_adk_gemini_model_call_is_observed() -> None:
    pytest.importorskip("google.adk")
    from google.adk.models.llm_request import LlmRequest

    donkey = _donkey(_gemini)
    async with donkey:
        request = LlmRequest(model="gemini-3.8-flash", contents=[_gemini_message()])
        async for _ in donkey.adk.gemini("gemini-3.8-flash").generate_content_async(request):
            pass
        assert donkey.last_call.status is LastCallStatus.OBSERVED


async def test_adk_runner_is_unobserved_but_after_model_callback_sees_it() -> None:
    pytest.importorskip("google.adk")
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner

    donkey = _donkey(_gemini)
    in_callback: list[LastCallStatus] = []

    def after_model(callback_context: Any, llm_response: Any) -> None:
        in_callback.append(donkey.last_call.status)

    async with donkey:
        agent = LlmAgent(
            name="a",
            model=donkey.adk.gemini("gemini-3.8-flash"),
            after_model_callback=after_model,
        )
        runner = InMemoryRunner(agent=agent, app_name="app")
        session = await runner.session_service.create_session(app_name="app", user_id="u")
        async for _ in runner.run_async(
            user_id="u", session_id=session.id, new_message=_gemini_message()
        ):
            pass
        # Documented gap: ADK's Runner runs the agent in a task of its own.
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED
    assert in_callback == [LastCallStatus.OBSERVED]


async def test_adk_model_runner_is_unobserved_but_after_model_callback_sees_it() -> None:
    pytest.importorskip("google.adk")
    pytest.importorskip("litellm")
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner

    donkey = _donkey()
    in_callback: list[str | None] = []

    def after_model(callback_context: Any, llm_response: Any) -> None:
        in_callback.append(donkey.last_call.request_id)

    async with donkey:
        agent = LlmAgent(
            name="a",
            model=donkey.adk.model("gpt-4o"),
            after_model_callback=after_model,
        )
        runner = InMemoryRunner(agent=agent, app_name="app")
        session = await runner.session_service.create_session(app_name="app", user_id="u")
        async for _ in runner.run_async(
            user_id="u", session_id=session.id, new_message=_gemini_message()
        ):
            pass
        # Documented gap: ADK's Runner runs the agent in a task of its own.
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED
    assert in_callback == ["rid-answer"]


async def test_adk_model_runner_with_a_tool_reaches_the_answer() -> None:
    """A tool-bearing request takes litellm 1.106+ through its MCP handler,
    which imports ``mcp`` at module level; without it the run fails with "No
    module named 'mcp'" (#1267)."""
    pytest.importorskip("google.adk")
    pytest.importorskip("litellm")
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner

    def get_weather() -> str:
        """The weather."""
        return "sunny"

    donkey = _donkey(_tool_then_answer)
    texts: list[str] = []
    async with donkey:
        agent = LlmAgent(name="a", model=donkey.adk.model("gpt-4o"), tools=[get_weather])
        runner = InMemoryRunner(agent=agent, app_name="app")
        session = await runner.session_service.create_session(app_name="app", user_id="u")
        async for event in runner.run_async(
            user_id="u", session_id=session.id, new_message=_gemini_message()
        ):
            texts += [p.text for p in (event.content.parts if event.content else []) if p.text]
    assert texts == ["PONG"]


def _sse_chunks(body: bytes, headers: dict[str, str]) -> httpx.Response:
    """An SSE body delivered one event at a time, as from the network. A
    ``content=`` body is read when the response is built, so the transport's
    stream wrapper never scans it and usage stays ``None`` (#1219)."""
    events = [event + b"\n\n" for event in body.split(b"\n\n") if event.strip()]
    return httpx.Response(200, headers=headers, stream=Chunks(events))


def _gemini_stream(request: httpx.Request) -> httpx.Response:
    return _sse_chunks(
        (GEMINI_FIXTURES / "responses.stream.body.txt").read_bytes(),
        parse_headers((GEMINI_FIXTURES / "responses.stream.headers.txt").read_text()),
    )


def _chat_stream(request: httpx.Request) -> httpx.Response:
    """A streamed chat completion whose last event carries the usage."""
    chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "gpt-4o"}
    events = [
        {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "PO"}}]},
        {**chunk, "choices": [{"index": 0, "delta": {"content": "NG"}, "finish_reason": "stop"}]},
        {
            **chunk,
            "choices": [],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    ]
    body = b"".join(f"data: {json.dumps(e)}\n\n".encode() for e in events) + b"data: [DONE]\n\n"
    return _sse_chunks(body, {"content-type": "text/event-stream", "x-request-id": "rid-stream"})


async def _streamed_callbacks(donkey: Donkey, model: Any) -> list[tuple[bool, int | None]]:
    """``(partial, donkey.last_call.total_tokens)`` in each ``after_model_callback``
    of one streamed Runner run, after checking every callback reads ``OBSERVED``."""
    from google.adk.agents import LlmAgent
    from google.adk.agents.run_config import RunConfig, StreamingMode
    from google.adk.runners import InMemoryRunner

    in_callback: list[tuple[bool, int | None]] = []

    def after_model(callback_context: Any, llm_response: Any) -> None:
        assert donkey.last_call.status is LastCallStatus.OBSERVED
        in_callback.append((bool(llm_response.partial), donkey.last_call.total_tokens))

    agent = LlmAgent(name="a", model=model, after_model_callback=after_model)
    runner = InMemoryRunner(agent=agent, app_name="app")
    session = await runner.session_service.create_session(app_name="app", user_id="u")
    async for _ in runner.run_async(
        user_id="u",
        session_id=session.id,
        new_message=_gemini_message(),
        run_config=RunConfig(streaming_mode=StreamingMode.SSE),
    ):
        pass
    return in_callback


async def test_adk_streamed_run_after_model_callback_sees_every_response() -> None:
    """The bridge stays open across partial responses and its record survives
    the close, so each callback, the final complete one included, reads it. The
    usage lands once the stream ends, so only the complete response's callback
    reads it (#1219): the fixture's last event reports 38 tokens."""
    pytest.importorskip("google.adk")
    donkey = _donkey(_gemini_stream)
    async with donkey:
        in_callback = await _streamed_callbacks(donkey, donkey.adk.gemini("gemini-3.8-flash"))
    assert in_callback == [(True, None)] * 3 + [(False, 38)]


async def test_adk_model_streamed_run_usage_lands_on_the_complete_response() -> None:
    pytest.importorskip("google.adk")
    pytest.importorskip("litellm")
    donkey = _donkey(_chat_stream)
    async with donkey:
        in_callback = await _streamed_callbacks(donkey, donkey.adk.model("gpt-4o"))
    assert in_callback == [(True, None)] * 2 + [(False, 5)]


async def test_adk_hand_built_models_reach_after_model_callback_when_bridged() -> None:
    """A model built from the governed kwargs reads ``UNOBSERVED`` in its
    callback on google-adk >= 2.13 unless its class is bridged (#1218)."""
    pytest.importorskip("google.adk")
    pytest.importorskip("litellm")
    from google.adk.agents import LlmAgent
    from google.adk.models import Gemini
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import InMemoryRunner

    from donkey_kit.integrations.adk import last_call_bridged

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return _answer(request)
        return _gemini(request)

    donkey = _donkey(handler)
    in_callback: list[LastCallStatus] = []

    def after_model(callback_context: Any, llm_response: Any) -> None:
        in_callback.append(donkey.last_call.status)

    async def run(llm: Any) -> None:
        agent = LlmAgent(name="a", model=llm, after_model_callback=after_model)
        runner = InMemoryRunner(agent=agent, app_name="app")
        session = await runner.session_service.create_session(app_name="app", user_id="u")
        async for _ in runner.run_async(
            user_id="u", session_id=session.id, new_message=_gemini_message()
        ):
            pass

    async with donkey:
        lite = last_call_bridged(LiteLlm)
        await run(lite(model="openai/gpt-4o", **donkey.adk.connection_kwargs()))
        gemini = last_call_bridged(Gemini)
        await run(gemini(model="gemini-3.8-flash", **donkey.adk.gemini_connection_kwargs()))
    assert in_callback == [LastCallStatus.OBSERVED] * 2
    assert issubclass(lite, LiteLlm) and issubclass(gemini, Gemini)


def test_last_call_bridged_builds_one_class_and_takes_only_adk_models() -> None:
    pytest.importorskip("google.adk")
    from google.adk.models import Gemini

    from donkey_kit.integrations.adk import last_call_bridged

    bridged = last_call_bridged(Gemini)
    assert last_call_bridged(Gemini) is bridged
    # Already bridged, as the factories' classes are: not bridged twice.
    assert last_call_bridged(bridged) is bridged
    donkey = _donkey(_gemini)
    assert last_call_bridged(type(donkey.adk.gemini("gemini-3.8-flash"))) is bridged

    class Mine(Gemini):
        pass

    mine = last_call_bridged(Mine)
    assert issubclass(mine, Mine) and mine is not bridged
    for wrong in (object, "Gemini", Gemini(model="gemini-3.8-flash")):
        with pytest.raises(TypeError, match="BaseLlm"):
            last_call_bridged(wrong)  # type: ignore[arg-type]


def test_the_bridge_marker_is_a_plain_class_attribute() -> None:
    """Not a pydantic private attribute, which is a truthy ``ModelPrivateAttr``."""
    pytest.importorskip("google.adk")
    pytest.importorskip("litellm")
    from google.adk.models import Gemini
    from google.adk.models.lite_llm import LiteLlm

    from donkey_kit.integrations.adk import last_call_bridged

    for base in (Gemini, LiteLlm):
        bridged = last_call_bridged(base)
        assert bridged.__dict__["_donkey_last_call_bridged"] is True
        assert "_donkey_last_call_bridged" not in bridged.__private_attributes__


async def test_the_factory_classes_resolve_by_name_so_they_pickle() -> None:
    pytest.importorskip("google.adk")
    pytest.importorskip("litellm")
    from google.adk.models import Gemini
    from google.adk.models.lite_llm import LiteLlm

    from donkey_kit.integrations import adk

    donkey = _donkey()
    async with donkey:
        lite = donkey.adk.model("gpt-4o")
        gemini = donkey.adk.gemini("gemini-3.8-flash")
    assert type(lite) is adk.GovernedLiteLlm
    assert type(lite.llm_client) is adk.GovernedLiteLLMClient
    assert type(gemini) is adk.GovernedGemini
    for cls in (adk.GovernedLiteLlm, adk.GovernedGemini, adk.GovernedLiteLLMClient):
        assert pickle.loads(pickle.dumps(cls)) is cls
    plain = adk.last_call_bridged(LiteLlm)(model="openai/gpt-4o")
    assert type(pickle.loads(pickle.dumps(plain))) is adk.GovernedLiteLlm
    assert adk.last_call_bridged(Gemini) is adk.GovernedGemini
    with pytest.raises(AttributeError, match="GovernedNothing"):
        adk.GovernedNothing  # noqa: B018


async def test_adk_factories_return_the_adk_classes() -> None:
    pytest.importorskip("google.adk")
    pytest.importorskip("litellm")
    from google.adk.models import Gemini
    from google.adk.models.lite_llm import LiteLlm

    donkey = _donkey()
    async with donkey:
        assert isinstance(donkey.adk.gemini("gemini-3.8-flash"), Gemini)
        assert isinstance(donkey.adk.model("gpt-4o"), LiteLlm)


async def test_adk_bridge_closes_before_a_complete_response() -> None:
    """A task started while ADK handles a complete response, as ADK starts a tool
    call, records in its own context and not through the model call's bridge."""
    pytest.importorskip("google.adk")
    from google.adk.models.llm_request import LlmRequest

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return _answer(request)
        return _gemini(request)

    donkey = _donkey(handler)

    async def sibling() -> None:
        await donkey.llm.client().chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

    async with donkey:
        request = LlmRequest(model="gemini-3.8-flash", contents=[_gemini_message()])
        model = donkey.adk.gemini("gemini-3.8-flash")
        async for response in model.generate_content_async(request):
            if not response.partial:
                await asyncio.create_task(sibling())
        assert donkey.last_call.requested_model == "gemini-3.8-flash"
