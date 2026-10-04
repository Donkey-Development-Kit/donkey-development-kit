"""Hand a shared governed client to a framework built on ``httpx2`` (#701, #728).

``httpx2`` is Pydantic's continuation of ``httpx``: the same API, shipped as a
separate distribution with its own classes. ``anthropic>=1.0`` is built on it and
rejects any ``httpx`` object passed as ``http_client``, and ``openai>=3`` types
its ``http_client`` as an ``httpx2`` client, so the SDK's shared clients (``httpx``
subclasses) are not handed over directly.

:func:`bridged_client` returns an ``httpx2.AsyncClient`` whose transport sends
every request through the shared async client, and :func:`bridged_sync_client`
the blocking twin over a :class:`~donkey_kit.core.transport.DonkeyClient`. The
framework keeps its own client type, and the SDK keeps one HTTP stack: header
injection, retries and the 401 refresh, the GenAI span, budget and
``donkey.last_call`` all run in the shared client's ``send()`` exactly as they
do for an ``httpx`` framework. Only
the request and response objects are translated, never the wire call. The ids the
shared client sends are copied back onto the framework's request, so
``classify(exc.response)`` joins a refusal to its run on either stack (#738).

It lives in core, beside the clients it bridges, so ``donkey.llm`` and every
adapter can use it without crossing the layering contract (#728). ``httpx2`` is
not a base dependency and is imported at module load, so import this module
lazily, from the method that builds a framework client, once that framework is
known to need it (BG §1.8). Nothing imports it at package import.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator

import httpx
import httpx2

from .async_client import DonkeyAsyncClient
from .sync_client import DonkeyClient

__all__ = [
    "DonkeyForwardingSyncTransport",
    "DonkeyForwardingTransport",
    "bridged_client",
    "bridged_sync_client",
    "wants_stream",
]

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

    Only these two Stainless-style signals are recognised. A framework that
    streams some other way gets a buffered call: correct, but read in full
    before the framework sees the first byte.
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


def _copy_sent_ids(forwarded: httpx.Request, request: httpx2.Request) -> None:
    """Copy the ids the shared client stamped on ``forwarded`` onto the
    framework's own ``request`` (#738).

    ``httpx2.AsyncClient`` binds the returned response to ``request``, whatever
    the transport passes, so that is where ``classify(exc.response)`` reads the
    sent correlation and call ids (``core/errors._sent_ids``). The ``donkey_*``
    extensions name the headers that carry them. Only those two headers are
    copied, so the consumer credentials stay off the framework's request.
    """
    for key, value in forwarded.extensions.items():
        if not key.startswith("donkey_"):
            continue
        request.extensions[key] = value
        if key in ("donkey_correlation_header", "donkey_call_id_header"):
            sent = forwarded.headers.get(value)
            if sent is not None:
                request.headers[value] = sent


def _forwarded(
    request: httpx2.Request, body: bytes, client: httpx.AsyncClient | httpx.Client
) -> httpx.Request:
    """The framework's ``request`` as an ``httpx`` request for the shared client.
    httpx reads the timeout from the request, not the client, once a request is
    built; without it the send would have no timeout at all."""
    return httpx.Request(
        request.method,
        str(request.url),
        headers=request.headers.raw,
        content=body,
        extensions={"timeout": request.extensions.get("timeout") or client.timeout.as_dict()},
    )


def _extensions(response: httpx.Response) -> dict[str, object]:
    return {
        key: response.extensions[key]
        for key in ("http_version", "reason_phrase")
        if key in response.extensions
    }


def _buffered(response: httpx.Response, request: httpx2.Request) -> httpx2.Response:
    """A closed, read ``response`` as an ``httpx2`` response. httpx has decoded
    the body, so its encoding and length headers are dropped."""
    return httpx2.Response(
        response.status_code,
        headers=[
            (name, value)
            for name, value in response.headers.raw
            if name.decode("latin-1").lower() not in _DECODED_BODY_HEADERS
        ],
        content=response.content,
        request=request,
        extensions=_extensions(response),
    )


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


class _ForwardedSyncStream(httpx2.SyncByteStream):
    """Blocking twin of :class:`_ForwardedStream`."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    def __iter__(self) -> Iterator[bytes]:
        yield from self._response.iter_raw()

    def close(self) -> None:
        self._response.close()


class DonkeyForwardingTransport(httpx2.AsyncBaseTransport):
    """An ``httpx2`` transport that sends through a :class:`DonkeyAsyncClient`."""

    def __init__(self, client: DonkeyAsyncClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        """Send ``request`` through the shared client and translate the response."""
        body = await request.aread()
        stream = wants_stream(request, body)
        forwarded = _forwarded(request, body, self._client)
        response = await self._client.send(forwarded, stream=stream)
        _copy_sent_ids(forwarded, request)
        if stream and not response.is_stream_consumed:
            return httpx2.Response(
                response.status_code,
                headers=response.headers.raw,
                stream=_ForwardedStream(response),
                request=request,
                extensions=_extensions(response),
            )
        # A buffered response, or one whose body was read before it was returned:
        # an ``httpx.Response(content=...)`` from ``simulate()``, ``donkey mock`` or
        # a mock transport reads itself on construction. Either way httpx has
        # decoded the body, and closing it ends a streamed call's span.
        await response.aclose()
        return _buffered(response, request)

    async def aclose(self) -> None:
        """Leave the shared client open. The framework closes its own client (for
        example on ``async with AsyncAnthropic(...)`` exit), but the shared client
        also serves ``donkey.llm``, the registry and every other adapter, and
        ``Donkey.aclose()`` owns its lifecycle."""


class DonkeyForwardingSyncTransport(httpx2.BaseTransport):
    """Blocking twin of :class:`DonkeyForwardingTransport`, over a :class:`DonkeyClient`."""

    def __init__(self, client: DonkeyClient) -> None:
        self._client = client

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        """Send ``request`` through the shared client and translate the response."""
        body = request.read()
        stream = wants_stream(request, body)
        forwarded = _forwarded(request, body, self._client)
        response = self._client.send(forwarded, stream=stream)
        _copy_sent_ids(forwarded, request)
        if stream and not response.is_stream_consumed:
            return httpx2.Response(
                response.status_code,
                headers=response.headers.raw,
                stream=_ForwardedSyncStream(response),
                request=request,
                extensions=_extensions(response),
            )
        response.close()  # see DonkeyForwardingTransport.handle_async_request
        return _buffered(response, request)

    def close(self) -> None:
        """Leave the shared client open (see :meth:`DonkeyForwardingTransport.aclose`)."""


class _ReusableBridgedClient(httpx2.AsyncClient):
    """A bridged client that closing leaves usable, like a view (#733).

    For a framework that builds and closes an ``AsyncOpenAI`` around the same
    ``http_client`` on every request (Strands runs ``async with
    AsyncOpenAI(**client_args)``): a plain ``httpx2.AsyncClient`` refuses every
    send once closed, so the second request would fail. It owns nothing to
    release: its transport forwards to the shared client and it has no pool."""

    async def aclose(self) -> None:
        """Stay usable; ``Donkey.aclose()`` owns the shared client."""

    async def __aenter__(self) -> _ReusableBridgedClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stay usable (see :meth:`aclose`)."""


class _ReusableBridgedSyncClient(httpx2.Client):
    """Blocking twin of :class:`_ReusableBridgedClient`."""

    def close(self) -> None:
        """Stay usable; ``Donkey.close()`` owns the shared client."""

    def __enter__(self) -> _ReusableBridgedSyncClient:
        return self

    def __exit__(self, *exc: object) -> None:
        """Stay usable (see :meth:`close`)."""


def bridged_client(client: DonkeyAsyncClient, *, reusable: bool = False) -> httpx2.AsyncClient:
    """An ``httpx2.AsyncClient`` that sends every request through ``client``.

    Its default timeout and redirect policy mirror the shared client's, so a
    framework that reads them off its ``http_client`` behaves as it would with
    the shared client itself. ``trust_env`` is off because the shared client
    already applied the environment's settings to the connection that is used.
    Closing it leaves ``client`` open. With ``reusable=True`` closing it also
    leaves the bridged client itself usable, as closing a view does: for an
    ``http_client`` handed out once and closed by the framework after each
    request (the framework adapters, #728).
    """
    cls = _ReusableBridgedClient if reusable else httpx2.AsyncClient
    return cls(
        transport=DonkeyForwardingTransport(client),
        timeout=httpx2.Timeout(**client.timeout.as_dict()),
        follow_redirects=client.follow_redirects,
        trust_env=False,
    )


def bridged_sync_client(client: DonkeyClient, *, reusable: bool = False) -> httpx2.Client:
    """Blocking twin of :func:`bridged_client`: an ``httpx2.Client`` that sends
    every request through ``client``, for ``OpenAI(http_client=...)`` on
    ``openai>=3`` (#728). ``client`` refuses to send in a token auth mode, so
    neither does this."""
    cls = _ReusableBridgedSyncClient if reusable else httpx2.Client
    return cls(
        transport=DonkeyForwardingSyncTransport(client),
        timeout=httpx2.Timeout(**client.timeout.as_dict()),
        follow_redirects=client.follow_redirects,
        trust_env=False,
    )
