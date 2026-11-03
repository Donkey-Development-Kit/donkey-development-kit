"""Pins model-based-routing def 1.0.5 / impl 1.0.9 to LIVE captures (#1404, #1405):
a qualified-vendor OpenAI-format proxy and two-vendor native Anthropic and Gemini
ingresses. See the READMEs under tests/fixtures/anypoint/
{model_routing_qualified_vendor,anthropic_inbound_multiroute,gemini_inbound_multiroute}/
and docs/verified-apis.md §2.

On 1.0.5 a native ingress routes like an OpenAI one: the gateway now returns the
provider/model/fallback headers there too, so ``donkey.last_call`` and the span
report the served route instead of ``None``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from donkey_kit.core import telemetry
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ModelNotRoutable, PolicyViolation, classify
from donkey_kit.core.lastcall import LastCall, current_last_call
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.core.transport.policy import _request_model
from donkey_kit.simulator.fixtures import parse_headers

_ANYPOINT = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"
QUALIFIED = _ANYPOINT / "model_routing_qualified_vendor"
ANTHROPIC = _ANYPOINT / "anthropic_inbound_multiroute"
GEMINI = _ANYPOINT / "gemini_inbound_multiroute"

_CFG = DonkeyConfig(llm_proxy_url="https://proxy")
_CHAT = "https://proxy/qualified/chat/completions"
_MESSAGES = "https://proxy/anthropic-multi/v1/messages"
_GEMINI = "https://proxy/gemini-multi/models/{}:{}"
_CONTENTS = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _chat(model: str) -> dict[str, object]:
    return {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]}


def _response(directory: Path, name: str, *, stream: bool = False) -> httpx.Response:
    raw = (directory / f"{name}.headers.txt").read_text()
    status = int(raw.split()[1])
    body = next(directory.glob(f"{name}.body.*")).read_bytes()
    headers = parse_headers(raw)
    if stream:
        return httpx.Response(status, headers=headers, stream=_AsyncChunks(body))
    return httpx.Response(status, headers=headers, content=body)


class _AsyncChunks(httpx.AsyncByteStream):
    """A genuinely unread stream, so the transport's SSE wrapper does the work."""

    def __init__(self, data: bytes, size: int = 64) -> None:
        self._chunks = [data[i : i + size] for i in range(0, len(data), size)]

    async def __aiter__(self):  # type: ignore[override]
        for chunk in self._chunks:
            yield chunk


async def _send(
    directory: Path, name: str, url: str, body: dict[str, object], *, stream: bool = False
) -> LastCall:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(directory, name, stream=stream)

    async with DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler)) as client:
        resp = await client.send(client.build_request("POST", url, json=body), stream=stream)
        if stream:
            async for _line in resp.aiter_lines():
                pass
            await resp.aclose()
    record = current_last_call()
    assert record is not None
    return record


def _use_tracer(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("donkey_kit.test"))
    return exporter


# --- qualified vendor on an OpenAI-format proxy (#1405) ------------------------


@pytest.mark.parametrize(
    ("name", "requested", "provider", "served", "fallback", "substituted"),
    [
        (
            "chat_completions.success",
            "openai/gpt-5-mini",
            "mulesoft:openai",
            "gpt-5-mini",
            False,
            False,
        ),
        (
            "chat_completions.gemini",
            "gemini/gemini-3.8-flash",
            "mulesoft:gemini",
            "gemini-3.8-flash",
            False,
            False,
        ),
        (
            "chat_completions.fallback",
            "mistral/mistral-small-latest",
            "mulesoft:openai",
            "gpt-5-mini",
            True,
            True,
        ),
    ],
)
async def test_qualified_vendor_route_reaches_last_call(
    name: str, requested: str, provider: str, served: str, fallback: bool, substituted: bool
) -> None:
    record = await _send(QUALIFIED, name, _CHAT, _chat(requested))
    assert record.requested_model == requested
    # served_provider stays verbatim; only the substitution check uses the suffix.
    assert (record.served_provider, record.served_model) == (provider, served)
    assert record.routing_type == "ModelBased"
    assert (record.fallback, record.substituted) == (fallback, substituted)


async def test_qualified_vendor_span_system_is_the_bare_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _use_tracer(monkeypatch)
    await _send(QUALIFIED, "chat_completions.gemini", _CHAT, _chat("gemini/gemini-3.8-flash"))
    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert attrs["gen_ai.system"] == "gemini"
    assert attrs["gen_ai.response.model"] == "gemini-3.8-flash"


def test_qualified_vendor_bare_model_is_not_routable() -> None:
    error = classify(_response(QUALIFIED, "chat_completions.bare_model_400"))
    assert isinstance(error, ModelNotRoutable)
    assert error.model == "gpt-5-mini"


# --- native Anthropic ingress, two vendors (#1404) -----------------------------


@pytest.mark.parametrize(
    ("name", "requested", "fallback", "substituted"),
    [
        ("messages.success", "claude-haiku-4-5-20251001", False, False),
        ("messages.prefixed", "anthropic/claude-haiku-4-5-20251001", False, False),
        # An unresolvable model falls back to the format's own identity: the
        # gateway says fallback=false, but the served model is not the one asked.
        ("messages.unresolvable", "some-unknown-model", False, True),
        # An unconfigured vendor prefix engages the policy fallback.
        ("messages.fallback", "openai/gpt-5-mini", True, True),
    ],
)
async def test_anthropic_multiroute_reaches_last_call(
    name: str, requested: str, fallback: bool, substituted: bool
) -> None:
    record = await _send(ANTHROPIC, name, _MESSAGES, _chat(requested))
    assert record.requested_model == requested
    assert (record.served_provider, record.served_model) == (
        "anthropic",
        "claude-haiku-4-5-20251001",
    )
    assert (record.fallback, record.substituted) == (fallback, substituted)
    assert (record.input_tokens, record.output_tokens) == (15, 4)


async def test_anthropic_multiroute_stream_fills_usage_after_drain() -> None:
    body = {**_chat("claude-haiku-4-5-20251001"), "stream": True}
    record = await _send(ANTHROPIC, "messages.stream", _MESSAGES, body, stream=True)
    assert record.served_provider == "anthropic" and record.substituted is False
    assert (record.input_tokens, record.output_tokens) == (15, 4)


def test_anthropic_bedrock_route_is_an_unconfirmed_text_404() -> None:
    # Live 2026-11-03: the policy matched the bedrockanthropic route, then the
    # upstream answered a text/plain 404. No documented contract, so it stays an
    # unconfirmed refusal that names the routing headers it saw.
    resp = _response(ANTHROPIC, "messages.bedrock_404")
    assert resp.headers["x-llm-proxy-llm-provider"] == "bedrockanthropic"
    error = classify(resp)
    assert type(error) is PolicyViolation and error.policy == "unknown"
    assert "x-llm-proxy-llm-provider" in str(error)


# --- native Gemini ingress, two vendors (#1404) --------------------------------


def test_request_model_reads_a_provider_prefixed_gemini_path() -> None:
    url = _GEMINI.format("openai/gpt-5-mini", "generateContent")
    assert _request_model(httpx.Request("POST", url, json=_CONTENTS)) == "openai/gpt-5-mini"


@pytest.mark.parametrize(
    ("name", "requested", "substituted"),
    [
        ("generate_content.success", "gemini-3.8-flash", False),
        # Live: a configured second vendor in the path is still served by gemini.
        ("generate_content.cross_vendor", "openai/gpt-5-mini", True),
        ("generate_content.unresolvable", "some-unknown-model", True),
    ],
)
async def test_gemini_multiroute_reaches_last_call(
    name: str, requested: str, substituted: bool
) -> None:
    url = _GEMINI.format(requested, "generateContent")
    record = await _send(GEMINI, name, url, _CONTENTS)
    assert record.requested_model == requested
    assert (record.served_provider, record.served_model) == ("gemini", "gemini-3.8-flash")
    assert record.fallback is False and record.substituted is substituted


async def test_gemini_multiroute_stream_fills_usage_after_drain() -> None:
    url = _GEMINI.format("gemini-3.8-flash", "streamGenerateContent") + "?alt=sse"
    record = await _send(GEMINI, "stream_generate_content.success", url, _CONTENTS, stream=True)
    assert record.served_provider == "gemini" and record.substituted is False
    assert (record.input_tokens, record.total_tokens) == (9, 134)


# --- invalid JSON on both native formats ---------------------------------------


@pytest.mark.parametrize(
    ("directory", "name"),
    [(ANTHROPIC, "messages.invalid_json"), (GEMINI, "generate_content.invalid_json")],
)
def test_invalid_json_is_a_flat_400_left_unconfirmed(directory: Path, name: str) -> None:
    # 1.0.5 rejects an unparseable body on every format with a flat-string 400
    # and no routing headers. The SDK's own clients always send valid JSON, so
    # it is pinned here as the generic refusal rather than given its own type.
    resp = _response(directory, name)
    assert resp.status_code == 400
    assert resp.json()["error"].startswith("Invalid JSON body: ")
    error = classify(resp)
    assert type(error) is PolicyViolation and error.policy == "unknown"
