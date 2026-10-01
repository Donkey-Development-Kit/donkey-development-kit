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
  :class:`~donkey_kit.core.lastcall.LastCallBridge`. Bridging the whole run
  instead would break hazard #2, since a run can fan out (agents as tools, ADK's
  ``ParallelAgent``). The gap is documented on the framework pages, with the
  in-task hook that reads the record (``RunHooks.on_llm_end``,
  ``after_model_callback``). The ``UNOBSERVED`` assertions below pin that
  documented behaviour: if one starts failing, the framework changed where it
  sends the call, and the page needs updating.

Framework-free at module level (the base-only job); each test skips unless its
framework is installed. CI runs this file in the jobs that install them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from _anthropic_wire import BASE_URL as ANTHROPIC_BASE_URL
from _anthropic_wire import BODY as ANTHROPIC_BODY
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
    donkey._http._swap_transport(httpx.MockTransport(handler))
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
        request = LlmRequest(model="gemini-2.5-flash", contents=[_gemini_message()])
        async for _ in donkey.adk.gemini("gemini-2.5-flash").generate_content_async(request):
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
            model=donkey.adk.gemini("gemini-2.5-flash"),
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
