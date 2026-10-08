"""Pins the per-upstream route contract (#894) to LIVE captures: each of the
four OpenAI-ingress upstreams (openai, azureopenai, bedrockanthropic, gemini)
called on both `/chat/completions` and `/responses`, success + stream + reject,
captured by hand on 2026-10-08. See tests/fixtures/anypoint/routes/README.md and
docs/verified-apis.md §2 ("Per-upstream route matrix").

The headline result drives the framework default-route decision: only
`/chat/completions` is served by every upstream; `/responses` 404s on Azure
OpenAI, and the transcoded upstreams (Bedrock, Gemini) stream it only partially
or not at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from donkey_kit.core.errors import PolicyViolation, UpstreamRequestError, classify
from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "routes"

UPSTREAMS = ("openai", "azureopenai", "bedrockanthropic", "gemini")
ROUTES = ("chat_completions", "responses")


def _status(name: str) -> int:
    return int((FIXTURES / f"{name}.headers.txt").read_text().split()[1])


def _headers(name: str) -> dict[str, str]:
    return parse_headers((FIXTURES / f"{name}.headers.txt").read_text())


def _json(name: str) -> dict[str, object]:
    body = json.loads((FIXTURES / f"{name}.body.json").read_text())
    assert isinstance(body, dict)
    return body


def _sse(name: str) -> tuple[list[str], list[str]]:
    """(event: names, data: payloads) of a captured SSE body."""
    lines = (FIXTURES / f"{name}.body.txt").read_text().splitlines()
    events = [ln.removeprefix("event: ") for ln in lines if ln.startswith("event: ")]
    data = [ln.removeprefix("data: ") for ln in lines if ln.startswith("data: ")]
    return events, data


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("shape", ["success", "stream", "reject"])
@pytest.mark.parametrize("upstream", UPSTREAMS)
def test_each_capture_was_served_by_the_intended_upstream(
    upstream: str, route: str, shape: str
) -> None:
    """The provider header proves which upstream answered (the Gemini captures
    went through the Azure proxy's model-based route, not its Route A fallback)."""
    headers = _headers(f"{upstream}.{route}.{shape}")
    assert headers["x-llm-proxy-llm-provider"] == upstream


@pytest.mark.parametrize("upstream", UPSTREAMS)
def test_chat_completions_is_served_by_every_upstream(upstream: str) -> None:
    """The only route all four upstreams serve: the basis for defaulting the
    Responses-first adapters (agent-framework, LangGraph) to Chat Completions."""
    name = f"{upstream}.chat_completions.success"
    assert _status(name) == 200
    body = _json(name)
    assert body["object"] == "chat.completion"
    choices = body["choices"]
    assert isinstance(choices, list)
    assert choices[0]["message"]["role"] == "assistant"
    usage = body["usage"]
    assert isinstance(usage, dict)
    assert {"prompt_tokens", "completion_tokens", "total_tokens"} <= set(usage)


@pytest.mark.parametrize("upstream", ["openai", "bedrockanthropic", "gemini"])
def test_responses_is_served_natively_or_transcoded(upstream: str) -> None:
    name = f"{upstream}.responses.success"
    assert _status(name) == 200
    body = _json(name)
    assert body["object"] == "response"
    output = body["output"]
    assert isinstance(output, list)
    message = next(o for o in output if o["type"] == "message")
    assert message["content"][0]["type"] == "output_text"
    usage = body["usage"]
    assert isinstance(usage, dict)
    assert {"input_tokens", "output_tokens", "total_tokens"} <= set(usage)


@pytest.mark.parametrize("upstream", ["bedrockanthropic", "gemini"])
def test_transcoded_responses_omit_top_level_status(upstream: str) -> None:
    """Native OpenAI sets `status: "completed"`; the gateway's transcoder for
    non-OpenAI upstreams leaves it unset (the per-item status is still set)."""
    assert _json("openai.responses.success")["status"] == "completed"
    assert _json(f"{upstream}.responses.success").get("status") is None


@pytest.mark.parametrize("shape", ["success", "stream", "reject"])
def test_azure_openai_does_not_serve_responses(shape: str) -> None:
    """Azure OpenAI 404s `/responses` on every shape, before the body is even
    validated, so a Responses-first adapter can't reach Azure at all."""
    name = f"azureopenai.responses.{shape}"
    assert _status(name) == 404
    body = _json(name)
    assert body == {"error": {"code": "404", "message": "Resource not found"}}
    err = classify(httpx.Response(404, json=body))
    assert isinstance(err, UpstreamRequestError)
    assert "Resource not found" in str(err)


@pytest.mark.parametrize("upstream", ["openai", "azureopenai", "bedrockanthropic"])
def test_chat_completions_stream_is_chunked_sse(upstream: str) -> None:
    name = f"{upstream}.chat_completions.stream"
    assert _headers(name)["content-type"].startswith("text/event-stream")
    _, data = _sse(name)
    assert data[-1] == "[DONE]"
    chunks = [json.loads(d) for d in data[:-1]]
    if upstream == "azureopenai":
        # Azure leads with a content-safety chunk: no choices, `object: ""`.
        lead = chunks.pop(0)
        assert lead["object"] == ""
        assert lead["choices"] == []
        assert "prompt_filter_results" in lead
    assert len(chunks) >= 2
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert any("delta" in c["choices"][0] for c in chunks if c["choices"])


def test_openai_responses_stream_has_the_full_event_sequence() -> None:
    events, _ = _sse("openai.responses.stream")
    assert events[0] == "response.created"
    assert events[-1] == "response.completed"
    assert {
        "response.in_progress",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
    } <= set(events)


def test_bedrock_responses_stream_is_partial() -> None:
    """The transcoded Bedrock stream starts and completes but skips the
    `in_progress` and `*.done` text/part events a native stream emits."""
    events, _ = _sse("bedrockanthropic.responses.stream")
    assert events[0] == "response.created"
    assert events[-1] == "response.completed"
    assert "response.output_text.delta" in events
    assert not {
        "response.in_progress",
        "response.output_text.done",
        "response.content_part.done",
    } & set(events)


def test_gemini_chat_completions_stream_is_one_unchunked_event() -> None:
    """Fake streaming: a single `data:` event holding the whole non-chunk
    completion (`message`, not `delta`) and no `[DONE]` terminator."""
    events, data = _sse("gemini.chat_completions.stream")
    assert events == []
    assert len(data) == 1
    body = json.loads(data[0])
    assert body["object"] == "chat.completion"
    assert "message" in body["choices"][0]
    assert "delta" not in body["choices"][0]


def test_gemini_responses_stream_is_one_unnamed_event() -> None:
    """Fake streaming: a single `data:` event holding the whole Response object,
    with no `event:` lines at all."""
    events, data = _sse("gemini.responses.stream")
    assert events == []
    assert len(data) == 1
    assert json.loads(data[0])["object"] == "response"


_REJECT_ENVELOPES = {
    "openai": ("decimal_above_max_value", "invalid_request_error"),
    "azureopenai": ("decimal_above_max_value", "invalid_request_error"),
    "bedrockanthropic": ("400", "upstream_error"),
    "gemini": ("400", None),
}


@pytest.mark.parametrize(
    ("upstream", "route", "code", "error_type"),
    [
        (up, route, *envelope)
        for up, envelope in _REJECT_ENVELOPES.items()
        for route in ROUTES
        # Azure 404s /responses before validating the body (pinned above).
        if (up, route) != ("azureopenai", "responses")
    ],
)
def test_out_of_range_param_classifies_as_upstream_request_error(
    upstream: str, route: str, code: str, error_type: str | None
) -> None:
    """Each upstream's own 400 envelope for `temperature: 99` maps to
    UpstreamRequestError (not a gateway PolicyViolation). OpenAI and Azure
    send the native OpenAI code/type; the transcoded upstreams send the HTTP
    status as `code`, and Gemini an empty `type` (surfaced as None)."""
    name = f"{upstream}.{route}.reject"
    assert _status(name) == 400
    err = classify(httpx.Response(400, json=_json(name)))
    assert isinstance(err, UpstreamRequestError)
    assert not isinstance(err, PolicyViolation)
    assert err.code == code
    assert err.error_type == error_type
    assert "temperature" in str(err)
