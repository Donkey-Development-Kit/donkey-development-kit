"""LlamaIndex, MS Agent Framework and ADK ``model()`` send through the shared
client, so their calls carry the run's correlation id, populate
``donkey.last_call`` and refuse as typed errors (#740).

Each test drives the real framework against a ``MockTransport`` (or
``donkey.simulate()``), through the framework's own entry point, sync and
streaming where the framework has them. These retire the
``correlation_id_propagated`` and ``gateway_identity_observed`` exemptions the
three adapters used to record (``tests/conformance/suite.py``).

Framework-free at module level (the base-only job); each test skips unless its
framework is installed. CI runs this file in the jobs that install them.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import lastcall
from donkey_kit.core.errors import PIIDetected
from donkey_kit.core.lastcall import LastCallStatus

_CORRELATION_HEADER = "x-correlation-id"
_RUN_ID = "run-740"


@pytest.fixture(autouse=True)
def _reset_last_call() -> Iterator[None]:
    """Clear the record so a cold read is cold whatever an earlier test in this
    process left in the context the async tests copy."""
    token = lastcall._last_call.set(None)
    try:
        yield
    finally:
        lastcall._last_call.reset(token)


def _chat(request: httpx.Request) -> httpx.Response:
    if json.loads(request.content or b"{}").get("stream"):
        chunks = [
            {"role": "assistant", "content": "PONG"},
            {},
        ]
        body = "".join(
            "data: "
            + json.dumps(
                {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "m",
                    "choices": [
                        {"index": 0, "delta": delta, "finish_reason": None if delta else "stop"}
                    ],
                }
            )
            + "\n\n"
            for delta in chunks
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "x-request-id": "rid"},
            content=(body + "data: [DONE]\n\n").encode(),
        )
    return httpx.Response(
        200,
        headers={"x-request-id": "rid"},
        json={
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "PONG"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def _responses() -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-request-id": "rid"},
        json={
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "model": "m",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "id": "msg_1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "PONG", "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "parallel_tool_calls": False,
            "tool_choice": "auto",
            "tools": [],
        },
    )


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy/p",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        correlation_header=_CORRELATION_HEADER,
    )


def _donkey(seen: list[httpx.Request]) -> Donkey:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _responses() if request.url.path.endswith("/responses") else _chat(request)

    donkey = Donkey(_cfg())
    for client in http_clients(donkey):
        swap_transport(client, httpx.MockTransport(handler))
    return donkey


# --- the calls, one per framework entry point -----------------------------------


async def _llamaindex_acomplete(donkey: Donkey) -> None:
    await donkey.llamaindex.llm("m").acomplete("hi")


async def _llamaindex_astream_chat(donkey: Donkey) -> None:
    from llama_index.core.llms import ChatMessage

    stream = await donkey.llamaindex.llm("m").astream_chat([ChatMessage(role="user", content="hi")])
    async for _ in stream:
        pass


async def _llamaindex_sync_chat(donkey: Donkey) -> None:
    from llama_index.core.llms import ChatMessage

    donkey.llamaindex.llm("m").chat([ChatMessage(role="user", content="hi")])


async def _llamaindex_sync_stream_chat(donkey: Donkey) -> None:
    from llama_index.core.llms import ChatMessage

    for _ in donkey.llamaindex.llm("m").stream_chat([ChatMessage(role="user", content="hi")]):
        pass


async def _maf_responses(donkey: Donkey) -> None:
    await donkey.agent_framework.chat_client("m").get_response("hi")


async def _maf_chat_completions_stream(donkey: Donkey) -> None:
    client = donkey.agent_framework.chat_client("m", api="chat_completions")
    async for _ in client.get_response("hi", stream=True):
        pass


async def _maf_agent_run(donkey: Donkey) -> None:
    from agent_framework import Agent

    client = donkey.agent_framework.chat_client("m", api="chat_completions")
    await Agent(client=client, name="a").run("hi")


def _llm_request() -> Any:
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    return LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="hi")])])


async def _adk_model(donkey: Donkey) -> None:
    async for _ in donkey.adk.model("m").generate_content_async(_llm_request()):
        pass


async def _adk_model_stream(donkey: Donkey) -> None:
    async for _ in donkey.adk.model("m").generate_content_async(_llm_request(), stream=True):
        pass


_CASES: list[tuple[str, str, Callable[[Donkey], Awaitable[None]]]] = [
    ("llama_index.llms.openai_like", "acomplete", _llamaindex_acomplete),
    ("llama_index.llms.openai_like", "astream_chat", _llamaindex_astream_chat),
    ("llama_index.llms.openai_like", "chat (sync)", _llamaindex_sync_chat),
    ("llama_index.llms.openai_like", "stream_chat (sync)", _llamaindex_sync_stream_chat),
    ("agent_framework", "responses", _maf_responses),
    ("agent_framework", "chat_completions stream", _maf_chat_completions_stream),
    ("agent_framework", "Agent.run", _maf_agent_run),
    ("google.adk.models.lite_llm", "model()", _adk_model),
    ("google.adk.models.lite_llm", "model() stream", _adk_model_stream),
]


@pytest.mark.parametrize(
    ("module", "call"), [(m, c) for m, _, c in _CASES], ids=[f"{m}:{n}" for m, n, _ in _CASES]
)
async def test_call_carries_run_correlation_and_is_observed(
    module: str, call: Callable[[Donkey], Awaitable[None]]
) -> None:
    pytest.importorskip(module)
    seen: list[httpx.Request] = []
    async with _donkey(seen) as donkey:
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED  # cold, not UNAVAILABLE
        async with donkey.run(id=_RUN_ID):
            await call(donkey)
            assert donkey.last_call.status is LastCallStatus.OBSERVED
            assert donkey.last_call.request_id == "rid"
    assert len(seen) == 1
    assert seen[0].headers[_CORRELATION_HEADER] == _RUN_ID


# --- typed refusals under simulate() ---------------------------------------------


@pytest.mark.parametrize("sync", [False, True], ids=["async", "sync"])
async def test_llamaindex_typed_refusals_raises_the_typed_error(sync: bool) -> None:
    pytest.importorskip("llama_index.llms.openai_like")
    async with Donkey(_cfg()) as donkey, donkey.run(id=_RUN_ID):
        llm = donkey.llamaindex.llm("m")
        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as exc_info:
            with donkey.llamaindex.typed_refusals():
                if sync:
                    llm.complete("hi")
                else:
                    await llm.acomplete("hi")
        assert donkey.last_call.status is LastCallStatus.OBSERVED
    assert exc_info.value.correlation_id == _RUN_ID
    assert exc_info.value.framework_error is not None


def test_llamaindex_module_typed_refusals_passes_other_errors_through() -> None:
    pytest.importorskip("llama_index.llms.openai_like")  # the helper imports openai
    from donkey_kit.integrations.llamaindex import typed_refusals

    with pytest.raises(ValueError, match="not a refusal"), typed_refusals():
        raise ValueError("not a refusal")


async def test_agent_framework_policy_middleware_raises_the_typed_error() -> None:
    pytest.importorskip("agent_framework")
    from agent_framework import Agent

    async with Donkey(_cfg()) as donkey, donkey.run(id=_RUN_ID):
        agent = Agent(
            client=donkey.agent_framework.chat_client("m"),
            name="a",
            middleware=[donkey.agent_framework.policy_middleware()],
        )
        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as exc_info:
            await agent.run("hi")
        assert donkey.last_call.status is LastCallStatus.OBSERVED
    assert exc_info.value.correlation_id == _RUN_ID
