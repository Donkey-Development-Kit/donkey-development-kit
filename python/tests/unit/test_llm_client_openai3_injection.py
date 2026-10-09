"""openai 3.x drives our shared client end to end, via the core httpx2 bridge (#18, #728).

``openai>=3.0`` is built on ``httpx2`` (a *separate* distribution from the
``httpx`` our ``DonkeyAsyncClient`` / ``DonkeyClient`` subclass) and types
``AsyncOpenAI(http_client=…)`` as an ``httpx2`` client. So ``donkey.llm`` hands
it the core bridge (``core/transport/httpx2``): an ``httpx2`` client whose
transport forwards every request through the shared client, exactly as for
``anthropic>=1`` (#701). These tests pin that the bridged client is what openai
gets, and that every request still reaches our transport as a plain
``httpx.Request`` with the governed headers, so a future openai release that
broke the bridge would fail CI loudly (floors-never-ceilings,
docs/verified-apis.md §8.1) rather than in a customer sandbox.

Guarded by ``importorskip("openai")`` at module top so the base-only CI job
(the ``dev`` group only, no ``[llm]``) skips it cleanly; it runs in the full/nightly
matrix where openai is installed and always the newest release.
"""

from __future__ import annotations

import pytest

openai = pytest.importorskip("openai")

from collections.abc import AsyncIterator, Iterator  # noqa: E402

import httpx  # noqa: E402

from donkey_kit.core.config import DonkeyConfig  # noqa: E402
from donkey_kit.core.errors import ConfigError, PIIDetected, classify  # noqa: E402
from donkey_kit.core.telemetry import run_scope  # noqa: E402
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient  # noqa: E402
from donkey_kit.core.transport.views import built_on_httpx2  # noqa: E402
from donkey_kit.llm.client import LLMClient  # noqa: E402

_OPENAI_ON_HTTPX2 = built_on_httpx2(getattr(openai, "DefaultAsyncHttpxClient", None))

# A base URL with NO `/v1` — the verified ingress shape (docs/verified-apis.md §2).
# The trailing slash lets the OpenAI SDK append `chat/completions` directly.
_BASE_URL = "https://gw.example.internal/openai-sdk/"

# Explicit correlation/call-id header names so no UnverifiedValueWarning fires and
# the asserted keys are deterministic (the placeholder names warn once per key).
_CFG = DonkeyConfig(
    llm_proxy_url=_BASE_URL,
    llm_proxy_client_id="cid-123",
    llm_proxy_client_secret="csecret-456",
    correlation_header="x-correlation-id",
    call_id_header="x-donkey-request-id",
)

_CANNED_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hi there"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


def _recording_handler(seen: dict[str, object]):
    """A MockTransport handler that records the request our transport actually
    sent, then returns a canned chat completion."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=_CANNED_COMPLETION)

    return handler


def _assert_governed(seen: dict[str, object]) -> None:
    request = seen["request"]
    # openai built and sent the request through OUR client — the object our
    # transport saw is a plain httpx.Request, not something from httpx2.
    assert isinstance(request, httpx.Request)
    assert type(request).__module__.split(".")[0] == "httpx"
    # The verified client_id/client_secret pair is the real auth (docs/verified-apis.md §2/§3).
    assert request.headers["client_id"] == "cid-123"
    assert request.headers["client_secret"] == "csecret-456"
    # The OpenAI SDK still sets Authorization from its required api_key slot; that
    # slot is the sentinel the proxy ignores, never a real credential.
    assert request.headers["authorization"] == "Bearer client-id-enforced"
    # Correlation + per-call ids stamped by the transport.
    assert request.headers.get("x-correlation-id")
    assert request.headers.get("x-donkey-request-id")
    # Base URL used verbatim: no `/v1` injected at the ingress (docs/verified-apis.md §2).
    url = str(request.url)
    assert url == "https://gw.example.internal/openai-sdk/chat/completions"
    assert "/v1/" not in url


def _assert_bridged(client: object) -> None:
    """On openai>=3 the client's http_client is the core httpx2 bridge (#728)."""
    if not _OPENAI_ON_HTTPX2:
        return
    transport = type(getattr(getattr(client, "_client", None), "_transport", None)).__name__
    assert transport in ("DonkeyForwardingTransport", "DonkeyForwardingSyncTransport")


async def test_async_openai_client_drives_our_httpx_client_end_to_end() -> None:
    seen: dict[str, object] = {}
    shared = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(_recording_handler(seen)))
    async with shared:
        client = LLMClient(_CFG, shared).client()  # AsyncOpenAI
        _assert_bridged(client)
        resp = await client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )
    assert resp.choices[0].message.content == "hi there"
    assert resp.usage.total_tokens == 7
    _assert_governed(seen)


def test_sync_openai_client_drives_our_httpx_client_end_to_end() -> None:
    seen: dict[str, object] = {}
    # The async client is required by LLMClient's constructor but unused on the
    # sync path; the blocking transport is what we drive here.
    async_shared = DonkeyAsyncClient(
        _CFG, None, transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    sync_client = DonkeyClient(_CFG, transport=httpx.MockTransport(_recording_handler(seen)))
    with sync_client:
        llm = LLMClient(_CFG, async_shared, sync_http_client=lambda: sync_client)
        client = llm.client(sync=True)  # OpenAI
        _assert_bridged(client)
        resp = client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )
    assert resp.choices[0].message.content == "hi there"
    assert resp.usage.total_tokens == 7
    _assert_governed(seen)


# --- the bridge's streaming, refusal and lifecycle paths (#728) -------------

_SSE_BODY = (
    b'data: {"id":"c1","object":"chat.completion.chunk","created":0,"model":"gpt-4o",'
    b'"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"},"finish_reason":null}]}\n\n'
    b'data: {"id":"c1","object":"chat.completion.chunk","created":0,"model":"gpt-4o",'
    b'"choices":[{"index":0,"delta":{"content":" there"},"finish_reason":"stop"}]}\n\n'
    b"data: [DONE]\n\n"
)
_HI = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


class _Chunks(httpx.SyncByteStream, httpx.AsyncByteStream):
    """An SSE body that records whether the shared response was closed."""

    def __init__(self) -> None:
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield _SSE_BODY

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _SSE_BODY

    def close(self) -> None:
        self.closed = True

    async def aclose(self) -> None:
        self.closed = True


def _sse(chunks: _Chunks) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=chunks)


def _pii(request: httpx.Request) -> httpx.Response:
    body = {"error": {"type": "pii_detected", "message": "Found email at 10-25"}}
    return httpx.Response(403, json=body)


async def test_an_async_streamed_call_closes_the_shared_response() -> None:
    chunks = _Chunks()
    shared = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(lambda r: _sse(chunks)))
    async with shared:
        stream = await LLMClient(_CFG, shared).client().chat.completions.create(
            **_HI, stream=True
        )
        text = "".join([c.choices[0].delta.content or "" async for c in stream])
    assert text == "hi there"
    assert chunks.closed


def test_a_sync_streamed_call_closes_the_shared_response() -> None:
    chunks = _Chunks()
    async_shared = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(_pii))
    with DonkeyClient(_CFG, transport=httpx.MockTransport(lambda r: _sse(chunks))) as shared:
        llm = LLMClient(_CFG, async_shared, sync_http_client=lambda: shared)
        stream = llm.client(sync=True).chat.completions.create(**_HI, stream=True)
        text = "".join(c.choices[0].delta.content or "" for c in stream)
    assert text == "hi there"
    assert chunks.closed


async def test_an_async_refusal_classifies_with_the_ids_that_were_sent() -> None:
    # ``classify(exc.response)`` joins a refusal to its run on either stack (#738).
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _pii(request)

    shared = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler))
    async with shared:
        client = LLMClient(_CFG, shared).client()
        with run_scope("run-7"), pytest.raises(openai.PermissionDeniedError) as info:
            await client.chat.completions.create(**_HI)
    err = classify(info.value.response)
    assert isinstance(err, PIIDetected)
    (wire,) = sent
    assert err.correlation_id == "run-7"
    assert err.call_id == wire.headers["x-donkey-request-id"]


def test_a_sync_refusal_classifies_with_the_ids_that_were_sent() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _pii(request)

    async_shared = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(_pii))
    with DonkeyClient(_CFG, transport=httpx.MockTransport(handler)) as shared:
        client = LLMClient(_CFG, async_shared, sync_http_client=lambda: shared).client(sync=True)
        with run_scope("run-8"), pytest.raises(openai.PermissionDeniedError) as info:
            client.chat.completions.create(**_HI)
    err = classify(info.value.response)
    assert isinstance(err, PIIDetected)
    (wire,) = sent
    assert err.correlation_id == "run-8"
    assert err.call_id == wire.headers["x-donkey-request-id"]


async def test_closing_the_openai_clients_leaves_the_shared_clients_open() -> None:
    # #733 on both stacks: openai<3 gets the view, 3.x the bridge.
    seen: dict[str, object] = {}
    shared = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(_recording_handler(seen)))
    sync_shared = DonkeyClient(_CFG, transport=httpx.MockTransport(_recording_handler(seen)))
    llm = LLMClient(_CFG, shared, sync_http_client=lambda: sync_shared)
    async with shared:
        async with llm.client() as client:
            await client.chat.completions.create(**_HI)
        with llm.client(sync=True) as sync:
            sync.chat.completions.create(**_HI)
        assert not shared.is_closed
        assert not sync_shared.is_closed
        reply = await llm.client().chat.completions.create(**_HI)
    sync_shared.close()
    assert reply.choices[0].message.content == "hi there"


def test_the_blocking_client_refuses_a_prebuilt_request_in_a_token_mode() -> None:
    # The sync bridge builds its own httpx.Request, skipping
    # DonkeyClient.build_request, so send() refuses too: no token-mode request
    # leaves the blocking client unauthenticated (#509, #728).
    cfg = DonkeyConfig(
        llm_proxy_url=_BASE_URL,
        llm_proxy_auth="bearer",
        correlation_header="x-correlation-id",
        call_id_header="x-donkey-request-id",
    )
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    with DonkeyClient(cfg, transport=httpx.MockTransport(handler)) as client:
        request = httpx.Request("POST", _BASE_URL + "chat/completions", json={"model": "m"})
        with pytest.raises(ConfigError):
            client.send(request)
    assert sent == []
