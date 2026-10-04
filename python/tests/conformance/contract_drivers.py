"""How the adapter contract suite drives each factory's native object (#742).
Not a test module.

One :class:`Driver` per factory, keyed ``<adapter>.<factory>``. Each one makes
the framework's own call on the object the factory returns, the way a developer
would: a buffered async call, a streamed call and a sync call. A framework that
has no such call states why in ``no_stream`` / ``no_sync``, which the suite
asserts rather than skipping. Every call imports its framework lazily, so this
module loads on a base install.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from donkey_kit import Donkey

_PROMPT = "hi"
_CHAT = [{"role": "user", "content": _PROMPT}]

#: How a proxy refusal reaches the caller as the SDK's typed error. ``raised``:
#: the documented helper raises it (``typed_refusals()``, ``policy_middleware()``).
#: ``classify``: the framework raises its own error, and the gateway response it
#: carries (on it or its cause chain) classifies to the typed error.
Typed = Literal["raised", "classify"]


@dataclass(frozen=True)
class Driver:
    """The calls the contract suite makes through one adapter factory."""

    adapter: str
    factory: str
    #: The module the factory imports; the driver skips without it.
    probe: str
    #: One buffered async call, or the framework's only call shape.
    call: Callable[[Donkey], Awaitable[Any]]
    #: How a refusal from ``call`` reaches the caller as the typed error.
    typed: Typed
    stream: Callable[[Donkey], Awaitable[Any]] | None = None
    no_stream: str | None = None
    sync: Callable[[Donkey], Any] | None = None
    no_sync: str | None = None


# --- LangGraph ------------------------------------------------------------------


async def _langgraph_call(d: Donkey) -> None:
    with d.langgraph.typed_refusals():
        await d.langgraph.chat_model("m").ainvoke(_PROMPT)


async def _langgraph_stream(d: Donkey) -> None:
    async for _ in d.langgraph.chat_model("m").astream(_PROMPT):
        pass


def _langgraph_sync(d: Donkey) -> None:
    d.langgraph.chat_model("m").invoke(_PROMPT)


# --- Google ADK -----------------------------------------------------------------


def _llm_request(model: str) -> Any:
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    return LlmRequest(
        model=model, contents=[types.Content(role="user", parts=[types.Part(text=_PROMPT)])]
    )


async def _adk(model: Any, *, stream: bool = False) -> None:
    async for _ in model.generate_content_async(_llm_request(model.model), stream=stream):
        pass


# --- Strands ----------------------------------------------------------------------


async def _strands_call(d: Donkey) -> None:
    from strands import Agent

    agent = Agent(model=d.strands.model("m"), callback_handler=None, retry_strategy=None)
    await agent.invoke_async(_PROMPT)


async def _strands_stream(d: Donkey) -> None:
    async for _ in d.strands.model("m").stream([{"role": "user", "content": [{"text": _PROMPT}]}]):
        pass


def _strands_sync(d: Donkey) -> None:
    from strands import Agent

    Agent(model=d.strands.model("m"), callback_handler=None, retry_strategy=None)(_PROMPT)


# --- MS Agent Framework -------------------------------------------------------------


async def _maf_call(d: Donkey) -> None:
    from agent_framework import Agent

    agent = Agent(
        client=d.agent_framework.chat_client("m"),
        name="contract",
        middleware=[d.agent_framework.policy_middleware()],
    )
    await agent.run(_PROMPT)


async def _maf_stream(d: Donkey) -> None:
    async for _ in d.agent_framework.chat_client("m").get_response(_PROMPT, stream=True):
        pass


# --- OpenAI Agents SDK --------------------------------------------------------------


def _agents_args() -> dict[str, Any]:
    from agents import ModelSettings
    from agents.models.interface import ModelTracing

    return {
        "system_instructions": None,
        "input": _PROMPT,
        "model_settings": ModelSettings(),
        "tools": [],
        "output_schema": None,
        "handoffs": [],
        "tracing": ModelTracing.DISABLED,
    }


async def _agents_call(d: Donkey) -> None:
    await d.openai_agents.model("m").get_response(**_agents_args())


async def _agents_stream(d: Donkey) -> None:
    async for _ in d.openai_agents.model("m").stream_response(**_agents_args()):
        pass


# --- Anthropic ----------------------------------------------------------------------


async def _anthropic_call(d: Donkey) -> None:
    await d.anthropic.client().messages.create(model="m", max_tokens=8, messages=_CHAT)


async def _anthropic_stream(d: Donkey) -> None:
    stream = await d.anthropic.client().messages.create(
        model="m", max_tokens=8, messages=_CHAT, stream=True
    )
    async for _ in stream:
        pass


# --- CrewAI -------------------------------------------------------------------------


async def _crewai_call(d: Donkey) -> None:
    await d.crewai.llm("m").acall(_PROMPT)


async def _crewai_stream(d: Donkey) -> None:
    await d.crewai.llm("m", stream=True).acall(_PROMPT)


def _crewai_sync(d: Donkey) -> None:
    d.crewai.llm("m").call(_PROMPT)


# --- LlamaIndex ---------------------------------------------------------------------


async def _llamaindex_call(d: Donkey) -> None:
    with d.llamaindex.typed_refusals():
        await d.llamaindex.llm("m").acomplete(_PROMPT)


async def _llamaindex_stream(d: Donkey) -> None:
    from llama_index.core.llms import ChatMessage

    stream = await d.llamaindex.llm("m").astream_chat([ChatMessage(role="user", content=_PROMPT)])
    async for _ in stream:
        pass


def _llamaindex_sync(d: Donkey) -> None:
    d.llamaindex.llm("m").complete(_PROMPT)


_ASYNC_ONLY = "{fw} has no sync call: its model interface is async-only."

DRIVERS: dict[str, Driver] = {
    driver.adapter + "." + driver.factory: driver
    for driver in [
        Driver(
            "langgraph", "chat_model", "langchain_openai", _langgraph_call, "raised",
            stream=_langgraph_stream, sync=_langgraph_sync,
        ),
        Driver(
            "adk", "model", "google.adk.models.lite_llm",
            lambda d: _adk(d.adk.model("m")), "classify",
            stream=lambda d: _adk(d.adk.model("m"), stream=True),
            no_sync=_ASYNC_ONLY.format(fw="ADK's BaseLlm"),
        ),
        Driver(
            "adk", "gemini", "google.adk.models",
            lambda d: _adk(d.adk.gemini("gemini-2.5-flash")), "classify",
            stream=lambda d: _adk(d.adk.gemini("gemini-2.5-flash"), stream=True),
            no_sync=_ASYNC_ONLY.format(fw="ADK's BaseLlm"),
        ),
        Driver(
            "strands", "model", "strands.models.openai", _strands_call, "classify",
            stream=_strands_stream, sync=_strands_sync,
        ),
        Driver(
            "agent_framework", "chat_client", "agent_framework.openai", _maf_call, "raised",
            stream=_maf_stream, no_sync=_ASYNC_ONLY.format(fw="Agent Framework's chat client"),
        ),
        Driver(
            "openai_agents", "model", "agents", _agents_call, "classify",
            stream=_agents_stream, no_sync=_ASYNC_ONLY.format(fw="The Agents SDK's Model"),
        ),
        Driver(
            "anthropic", "client", "anthropic", _anthropic_call, "classify",
            stream=_anthropic_stream,
            no_sync="donkey.anthropic.client() returns AsyncAnthropic only; there is no "
            "sync factory.",
        ),
        Driver(
            "crewai", "llm", "crewai", _crewai_call, "classify",
            stream=_crewai_stream, sync=_crewai_sync,
        ),
        Driver(
            "llamaindex", "llm", "llama_index.llms.openai_like", _llamaindex_call, "raised",
            stream=_llamaindex_stream, sync=_llamaindex_sync,
        ),
    ]
}

