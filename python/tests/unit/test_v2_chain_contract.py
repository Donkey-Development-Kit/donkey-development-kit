"""Pins the ``llm-gw-v2`` outbound transcoding chain (#1409) to LIVE captures from
a short-lived probe proxy (instance 21300212, 2026-11-14): tool names on
``/responses``, the named-event stream and the usage shapes, for an Anthropic and
a Gemini upstream. See tests/fixtures/anypoint/v2_chain/README.md and
docs/verified-apis.md §2.

The SDK needs no change for any of it; the test pins the captured shapes and the
transport consuming them, so a gateway change shows up here first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import UpstreamRequestError, classify
from donkey_kit.core.lastcall import current_last_call, parse_usage
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "v2_chain"
_CFG = DonkeyConfig(llm_proxy_url="https://proxy")
_RESPONSES = "https://proxy/ddk-testv2-probe/responses"

UPSTREAMS = {"anthropic": "claude-haiku-4-5-20251001", "gemini": "gemini-3.8-flash"}
LONG_NAME = "lookup_" + "x" * 140
NAMES = {"tool_long": LONG_NAME, "tool_invalid": "mcp.server/get weather!"}


def _headers(stem: str) -> dict[str, str]:
    return parse_headers((FIXTURES / f"{stem}.headers.txt").read_text())


def _json(stem: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{stem}.body.json").read_text())


def _sse(stem: str) -> list[tuple[str, dict[str, Any]]]:
    """``[(event, data)]``, checking each ``event:`` names its ``data`` type."""
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in (FIXTURES / f"{stem}.body.txt").read_text().splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data = json.loads(line[len("data:") :])
            assert data["type"] == event
            out.append((event, data))
    return out


def _calls(body: dict[str, Any]) -> list[str]:
    return [item["name"] for item in body["output"] if item["type"] == "function_call"]


class _AsyncChunks(httpx.AsyncByteStream):
    """A genuinely unread stream, so the transport's stream wrapper sees it."""

    def __init__(self, data: bytes, size: int = 64) -> None:
        self._chunks = [data[i : i + size] for i in range(0, len(data), size)]

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk


# --- tool names ---------------------------------------------------------------


def test_long_name_fixture_is_over_the_openai_limit() -> None:
    assert len(LONG_NAME) == 147 > 64


@pytest.mark.parametrize("upstream", UPSTREAMS)
@pytest.mark.parametrize("case", NAMES)
def test_tool_name_round_trips_unchanged(upstream: str, case: str) -> None:
    stem = f"{upstream}.responses.{case}"
    assert _headers(stem)["x-llm-proxy-llm-provider"] == upstream
    body = _json(stem)
    assert body["status"] == "completed"
    assert _calls(body) == [NAMES[case]]


@pytest.mark.parametrize("upstream", UPSTREAMS)
@pytest.mark.parametrize("case", NAMES)
def test_streamed_tool_name_round_trips_unchanged(upstream: str, case: str) -> None:
    items = [
        data["item"]
        for event, data in _sse(f"{upstream}.responses.{case}.stream")
        if event in ("response.output_item.added", "response.output_item.done")
        and data["item"]["type"] == "function_call"
    ]
    assert len(items) == 2
    assert {item["name"] for item in items} == {NAMES[case]}


@pytest.mark.parametrize("upstream", UPSTREAMS)
def test_colliding_names_are_accepted(upstream: str) -> None:
    # get.weather and get_weather in one request: no 400 on the v2 chain.
    stem = f"{upstream}.responses.tool_collide"
    assert _headers(stem)["x-llm-proxy-llm-provider"] == upstream
    assert _json(stem)["status"] == "completed"


def test_colliding_names_keep_the_called_name() -> None:
    assert _calls(_json("anthropic.responses.tool_collide")) == ["get.weather"]


# --- the /responses stream ------------------------------------------------------


@pytest.mark.parametrize("upstream", UPSTREAMS)
@pytest.mark.parametrize("case", ["tool_long", "tool_invalid", "tool_collide"])
def test_stream_has_the_named_event_lifecycle(upstream: str, case: str) -> None:
    stem = f"{upstream}.responses.{case}.stream"
    assert _headers(stem)["content-type"] == "text/event-stream"
    events = _sse(stem)
    names = [event for event, _ in events]
    assert names[:2] == ["response.created", "response.in_progress"]
    assert names[-1] == "response.completed"
    assert [d["sequence_number"] for _, d in events] == list(range(len(events)))
    final = events[-1][1]["response"]
    assert final["status"] == "completed" and final["model"] == UPSTREAMS[upstream]
    assert final["usage"]["total_tokens"] > 0


@pytest.mark.parametrize("upstream", UPSTREAMS)
@pytest.mark.parametrize("case", NAMES)
def test_stream_announces_each_output_item(upstream: str, case: str) -> None:
    names = [event for event, _ in _sse(f"{upstream}.responses.{case}.stream")]
    assert "response.output_item.added" in names and "response.output_item.done" in names


@pytest.mark.parametrize("suffix", ["", ".stream"])
def test_gemini_reasoning_can_leave_output_empty_yet_completed(suffix: str) -> None:
    # The gap: reasoning spent the budget, nothing was output, and the response
    # still says "completed" with no incomplete_details.
    stem = f"gemini.responses.tool_collide{suffix}"
    body = _json(stem) if not suffix else _sse(stem)[-1][1]["response"]
    assert body["output"] == []
    assert body["status"] == "completed" and body.get("incomplete_details") is None
    details = body["usage"]["output_tokens_details"]
    assert details["reasoning_tokens"] > 0.9 * body["usage"]["output_tokens"]


def test_gemini_stream_carries_reasoning_items() -> None:
    types = {
        data["item"]["type"]
        for event, data in _sse("gemini.responses.tool_long.stream")
        if event == "response.output_item.added"
    }
    assert types == {"reasoning", "function_call"}


# --- routing headers (#1405) ----------------------------------------------------


@pytest.mark.parametrize("upstream", UPSTREAMS)
def test_routing_headers_are_still_returned(upstream: str) -> None:
    h = _headers(f"{upstream}.responses.tool_long")
    assert h["x-llm-proxy-routing-type"] == "ModelBased"
    assert h["x-llm-proxy-routing-fallback"] == "false"
    assert h["x-llm-proxy-llm-model"] == UPSTREAMS[upstream]


# --- usage ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("anthropic.chat.cache_1", (7318, 5, 7323, 0, None, 0)),
        ("anthropic.chat.cache_2", (7318, 5, 7323, 7309, None, 0)),
        ("anthropic.responses.cache_2", (7318, 5, 7323, 0, None, 0)),
        ("gemini.chat.cache_2", (6753, 21, 6774, 4709, None, 19)),
        ("gemini.responses.cache_1", (6753, 75, 6828, 4709, None, 73)),
    ],
)
def test_parse_usage_reads_the_v2_shapes(stem: str, expected: tuple[int | None, ...]) -> None:
    usage = parse_usage(_json(stem)["usage"])
    fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    )
    # Input includes the cache read; there is no cache-write count, so it is None.
    assert tuple(usage[f] for f in fields) == expected


# --- the transport consuming them -----------------------------------------------


async def test_last_call_from_a_non_streamed_call() -> None:
    stem = "gemini.responses.tool_invalid"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers=_headers(stem), content=(FIXTURES / f"{stem}.body.json").read_bytes()
        )

    async with DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler)) as client:
        await client.post(_RESPONSES, json={"model": "gemini/gemini-3.8-flash", "input": "hi"})

    record = current_last_call()
    usage = _json(stem)["usage"]
    assert record is not None
    assert (record.served_provider, record.served_model, record.routing_type) == (
        "gemini",
        "gemini-3.8-flash",
        "ModelBased",
    )
    assert record.fallback is False
    assert (record.input_tokens, record.output_tokens, record.reasoning_tokens) == (
        usage["input_tokens"],
        usage["output_tokens"],
        usage["output_tokens_details"]["reasoning_tokens"],
    )
    assert record.cache_write_tokens is None


@pytest.mark.parametrize("upstream", UPSTREAMS)
async def test_last_call_from_a_streamed_call(upstream: str) -> None:
    stem = f"{upstream}.responses.tool_long.stream"

    def handler(request: httpx.Request) -> httpx.Response:
        body = (FIXTURES / f"{stem}.body.txt").read_bytes()
        return httpx.Response(200, headers=_headers(stem), stream=_AsyncChunks(body))

    async with DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler)) as client:
        request = client.build_request(
            "POST", _RESPONSES, json={"model": upstream, "input": "hi", "stream": True}
        )
        resp = await client.send(request, stream=True)
        async for _line in resp.aiter_lines():
            pass
        await resp.aclose()

    usage = _sse(stem)[-1][1]["response"]["usage"]
    record = current_last_call()
    assert record is not None and record.served_provider == upstream
    assert (record.input_tokens, record.output_tokens, record.total_tokens) == (
        usage["input_tokens"],
        usage["output_tokens"],
        usage["total_tokens"],
    )


# --- the openai SDK consuming the stream (#830 on v2) ---------------------------


@pytest.mark.parametrize("upstream", UPSTREAMS)
async def test_openai_responses_stream_helper_parses_the_v2_stream(upstream: str) -> None:
    openai = pytest.importorskip("openai")
    stem = f"{upstream}.responses.tool_long.stream"

    def handler(request: httpx.Request) -> httpx.Response:
        body = (FIXTURES / f"{stem}.body.txt").read_bytes()
        return httpx.Response(200, headers=_headers(stem), stream=_AsyncChunks(body))

    async with DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler)) as http:
        client = openai.AsyncOpenAI(
            base_url="https://proxy/ddk-testv2-probe/", api_key="k", http_client=http
        )
        async with client.responses.stream(model=upstream, input="hi") as stream:
            types = [event.type async for event in stream]
            final = await stream.get_final_response()

    assert types[0] == "response.created" and types[-1] == "response.completed"
    (call,) = [item for item in final.output if item.type == "function_call"]
    assert call.name == LONG_NAME
    assert final.usage is not None and final.usage.total_tokens > 0


# --- the tool-name 400s (#1755), shapes read from the policy source -------------
#
# Not captured live. The collision 400 needs two tool identities whose encodings
# share a 64-bit SHA-256 prefix after truncation (provider_tool_name.rs), so no
# real request reaches it. The bodies below are the ones the v2 transcoders build
# (microgateway-hybrid-policies b3e2ae1f): Anthropic answers invalid_request_error,
# Gemini unsupported_params_error, both with param "tools". classify() types them
# through its generic nested-error path, so no new error type is needed.

_POLICY_SOURCE_400S = {
    "anthropic.collision": (
        "invalid_request_error",
        "Responses tool names produced the same provider-safe alias: f_x__h_0123456789abcdef",
        "tools",
    ),
    "anthropic.invalid_name": (
        "invalid_request_error",
        "Anthropic tool names must match ^[a-zA-Z0-9_-]{1,128}$ after Responses namespace encoding",
        "tool_choice",
    ),
    "gemini.collision": (
        "unsupported_params_error",
        "Responses tool names produced the same provider-safe alias: f_x__h_0123456789abcdef",
        "tools",
    ),
}


@pytest.mark.parametrize("case", _POLICY_SOURCE_400S)
def test_tool_name_400_is_typed_as_upstream_request_error(case: str) -> None:
    error_type, message, param = _POLICY_SOURCE_400S[case]
    body = {"error": {"message": message, "type": error_type, "param": param, "code": None}}
    response = httpx.Response(400, json=body, request=httpx.Request("POST", _RESPONSES))

    err = classify(response)

    assert isinstance(err, UpstreamRequestError)
    assert err.param == param
