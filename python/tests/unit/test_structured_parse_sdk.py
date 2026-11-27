"""The SDK's ``.parse()`` path matches its plain path (BG §3.4, #231).

``donkey.openai().chat.completions.parse(response_format=Model)`` goes through the
same ``DonkeyAsyncClient`` transport as ``create()``, so the refusal, budget and
telemetry hooks must attach to it identically. Each test runs both calls against
the in-process simulator (or ``simulate()``) and compares the results.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytest.importorskip("openai")
pytest.importorskip("starlette")
pydantic = pytest.importorskip("pydantic")

from pydantic import BaseModel  # noqa: E402 — after importorskip

from donkey_kit import Donkey, DonkeyConfig, PIIDetected  # noqa: E402
from donkey_kit._testing import http_clients, swap_transport  # noqa: E402
from donkey_kit.core import telemetry  # noqa: E402
from donkey_kit.simulator import build_app  # noqa: E402

_MESSAGES = [{"role": "user", "content": "make a ticket"}]


class Ticket(BaseModel):
    id: str
    priority: str


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


def _donkey_on_simulator() -> Donkey:
    """A Donkey whose data-plane client is served by a fresh in-process simulator
    (its own budget counter)."""
    donkey = Donkey(_cfg())
    async_client, _ = http_clients(donkey)
    swap_transport(async_client, httpx.ASGITransport(app=build_app()))
    return donkey


async def _call(donkey: Donkey, method: str) -> Any:
    client = donkey.openai()
    if method == "parse":
        return await client.chat.completions.parse(
            model="gpt-4o", messages=_MESSAGES, response_format=Ticket
        )
    return await client.chat.completions.create(model="gpt-4o", messages=_MESSAGES)


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("donkey_kit"))
    try:
        yield exporter
    finally:
        provider.shutdown()


async def test_parse_returns_the_typed_model_through_the_sdk() -> None:
    donkey = _donkey_on_simulator()
    completion = await _call(donkey, "parse")
    assert isinstance(completion.choices[0].message.parsed, Ticket)


@pytest.mark.parametrize("method", ["create", "parse"])
async def test_refusal_raises_the_same_typed_error_on_both_paths(method: str) -> None:
    # Typed refusals are applied by the run scope (donkey.run / @governed), not by
    # the bare client, so the call runs inside one.
    donkey = Donkey(_cfg())
    with donkey.simulate(PIIDetected):
        with pytest.raises(PIIDetected):
            async with donkey.run():
                await _call(donkey, method)


async def test_parse_feeds_the_budget_like_create() -> None:
    remaining: dict[str, int | None] = {}
    for method in ("create", "parse"):
        donkey = _donkey_on_simulator()
        await _call(donkey, method)
        remaining[method] = donkey.budget.remaining
    assert remaining["parse"] is not None
    assert remaining["parse"] == remaining["create"]


async def test_parse_span_carries_the_plain_paths_attributes(spans: Any) -> None:
    keys: dict[str, set[str]] = {}
    for method in ("create", "parse"):
        spans.clear()
        donkey = _donkey_on_simulator()
        await _call(donkey, method)
        (span,) = spans.get_finished_spans()  # exactly one span per call
        assert span.name == telemetry.SPAN_LLM_CHAT
        keys[method] = set(dict(span.attributes))
        assert dict(span.attributes)["gen_ai.request.model"] == "gpt-4o"
    assert keys["parse"] == keys["create"]
