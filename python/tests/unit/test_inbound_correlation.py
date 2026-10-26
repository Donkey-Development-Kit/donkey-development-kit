"""Joinable correlation across hops (BG §1.1, #308).

``donkey.run_from_headers()`` adopts an inbound caller's correlation ID (or mints
one), every governed call in the block sends it, and the GenAI span records
whether it was adopted or minted. ``donkey.correlation_headers()`` hands the
bound ID to an outbound MCP or A2A hop the governed transport does not carry.
Framework-free: ``httpx.MockTransport`` behind the governed client, and a
JSON-RPC ``tools/call`` POST standing in for an MCP hop.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import telemetry
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.correlation import (
    current_correlation_id,
    current_correlation_source,
    request_correlation_id,
)

_HEX32 = re.compile(r"[0-9a-f]{32}")
_CHAT = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
_TOOLS_CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "lookup"}}


def _donkey(seen: list[httpx.Request], **cfg: Any) -> Donkey:
    donkey = Donkey(
        DonkeyConfig(
            llm_proxy_url="https://proxy",
            llm_proxy_client_id="cid",
            llm_proxy_client_secret="csecret",
            **cfg,
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": []})

    for client in http_clients(donkey):
        swap_transport(client, httpx.MockTransport(handler))
    return donkey


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Any:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("donkey_kit.test")
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)
    return exporter


def _chat_spans(spans: Any) -> list[Any]:
    return [s for s in spans.get_finished_spans() if s.name == "donkey.llm.chat"]


# --- adoption: run_from_headers binds the caller's ID, or mints one ---------


async def test_an_inbound_id_is_adopted_and_sent_on_every_downstream_call() -> None:
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        async with donkey.run_from_headers({"x-correlation-id": "caller-run-1"}) as run_id:
            assert run_id == "caller-run-1"
            assert current_correlation_source() == "adopted"
            http = donkey.http_client()
            await http.post("https://proxy/chat/completions", json=_CHAT)
            await http.post("https://proxy/chat/completions", json=_CHAT)
    finally:
        await donkey.aclose()
    assert [r.headers["X-Correlation-Id"] for r in seen] == ["caller-run-1", "caller-run-1"]


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Correlation-Id": ""}, {"X-Correlation-Id": "a\r\nb"}, {"X-Correlation-Id": "x" * 129}],
)
def test_a_missing_or_malformed_inbound_id_is_minted_not_forwarded(
    headers: dict[str, str],
) -> None:
    donkey = Donkey(DonkeyConfig())
    with donkey.run_from_headers(headers) as run_id:
        assert _HEX32.fullmatch(run_id)
        assert current_correlation_id() == run_id
        assert current_correlation_source() == "minted"


def test_the_configured_correlation_header_is_the_one_read_and_sent() -> None:
    donkey = Donkey(DonkeyConfig(correlation_header="X-Run-Id"))
    inbound = {"X-Run-Id": "via-custom", "X-Correlation-Id": "ignored"}
    with donkey.run_from_headers(inbound) as run_id:
        assert run_id == "via-custom"
        assert donkey.correlation_headers() == {"X-Run-Id": "via-custom"}


def test_leaving_the_block_restores_the_unbound_state() -> None:
    donkey = Donkey(DonkeyConfig())
    with donkey.run_from_headers({"X-Correlation-Id": "r1"}):
        pass
    assert current_correlation_id() is None
    assert current_correlation_source() is None


def test_a_nested_run_reports_no_source_for_its_own_id() -> None:
    """An id the handler's own code chose is neither adopted nor minted for a hop."""
    donkey = Donkey(DonkeyConfig())
    with donkey.run_from_headers({"X-Correlation-Id": "outer"}):
        with donkey.run(id="inner"):
            assert current_correlation_id() == "inner"
            assert current_correlation_source() is None
        assert current_correlation_id() == "outer"
        assert current_correlation_source() == "adopted"


def test_a_plain_run_binds_no_source() -> None:
    with Donkey(DonkeyConfig()).run(id="ticket-9"):
        assert current_correlation_source() is None


def test_outside_a_run_each_request_still_gets_its_own_unbound_id() -> None:
    """``request_correlation_id()`` for blocking callers is unchanged (#803)."""
    first, second = request_correlation_id(), request_correlation_id()
    assert first != second
    assert current_correlation_id() is None


def test_the_cost_fields_bind_as_they_do_for_run() -> None:
    from donkey_kit.core.correlation import current_business_group, current_cost_tags

    donkey = Donkey(DonkeyConfig())
    with donkey.run_from_headers({}, team="support", business_group="tenant-a"):
        tags = current_cost_tags()
        assert tags is not None and tags.team == "support"
        assert current_business_group() == "tenant-a"


def test_a_refusal_leaving_the_block_is_typed() -> None:
    """The typed-refusal bridge applies on exit, exactly as for ``donkey.run()``."""
    from donkey_kit.core.errors import PIIDetected

    donkey = Donkey(DonkeyConfig())
    refusal = PIIDetected("blocked")
    with pytest.raises(PIIDetected), donkey.run_from_headers({}):
        raise refusal


# --- the span records adopted vs minted ------------------------------------


async def test_the_chat_span_records_an_adopted_id(spans: Any) -> None:
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        async with donkey.run_from_headers({"X-Correlation-Id": "caller-run-2"}):
            await donkey.http_client().post("https://proxy/chat/completions", json=_CHAT)
    finally:
        await donkey.aclose()
    (chat,) = _chat_spans(spans)
    assert chat.attributes["donkey.correlation_id"] == "caller-run-2"
    assert chat.attributes["donkey.correlation_id.source"] == "adopted"


def test_the_sync_client_records_a_minted_id(spans: Any) -> None:
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        with donkey.run_from_headers({}) as run_id:
            donkey.http_client(sync=True).post("https://proxy/chat/completions", json=_CHAT)
    finally:
        donkey.close()
    (chat,) = _chat_spans(spans)
    assert chat.attributes["donkey.correlation_id"] == run_id
    assert chat.attributes["donkey.correlation_id.source"] == "minted"


async def test_a_call_outside_an_inbound_run_claims_no_source(spans: Any) -> None:
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        async with donkey.run(id="ticket-3"):
            await donkey.http_client().post("https://proxy/chat/completions", json=_CHAT)
        await donkey.http_client().post("https://proxy/chat/completions", json=_CHAT)
    finally:
        await donkey.aclose()
    for chat in _chat_spans(spans):
        assert "donkey.correlation_id.source" not in chat.attributes


# --- propagation: an outbound MCP hop carries the bound ID -----------------


def test_correlation_headers_is_empty_outside_a_run() -> None:
    assert Donkey(DonkeyConfig()).correlation_headers() == {}


async def test_an_mcp_hop_through_the_governed_client_carries_the_run_id() -> None:
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        async with donkey.run_from_headers({"X-Correlation-Id": "caller-run-4"}):
            await donkey.http_client().post("https://proxy/mcp", json=_TOOLS_CALL)
    finally:
        await donkey.aclose()
    (request,) = seen
    assert request.headers["X-Correlation-Id"] == "caller-run-4"


async def test_an_mcp_hop_on_a_foreign_client_carries_it_via_correlation_headers() -> None:
    """A client the governed transport does not own sends the ID only when given
    ``correlation_headers()``, and never the consumer's gateway credentials."""
    seen: list[httpx.Request] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

    donkey = Donkey(DonkeyConfig(llm_proxy_client_id="cid", llm_proxy_client_secret="csecret"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as mcp:
        async with donkey.run_from_headers({"X-Correlation-Id": "caller-run-5"}):
            await mcp.post(
                "https://tools.example/mcp", json=_TOOLS_CALL, headers=donkey.correlation_headers()
            )
    (request,) = seen
    assert request.headers["X-Correlation-Id"] == "caller-run-5"
    assert "client_id" not in request.headers
    assert "client_secret" not in request.headers
