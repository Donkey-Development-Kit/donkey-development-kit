"""openai 3.x drives our httpx client end to end (#18, BG §1.8).

The scare with `openai>=3.0` is that it retyped ``AsyncOpenAI(http_client=…)``
to ``httpx2.AsyncClient`` — a class from a *separate* distribution
(``httpx2``), distinct from the ``httpx`` our ``DonkeyAsyncClient`` /
``DonkeyClient`` subclass. That is a **typecheck-only** mismatch: when an
``http_client`` is injected, openai builds and sends every request *through
that client*, so ``httpx2`` never touches our code path. These tests pin that
runtime truth so a future openai release that actually broke injection would
fail CI loudly (floors-never-ceilings, docs/verified-apis.md §8.1) — rather
than us discovering it in a customer sandbox.

Guarded by ``importorskip("openai")`` at module top so the base-only CI job
(``.[dev]`` only, no ``[llm]``) skips it cleanly; it runs in the full/nightly
matrix where openai is installed and always the newest release.
"""

from __future__ import annotations

import pytest

openai = pytest.importorskip("openai")

import httpx  # noqa: E402

from donkey_kit.core.config import DonkeyConfig  # noqa: E402
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient  # noqa: E402
from donkey_kit.llm.client import LLMClient  # noqa: E402

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


async def test_async_openai_client_drives_our_httpx_client_end_to_end() -> None:
    seen: dict[str, object] = {}
    shared = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(_recording_handler(seen)))
    async with shared:
        client = LLMClient(_CFG, shared).client()  # AsyncOpenAI
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
        resp = client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )
    assert resp.choices[0].message.content == "hi there"
    assert resp.usage.total_tokens == 7
    _assert_governed(seen)
