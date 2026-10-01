"""A real ``anthropic.AsyncAnthropic`` from ``donkey.anthropic``, end to end (#701).

Runs against whichever ``anthropic`` is installed: on ``anthropic<1`` (``httpx``)
the client gets the shared ``DonkeyAsyncClient``'s non-owning view, on ``anthropic>=1.0``
(``httpx2``) a bridged client that sends through it. Either way the same governed
request reaches the wire and the same ``donkey.last_call`` is recorded. CI runs
this file on both majors (the ``anthropic-stacks`` job).

The shared client sits on a mock transport replaying the live ``Format=Anthropic``
capture, so no proxy is needed. Guarded by ``importorskip("anthropic")``.
"""

from __future__ import annotations

import pytest

anthropic = pytest.importorskip("anthropic")

import json  # noqa: E402

import httpx  # noqa: E402
from _anthropic_wire import (  # noqa: E402
    BODY,
    CFG,
    SSE_CHUNKS,
    SSE_REQUEST_ID,
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
from donkey_kit.core.telemetry import run_context  # noqa: E402
from donkey_kit.integrations.anthropic import AnthropicAdapter  # noqa: E402

_ON_HTTPX2 = not issubclass(anthropic.DefaultAsyncHttpxClient, httpx.AsyncClient)


def test_the_http_client_matches_the_installed_anthropic() -> None:
    shared = shared_client(lambda request: success_response())
    http_client = AnthropicAdapter(CFG, shared).connection_kwargs()["http_client"]
    if _ON_HTTPX2:
        assert type(http_client).__module__.split(".")[0] == "httpx2"
    else:
        assert http_client is shared.view()


async def test_a_governed_call_reaches_the_wire_and_last_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return success_response()

    async with shared_client(handler) as shared:
        llm = AnthropicAdapter(CFG, shared).client()
        assert isinstance(llm, anthropic.AsyncAnthropic)
        reply = await llm.messages.create(**BODY)

    assert reply.content[0].text == "PONG"
    (wire,) = seen
    assert wire.url.path == "/ddk-anthropic-inbound/v1/messages"
    assert wire.headers["client_id"] == "cid-123"
    assert wire.headers["client_secret"] == "csecret-456"
    assert wire.headers["x-correlation-id"]
    assert wire.headers["x-donkey-request-id"]
    record = current_last_call()
    assert record is not None and record.status is LastCallStatus.OBSERVED
    assert (record.input_tokens, record.output_tokens) == (16, 6)
    # Anthropic's id rides `request-id`; its cache counts are flat on `usage` (#827).
    assert record.request_id == "req_011CfMnBEhyZATzuKEENjEo6"
    assert (record.cached_tokens, record.cache_write_tokens) == (0, 0)


async def test_anthropic_cache_counts_reach_last_call() -> None:
    body = json.loads(success_response().content)
    body["usage"].update(
        input_tokens=12, cache_read_input_tokens=2000, cache_creation_input_tokens=100
    )

    def handler(request: httpx.Request) -> httpx.Response:
        response = success_response()
        return httpx.Response(200, json=body, headers=response.headers)

    async with shared_client(handler) as shared:
        await AnthropicAdapter(CFG, shared).client().messages.create(**BODY)

    record = current_last_call()
    assert record is not None
    # input_tokens is taken as Anthropic reports it: EXCLUDING the cache counts.
    assert (record.input_tokens, record.cached_tokens, record.cache_write_tokens) == (
        12,
        2000,
        100,
    )


async def test_a_streamed_call_arrives_and_closes_the_shared_response() -> None:
    upstream = Chunks(SSE_CHUNKS)

    async with shared_client(lambda request: sse_response(upstream)) as shared:
        llm = AnthropicAdapter(CFG, shared).client()
        async with llm.messages.stream(**BODY) as stream:
            text = "".join([delta async for delta in stream.text_stream])
            final = await stream.get_final_message()

    assert text == "PONG"
    assert final.usage.output_tokens == 6
    assert upstream.closed
    # input_tokens and the cache counts arrive only on message_start, nested
    # under `message`; output_tokens on the later message_delta (#827).
    record = current_last_call()
    assert record is not None and record.status is LastCallStatus.OBSERVED
    assert record.request_id == SSE_REQUEST_ID
    assert (record.input_tokens, record.output_tokens) == (16, 6)
    assert (record.cached_tokens, record.cache_write_tokens) == (2000, 100)


async def test_a_lost_gateway_is_a_connection_error_caused_by_gateway_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with shared_client(handler) as shared:
        llm = AnthropicAdapter(CFG, shared).client()
        with pytest.raises(anthropic.APIConnectionError) as info:
            await llm.messages.create(**BODY)

    assert isinstance(info.value.__cause__, GatewayUnavailable)


@pytest.mark.parametrize(
    ("name", "framework_error", "expected"),
    [
        ("pii-detected", anthropic.PermissionDeniedError, PIIDetected),
        ("token-rate-limit", anthropic.RateLimitError, TokenBudgetExceeded),
    ],
)
async def test_a_refusal_classifies_with_the_ids_that_were_sent(
    name: str, framework_error: type[Exception], expected: type[DonkeyError]
) -> None:
    # The documented path into the taxonomy, ``classify(exc.response)``, joins a
    # refusal to its run and gateway record on both stacks (#738).
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return refusal_response(name)

    async with shared_client(handler) as shared:
        llm = AnthropicAdapter(CFG, shared).client(max_retries=0)
        with run_context("run-42"), pytest.raises(framework_error) as info:
            await llm.messages.create(**BODY)

    err = classify(info.value.response)  # type: ignore[attr-defined]
    assert isinstance(err, expected)
    (wire,) = seen
    assert err.correlation_id == "run-42"
    assert err.call_id is not None and err.call_id == wire.headers["x-donkey-request-id"]


async def test_closing_the_anthropic_client_leaves_the_shared_client_usable() -> None:
    # Both stacks: anthropic<1 gets the shared client's view, 1.0+ the bridge (#733).
    async with shared_client(lambda request: success_response()) as shared:
        adapter = AnthropicAdapter(CFG, shared)
        async with adapter.client() as llm:
            await llm.messages.create(**BODY)
        assert not shared.is_closed
        reply = await adapter.client().messages.create(**BODY)

    assert reply.content[0].text == "PONG"
