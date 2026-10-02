"""The ``httpx2`` bridge and the Anthropic adapter's choice of HTTP stack (#701).

``anthropic>=1.0`` is built on ``httpx2`` and rejects any ``httpx`` object passed
as ``http_client``, so the shared ``DonkeyAsyncClient`` cannot be handed to it
directly. The adapter instead passes an ``httpx2.AsyncClient`` whose transport
forwards every request through the shared client (``integrations/_httpx2_bridge``),
and keeps passing the shared client itself on ``anthropic<1``.

Two halves: the bridge on its own, and the adapter choosing between the two
stacks (against a stub ``anthropic``). A real ``anthropic`` client on either
stack is driven end to end in ``test_anthropic_client_stacks.py``.

Guarded by ``importorskip("httpx2")``: the base-only CI job has no ``httpx2``.
"""

from __future__ import annotations

import gzip
import json
import sys
import types
from typing import Any

import pytest

httpx2 = pytest.importorskip("httpx2")

import httpx  # noqa: E402
from _anthropic_wire import (  # noqa: E402
    BODY,
    CFG,
    FIXTURES,
    MESSAGES,
    MODEL,
    REFUSALS,
    SSE_CHUNKS,
    Chunks,
    refusal_response,
    shared_client,
    sse_response,
    success_response,
)

from donkey_kit.core.errors import (  # noqa: E402
    DonkeyError,
    GatewayUnavailable,
    PIIDetected,
    TokenBudgetExceeded,
    classify,
)
from donkey_kit.core.lastcall import LastCallStatus, current_last_call  # noqa: E402
from donkey_kit.core.telemetry import run_scope  # noqa: E402
from donkey_kit.integrations._httpx2_bridge import (  # noqa: E402
    bridged_client,
    wants_stream,
)
from donkey_kit.integrations.anthropic import AnthropicAdapter  # noqa: E402

# --- the bridge on its own ----------------------------------------------------


async def test_a_bridged_call_is_governed_by_the_shared_client() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return success_response()

    async with shared_client(handler) as shared:
        bridged = bridged_client(shared)
        resp = await bridged.post(MESSAGES, json=BODY, headers={"client_id": "cid-123"})

    assert isinstance(resp, httpx2.Response)
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "PONG"
    assert resp.headers["x-llm-proxy-llm-model"] == MODEL

    (wire,) = seen
    # The caller's own headers and body arrive unchanged ...
    assert wire.headers["client_id"] == "cid-123"
    assert json.loads(wire.content) == BODY
    assert str(wire.url) == MESSAGES
    # ... and the shared client's request hook and retry loop ran on them.
    assert wire.headers["x-correlation-id"]
    assert wire.headers["x-donkey-request-id"]

    record = current_last_call()
    assert record is not None and record.status is LastCallStatus.OBSERVED
    assert record.requested_model == MODEL
    assert record.served_model == MODEL
    assert (record.input_tokens, record.output_tokens) == (16, 6)


async def test_a_bridged_stream_is_forwarded_chunk_by_chunk_and_closed() -> None:
    upstream = Chunks(SSE_CHUNKS)

    async with shared_client(lambda request: sse_response(upstream)) as shared:
        bridged = bridged_client(shared)
        received: list[bytes] = []
        async with bridged.stream("POST", MESSAGES, json={**BODY, "stream": True}) as resp:
            assert resp.headers["content-type"] == "text/event-stream"
            async for chunk in resp.aiter_raw():
                received.append(chunk)
        assert upstream.closed  # closing the httpx2 response closes the shared one

    assert received == SSE_CHUNKS


async def test_an_abandoned_bridged_stream_still_closes_the_shared_response() -> None:
    upstream = Chunks(SSE_CHUNKS)

    async with shared_client(lambda request: sse_response(upstream)) as shared:
        bridged = bridged_client(shared)
        async with bridged.stream("POST", MESSAGES, json={**BODY, "stream": True}) as resp:
            async for _ in resp.aiter_raw():
                break

    assert upstream.closed


@pytest.mark.parametrize(
    ("stream", "network_body"),
    [(False, False), (True, False), (True, True)],
    ids=["buffered", "streamed-pre-read", "streamed-from-network"],
)
async def test_a_compressed_body_is_decoded_exactly_once(stream: bool, network_body: bool) -> None:
    body = (FIXTURES / "responses.success.body.json").read_bytes()
    compressed = gzip.compress(body)
    headers = {"content-type": "application/json", "content-encoding": "gzip"}

    def handler(request: httpx.Request) -> httpx.Response:
        if network_body:
            return httpx.Response(200, headers=headers, stream=Chunks([compressed]))
        # content= reads (and decodes) itself on construction, as simulate() does.
        return httpx.Response(200, headers=headers, content=compressed)

    async with shared_client(handler) as shared:
        bridged = bridged_client(shared)
        resp = await bridged.post(MESSAGES, json={**BODY, "stream": stream})

    assert resp.content == body


async def test_a_pre_read_stream_from_a_simulated_transport_is_delivered() -> None:
    sse = b"".join(SSE_CHUNKS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

    async with shared_client(handler) as shared:
        bridged = bridged_client(shared)
        async with bridged.stream("POST", MESSAGES, json={**BODY, "stream": True}) as resp:
            received = b"".join([chunk async for chunk in resp.aiter_bytes()])

    assert received == sse


async def test_the_callers_timeout_reaches_the_wire() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return success_response()

    async with shared_client(handler) as shared:
        bridged = bridged_client(shared)
        await bridged.post(MESSAGES, json=BODY, timeout=httpx2.Timeout(12.5))
        await bridged.post(MESSAGES, json=BODY)

    explicit, default = (r.extensions["timeout"] for r in seen)
    assert explicit == {"connect": 12.5, "read": 12.5, "write": 12.5, "pool": 12.5}
    # Without a per-request timeout, the shared client's configured one applies.
    assert default == shared.timeout.as_dict()


@pytest.mark.parametrize(
    ("name", "expected"),
    [("pii-detected", PIIDetected), ("token-rate-limit", TokenBudgetExceeded)],
)
@pytest.mark.parametrize("stream", [False, True], ids=["buffered", "streamed"])
async def test_a_bridged_refusal_carries_the_ids_that_were_sent(
    name: str, expected: type[DonkeyError], stream: bool
) -> None:
    # httpx2 binds the response to the framework's own request, which the shared
    # client never saw; classify() reads the sent ids from that request (#738).
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return refusal_response(name)

    async with shared_client(handler) as shared:
        bridged = bridged_client(shared)
        with run_scope("run-42"):
            async with bridged.stream("POST", MESSAGES, json={**BODY, "stream": stream}) as resp:
                await resp.aread()

    err = classify(resp)  # type: ignore[arg-type]  # httpx2 mirrors the httpx API
    assert isinstance(err, expected)
    assert resp.status_code == REFUSALS[name]
    (wire,) = seen
    assert err.correlation_id == "run-42" == wire.headers["x-correlation-id"]
    assert err.call_id is not None and err.call_id == wire.headers["x-donkey-request-id"]
    # Only the ids are copied back: the consumer secret stays off the
    # framework's request object.
    assert "client_secret" not in resp.request.headers


async def test_a_lost_gateway_surfaces_as_gateway_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with shared_client(handler) as shared:
        bridged = bridged_client(shared)
        with pytest.raises(GatewayUnavailable):
            await bridged.post(MESSAGES, json=BODY)


async def test_closing_the_bridged_client_leaves_the_shared_one_open() -> None:
    async with shared_client(lambda request: success_response()) as shared:
        bridged = bridged_client(shared)
        await bridged.aclose()
        assert bridged.is_closed
        assert not shared.is_closed


@pytest.mark.parametrize(
    ("body", "headers", "expected"),
    [
        ({**BODY, "stream": True}, {}, True),
        ({**BODY, "stream": False}, {}, False),
        (BODY, {}, False),
        (BODY, {"X-Stainless-Raw-Response": "stream"}, True),
        (None, {}, False),
    ],
    ids=["stream-true", "stream-false", "no-flag", "raw-stream-header", "no-body"],
)
def test_wants_stream_follows_the_request(
    body: dict[str, Any] | None, headers: dict[str, str], expected: bool
) -> None:
    content = b"" if body is None else json.dumps(body).encode()
    request = httpx2.Request("POST", MESSAGES, content=content, headers=headers)
    assert wants_stream(request, content) is expected


def test_wants_stream_ignores_a_body_that_is_not_json() -> None:
    content = b'--boundary\r\n"stream": true'
    request = httpx2.Request("POST", MESSAGES, content=content)
    assert wants_stream(request, content) is False


# --- the adapter's choice of HTTP stack ---------------------------------------


def _stub_anthropic(monkeypatch: pytest.MonkeyPatch, default_client: type | None) -> None:
    stub = types.ModuleType("anthropic")
    if default_client is not None:
        stub.DefaultAsyncHttpxClient = default_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", stub)


class _HttpxEraClient(httpx.AsyncClient):
    """Stands in for ``anthropic<1``'s ``DefaultAsyncHttpxClient``."""


def test_anthropic_0x_gets_the_shared_clients_view(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_anthropic(monkeypatch, _HttpxEraClient)
    shared = shared_client(lambda request: success_response())
    kw = AnthropicAdapter(CFG, shared).connection_kwargs()
    assert kw["http_client"] is shared.view()


def test_anthropic_without_a_default_client_gets_the_shared_clients_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_anthropic(monkeypatch, None)
    shared = shared_client(lambda request: success_response())
    assert AnthropicAdapter(CFG, shared).connection_kwargs()["http_client"] is shared.view()


def test_connection_kwargs_work_without_anthropic_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import → ImportError
    shared = shared_client(lambda request: success_response())
    assert AnthropicAdapter(CFG, shared).connection_kwargs()["http_client"] is shared.view()


async def test_anthropic_1x_gets_one_bridged_client_per_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_anthropic(monkeypatch, httpx2.AsyncClient)
    shared = shared_client(lambda request: success_response())
    adapter = AnthropicAdapter(CFG, shared)

    first = adapter.connection_kwargs()["http_client"]
    assert isinstance(first, httpx2.AsyncClient)
    assert adapter.connection_kwargs()["http_client"] is first

    # `async with AsyncAnthropic(...)` closes its http_client on exit; the next
    # client the adapter hands out must not be that closed one.
    await first.aclose()
    second = adapter.connection_kwargs()["http_client"]
    assert isinstance(second, httpx2.AsyncClient)
    assert second is not first and not second.is_closed
    assert not shared.is_closed
    await shared.aclose()


def test_the_adapter_reports_last_call_on_both_stacks() -> None:
    assert AnthropicAdapter.observes_last_call is True
