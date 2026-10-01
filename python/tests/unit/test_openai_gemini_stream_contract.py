"""Pins the non-conformant chat-completions stream an OpenAI-format proxy sends
when it routes to a Gemini upstream, from LIVE captures (``ddk-model-wallet``,
instance 21189395; ``ddk-request-compression``, instance 21194432) — see
tests/fixtures/anypoint/openai_gemini_stream/README.md and docs/verified-apis.md
§2 (#830).

Three halves: the captured shapes, the framework-free transport consuming them
(unaffected), and the openai SDK consuming them (``delta`` is ``None``). The
last half is what breaks Strands' default streaming; it is an upstream gap, so
the test pins today's behaviour and should be revisited when the gateway sends
``chat.completion.chunk`` deltas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.lastcall import current_last_call
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = (
    Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "openai_gemini_stream"
)
_CFG = DonkeyConfig(llm_proxy_url="https://proxy")
_CHAT = "https://proxy/ddk-model-wallet/chat/completions"
_BODY = {
    "model": "gemini/gemini-2.5-flash",
    "stream": True,
    "messages": [{"role": "user", "content": "hi"}],
}
_STREAMS = (
    "responses.stream",
    "responses.stream-tool-call",
    "responses.stream.request-compression",
)


def _headers(stem: str) -> dict[str, str]:
    return parse_headers((FIXTURES / f"{stem}.headers.txt").read_text())


def _events(stem: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / f"{stem}.body.txt").read_text().splitlines()
    return [json.loads(line[len("data:") :]) for line in lines if line.startswith("data:")]


class _AsyncChunks(httpx.AsyncByteStream):
    """A genuinely unread stream — ``content=`` would buffer the SSE body and
    bypass the transport's stream wrapper."""

    def __init__(self, data: bytes, size: int = 64) -> None:
        self._chunks = [data[i : i + size] for i in range(0, len(data), size)]

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk


def _handler(stem: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (FIXTURES / f"{stem}.body.txt").read_bytes()
        return httpx.Response(200, headers=_headers(stem), stream=_AsyncChunks(body))

    return httpx.MockTransport(handler)


# --- the captured shapes ------------------------------------------------------


@pytest.mark.parametrize("stem", _STREAMS)
def test_stream_is_sse_routed_to_gemini(stem: str) -> None:
    h = _headers(stem)
    assert h["content-type"] == "text/event-stream"
    assert h["x-llm-proxy-llm-provider"] == "gemini"
    assert h["x-llm-proxy-routing-type"] == "ModelBased"


@pytest.mark.parametrize("stem", _STREAMS)
def test_every_event_is_a_whole_completion_not_a_chunk(stem: str) -> None:
    for event in _events(stem):
        choice = event["choices"][0]
        assert event["object"] == "chat.completion"
        assert "message" in choice and "delta" not in choice
        # "stop" on every event, non-final and tool-call ones included.
        assert choice["finish_reason"] == "stop"


@pytest.mark.parametrize("stem", _STREAMS)
def test_stream_has_no_done_sentinel(stem: str) -> None:
    assert b"[DONE]" not in (FIXTURES / f"{stem}.body.txt").read_bytes()


def test_text_arrives_as_increments_with_cumulative_usage() -> None:
    events = _events("responses.stream")
    assert len(events) > 1
    # Each event's content is the next piece, not the whole answer so far...
    text = "".join(e["choices"][0]["message"]["content"] for e in events)
    assert text.startswith(events[0]["choices"][0]["message"]["content"])
    assert text != events[-1]["choices"][0]["message"]["content"]
    # ...while usage is the running total, even with include_usage requested.
    request = (FIXTURES / "request.stream.http").read_text()
    assert '"include_usage":true' in request
    completions = [e["usage"]["completion_tokens"] for e in events]
    assert completions == sorted(completions) and completions[0] < completions[-1]


def test_tool_call_arrives_whole_in_one_event() -> None:
    (event,) = _events("responses.stream-tool-call")
    (call,) = event["choices"][0]["message"]["tool_calls"]
    assert call["function"]["name"] == "get_weather"
    assert json.loads(call["function"]["arguments"]) == {"city": "Paris"}


# --- the transport consuming them (unaffected) -------------------------------


async def test_streamed_usage_is_the_terminal_total_not_a_sum() -> None:
    async with DonkeyAsyncClient(_CFG, None, transport=_handler("responses.stream")) as client:
        resp = await client.send(client.build_request("POST", _CHAT, json=_BODY), stream=True)
        async for _line in resp.aiter_lines():
            pass
        await resp.aclose()

    last = _events("responses.stream")[-1]["usage"]
    record = current_last_call()
    assert record is not None and record.served_provider == "gemini"
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (
        last["prompt_tokens"],
        last["completion_tokens"],
        last["total_tokens"],
    )


# --- the openai SDK consuming them (the upstream gap) ------------------------


async def test_openai_sdk_yields_chunks_with_no_delta() -> None:
    openai = pytest.importorskip("openai")
    async with DonkeyAsyncClient(_CFG, None, transport=_handler("responses.stream")) as http:
        client = openai.AsyncOpenAI(
            base_url="https://proxy/ddk-model-wallet/", api_key="k", http_client=http
        )
        stream = await client.chat.completions.create(
            model=_BODY["model"], messages=_BODY["messages"], stream=True
        )
        chunks = [chunk async for chunk in stream]

    assert len(chunks) == len(_events("responses.stream"))
    # This is what crashes Strands' streaming path (#830): no delta to read.
    assert all(chunk.choices[0].delta is None for chunk in chunks)
