"""Pins the native Gemini ingress contract to LIVE captures from a real
``Format=Gemini`` proxy (``ddk-gemini-inbound``, instance 21193369) — see
tests/fixtures/anypoint/gemini_inbound/README.md and docs/verified-apis.md §2
(#540, #691).

Two halves: the captured shapes themselves, and the framework-free transport
consuming them. A native Gemini request carries the model in the URL path and
reports usage as ``usageMetadata``, so the transport must read both for
``donkey.last_call``, the GenAI span and usage to work on ``adk.gemini()``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import UpstreamRequestError, classify
from donkey_kit.core.lastcall import LastCallStatus, current_last_call
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.core.transport.policy import _request_model
from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "gemini_inbound"
_CFG = DonkeyConfig(llm_proxy_url="https://proxy")
_GENERATE = "https://proxy/ddk-gemini-inbound/models/gemini-2.5-flash:generateContent"
_STREAM = "https://proxy/ddk-gemini-inbound/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
_CONTENTS = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _headers(name: str) -> dict[str, str]:
    return parse_headers((FIXTURES / name).read_text())


def _fixture_response(body: str, headers: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=(FIXTURES / body).read_bytes(), headers=_headers(headers)
    )


# --- the captured shapes ------------------------------------------------------


def test_success_body_is_native_gemini_with_usage_metadata() -> None:
    body = json.loads((FIXTURES / "responses.success.body.json").read_text())
    usage = body["usageMetadata"]
    assert body["candidates"][0]["content"]["role"] == "model"
    # totalTokenCount already INCLUDES the thoughts — it is not prompt + candidates.
    assert usage["totalTokenCount"] == (
        usage["promptTokenCount"] + usage["candidatesTokenCount"] + usage["thoughtsTokenCount"]
    )


def test_passthrough_carries_no_served_model_routing_headers() -> None:
    # A native ingress passes through: no provider/model/routing-type headers, so
    # last_call's served_* fields stay None on this path (correct, not a gap).
    for name in ("responses.success.headers.txt", "responses.stream.headers.txt"):
        h = _headers(name)
        assert h["x-llm-proxy-model-based-routing-success"].startswith("Request passed through")
        assert "x-llm-proxy-llm-model" not in h
        assert "x-llm-proxy-routing-type" not in h
        assert h["x-envoy-decorator-operation"].startswith("api-instance-21193369.")


def test_stream_is_sse_with_cumulative_usage_per_event() -> None:
    assert _headers("responses.stream.headers.txt")["content-type"] == "text/event-stream"
    events = [
        json.loads(line[len("data:") :])
        for line in (FIXTURES / "responses.stream.body.txt").read_text().splitlines()
        if line.startswith("data:")
    ]
    totals = [e["usageMetadata"]["totalTokenCount"] for e in events]
    assert len(events) > 1
    assert totals == sorted(totals)  # each event reports the running total


def test_unknown_model_is_an_upstream_404_passed_through() -> None:
    resp = _fixture_response(
        "reject.unknown-model.body.json", "reject.unknown-model.headers.txt", status=404
    )
    assert resp.json()["error"]["status"] == "NOT_FOUND"
    assert isinstance(classify(resp), UpstreamRequestError)


# --- the transport consuming them ---------------------------------------------


def test_request_model_reads_the_gemini_url_path() -> None:
    assert _request_model(httpx.Request("POST", _GENERATE, json=_CONTENTS)) == "gemini-2.5-flash"
    assert _request_model(httpx.Request("POST", _STREAM, json=_CONTENTS)) == "gemini-2.5-flash"


def test_request_model_prefers_the_body_and_ignores_non_model_calls() -> None:
    body_model = {"model": "gpt-4o", **_CONTENTS}
    assert _request_model(httpx.Request("POST", _GENERATE, json=body_model)) == "gpt-4o"
    # A GET on the same path, an unrelated method suffix, or a plain POST is not a
    # model call, so it opens no span and records no last_call.
    assert _request_model(httpx.Request("GET", _GENERATE)) is None
    assert (
        _request_model(httpx.Request("POST", _GENERATE.replace("generateContent", "countTokens")))
        is None
    )
    assert _request_model(httpx.Request("POST", "https://proxy/token", json={"a": 1})) is None


async def test_buffered_gemini_call_populates_last_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _fixture_response("responses.success.body.json", "responses.success.headers.txt")

    async with DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler)) as client:
        await client.post(_GENERATE, json=_CONTENTS)

    record = current_last_call()
    assert record is not None and record.status is LastCallStatus.OBSERVED
    assert record.requested_model == "gemini-2.5-flash"
    assert record.api_instance_id == "21193369"
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (9, 2, 32)
    assert record.reasoning_tokens == 21
    assert record.served_model is None and record.routing_type is None
    assert record.substituted is False


class _AsyncChunks(httpx.AsyncByteStream):
    """A genuinely unread stream — ``content=`` would buffer the SSE body and
    bypass the transport's stream wrapper."""

    def __init__(self, data: bytes, size: int = 64) -> None:
        self._chunks = [data[i : i + size] for i in range(0, len(data), size)]

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk


async def test_streamed_gemini_call_fills_usage_after_drain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (FIXTURES / "responses.stream.body.txt").read_bytes()
        return httpx.Response(
            200, headers=_headers("responses.stream.headers.txt"), stream=_AsyncChunks(body)
        )

    async with DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler)) as client:
        req = client.build_request("POST", _STREAM, json=_CONTENTS)
        resp = await client.send(req, stream=True)
        async for _line in resp.aiter_lines():
            pass
        await resp.aclose()

    record = current_last_call()
    assert record is not None and record.requested_model == "gemini-2.5-flash"
    # The terminal event's cumulative counts win.
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (9, 29, 38)
