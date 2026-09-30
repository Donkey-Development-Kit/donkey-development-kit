"""Hand the shared ``DonkeyAsyncClient`` to a framework built on ``httpx2`` (#701).

``httpx2`` is Pydantic's continuation of ``httpx``: the same API, shipped as a
separate distribution with its own classes. ``anthropic>=1.0`` is built on it and
rejects any ``httpx`` object passed as ``http_client``, so the SDK's shared client
(an ``httpx.AsyncClient`` subclass) cannot be handed over directly.

:func:`bridged_client` returns an ``httpx2.AsyncClient`` whose transport sends
every request through the shared client. The framework keeps its own client type,
and the SDK keeps one HTTP stack: header injection, retries and the 401 refresh,
the GenAI span, budget and ``donkey.last_call`` all run in
``DonkeyAsyncClient.send()`` exactly as they do for an ``httpx`` framework. Only
the request and response objects are translated, never the wire call.

``httpx2`` is imported at module load, so import this module lazily, from an
adapter method, once the framework is known to need it (BG §1.8).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import httpx2

from ..core.transport import DonkeyAsyncClient

# The Stainless-generated SDKs (anthropic, openai) set this request header to
# "stream" for a raw streaming response (``.with_streaming_response``). That flag
# never reaches the transport any other way.
_RAW_RESPONSE_HEADER = "X-Stainless-Raw-Response"

# Dropped from a buffered response: httpx has already decoded the body, so its
# encoding and length no longer describe the bytes handed to httpx2.
_DECODED_BODY_HEADERS = frozenset({"content-encoding", "content-length"})


def wants_stream(request: httpx2.Request, body: bytes) -> bool:
    """Whether the framework asked for a streamed response.

    ``httpx2.AsyncClient.send(stream=...)`` is not passed to the transport, but
    ``DonkeyAsyncClient.send()`` must know: a streamed call gets a span that ends
    when the stream is drained, while a buffered call has its body read before
    ``donkey.last_call`` records its usage. The Messages API puts
    ``"stream": true`` in the JSON body of every streaming request.
    """
    if request.headers.get(_RAW_RESPONSE_HEADER) == "stream":
        return True
    if b'"stream"' not in body:
        return False
    try:
        payload = json.loads(body)
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("stream") is True


class _ForwardedStream(httpx2.AsyncByteStream):
    """The shared client's streamed body, as an ``httpx2`` byte stream. Yields the
    raw (still encoded) bytes, so ``httpx2`` decodes them exactly once, and closes
    the shared response on every close path, so its span ends and the connection
    returns to the pool."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._response.aiter_raw():
            yield chunk

    async def aclose(self) -> None:
        await self._response.aclose()


class DonkeyForwardingTransport(httpx2.AsyncBaseTransport):
    """An ``httpx2`` transport that sends through a :class:`DonkeyAsyncClient`."""

    def __init__(self, client: DonkeyAsyncClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        body = await request.aread()
        stream = wants_stream(request, body)
        forwarded = httpx.Request(
            request.method,
            str(request.url),
            headers=request.headers.raw,
            content=body,
            # httpx reads the timeout from the request, not the client, once a
            # request is built; without it the send would have no timeout at all.
            extensions={
                "timeout": request.extensions.get("timeout") or self._client.timeout.as_dict()
            },
        )
        response = await self._client.send(forwarded, stream=stream)
        extensions: dict[str, object] = {
            key: response.extensions[key]
            for key in ("http_version", "reason_phrase")
            if key in response.extensions
        }
        if stream and not response.is_stream_consumed:
            return httpx2.Response(
                response.status_code,
                headers=response.headers.raw,
                stream=_ForwardedStream(response),
                request=request,
                extensions=extensions,
            )
        # A buffered response, or one whose body was read before it was returned:
        # an ``httpx.Response(content=...)`` from ``simulate()``, ``donkey mock`` or
        # a mock transport reads itself on construction. Either way httpx has
        # decoded the body, and closing it ends a streamed call's span.
        await response.aclose()
        return httpx2.Response(
            response.status_code,
            headers=[
                (name, value)
                for name, value in response.headers.raw
                if name.decode("latin-1").lower() not in _DECODED_BODY_HEADERS
            ],
            content=response.content,
            request=request,
            extensions=extensions,
        )

    async def aclose(self) -> None:
        """Leave the shared client open. The framework closes its own client (for
        example on ``async with AsyncAnthropic(...)`` exit), but the shared client
        also serves ``donkey.llm``, the registry and every other adapter, and
        ``Donkey.aclose()`` owns its lifecycle."""


def bridged_client(client: DonkeyAsyncClient) -> httpx2.AsyncClient:
    """An ``httpx2.AsyncClient`` that sends every request through ``client``.

    Its default timeout and redirect policy mirror the shared client's, so a
    framework that reads them off its ``http_client`` behaves as it would with
    the shared client itself. ``trust_env`` is off because the shared client
    already applied the environment's settings to the connection that is used.
    """
    return httpx2.AsyncClient(
        transport=DonkeyForwardingTransport(client),
        timeout=httpx2.Timeout(**client.timeout.as_dict()),
        follow_redirects=client.follow_redirects,
        trust_env=False,
    )
