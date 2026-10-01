"""Wire data shared by the Anthropic adapter tests (#701). Not a test module.

Buffered responses replay the live ``Format=Anthropic`` capture in
``tests/fixtures/anypoint/anthropic_inbound/``. The event stream is synthetic
(the proxy capture has no streamed call): the Messages API event names and
shapes the ``anthropic`` SDK's stream accumulator reads.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "anthropic_inbound"
BASE_URL = "https://proxy/ddk-anthropic-inbound"
MESSAGES = f"{BASE_URL}/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
BODY = {
    "model": MODEL,
    "max_tokens": 16,
    "messages": [{"role": "user", "content": "Reply with the single word PONG."}],
}
CFG = DonkeyConfig(
    llm_proxy_url=BASE_URL,
    llm_proxy_client_id="cid-123",
    llm_proxy_client_secret="csecret-456",
    correlation_header="x-correlation-id",
    call_id_header="x-donkey-request-id",
)

_SSE_EVENTS = [
    ("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": MODEL,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 16, "cache_creation_input_tokens": 100,
                  "cache_read_input_tokens": 2000, "output_tokens": 1}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0,
                             "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "PO"}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0,
                             "delta": {"type": "text_delta", "text": "NG"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta",
                       "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 6}}),
    ("message_stop", {"type": "message_stop"}),
]
SSE_CHUNKS = [
    f"event: {name}\ndata: {json.dumps(data)}\n\n".encode() for name, data in _SSE_EVENTS
]


def success_response() -> httpx.Response:
    return httpx.Response(
        200,
        content=(FIXTURES / "responses.success.body.json").read_bytes(),
        headers=parse_headers((FIXTURES / "responses.success.headers.txt").read_text()),
    )


_LLM_PROXY = FIXTURES.parent / "llm_proxy"

# The live gateway refusals the bridge must hand back classifiable (#738):
# fixture stem → status.
REFUSALS = {"pii-detected": 403, "token-rate-limit": 429}


def refusal_response(name: str) -> httpx.Response:
    """A live LLM-proxy refusal (``tests/fixtures/anypoint/llm_proxy/reject.*``).
    The gateway applies the same policies whatever the inbound format."""
    body = next(_LLM_PROXY.glob(f"reject.{name}.body.*"))
    return httpx.Response(
        REFUSALS[name],
        content=body.read_bytes(),
        headers=parse_headers((_LLM_PROXY / f"reject.{name}.headers.txt").read_text()),
    )


class Chunks(httpx.AsyncByteStream):
    """A response body delivered chunk by chunk, as from the network, recording
    whether it was closed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


#: Anthropic's own request id, as the ``Format=Anthropic`` ingress passes it
#: through (``request-id``, the live capture's header; #827).
SSE_REQUEST_ID = "req_011CfStream"


def sse_response(stream: httpx.AsyncByteStream) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream", "request-id": SSE_REQUEST_ID},
        stream=stream,
    )


def shared_client(handler: Callable[[httpx.Request], httpx.Response]) -> DonkeyAsyncClient:
    return DonkeyAsyncClient(CFG, None, transport=httpx.MockTransport(handler))
