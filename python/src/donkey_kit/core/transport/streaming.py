"""The streaming side of a governed send (BG §1.6, #193, #805, #728).

The byte-stream wrappers that end a detached GenAI span when a streamed body
closes, the SSE scanner that fills its usage from the terminal event, and the
bounded read of a refused stream's body so it is classified like a buffered one.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator

import httpx

from ..lastcall import observe_usage, parse_usage, usage_mapping
from ..telemetry import GenAiSpan

__all__: list[str] = []

_log = logging.getLogger(__name__)

# Streaming (SSE) responses carry no usage on the envelope; it lives in a
# terminal event, captured by the span-closing stream wrapper (#193).
_STREAM_CONTENT_TYPE = "text/event-stream"
# A non-2xx body on a stream request is read up to this many bytes before the
# span is recorded, so classify() sees the same JSON a buffered refusal has
# (#805). Proxy error envelopes are a few hundred bytes; past the cap the body is
# left for the caller and the span falls back to what the headers say.
_ERROR_BODY_CAP = 64 * 1024


# --- streaming span lifecycle (#193, BG §1.6) -------------------------------
# A streamed completion carries its usage in the TERMINAL SSE event, read by the
# caller long after send() has returned. So the streaming span is DETACHED
# (start_genai_span) and handed to a wrapper around the response's byte stream:
# httpx routes BOTH iteration and response.aclose()/close() through
# ``response.stream`` (verified against httpx 0.28), so a wrapper there sees every
# close path — full drain, mid-iteration abandonment, and exception — and is the
# one place that can fill usage and end the span exactly once.


def _is_streaming_success(response: httpx.Response) -> bool:
    """True when a ``stream=True`` request returned a streamable 2xx SSE body —
    the only case whose span must outlive ``send()``. A non-2xx refusal or a
    buffered non-SSE 2xx on a stream request is finished inline instead."""
    if response.status_code // 100 != 2:
        return False
    return _STREAM_CONTENT_TYPE in response.headers.get("content-type", "")


# A refusal on a stream request arrives unread: classify() would hit
# ``ResponseNotRead`` and the span would lose its decision, policy type and ERROR
# status (#805). The SDKs read an error body anyway, so the transport reads it
# first — bounded by _ERROR_BODY_CAP, and replayed untouched when it overflows.


def _is_read(response: httpx.Response) -> bool:
    try:
        response.content  # noqa: B018 — raises ResponseNotRead on an unread body
    except httpx.ResponseNotRead:
        return False
    return True


class _ReplayAsyncStream(httpx.AsyncByteStream):
    """An over-cap error body: the chunks already read, then the rest, unchanged."""

    def __init__(
        self, head: list[bytes], rest: AsyncIterator[bytes], inner: httpx.AsyncByteStream
    ) -> None:
        self._head, self._rest, self._inner = head, rest, inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._head:
            yield chunk
        async for chunk in self._rest:
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _ReplaySyncStream(httpx.SyncByteStream):
    """Blocking twin of :class:`_ReplayAsyncStream`."""

    def __init__(
        self, head: list[bytes], rest: Iterator[bytes], inner: httpx.SyncByteStream
    ) -> None:
        self._head, self._rest, self._inner = head, rest, inner

    def __iter__(self) -> Iterator[bytes]:
        yield from self._head
        yield from self._rest

    def close(self) -> None:
        self._inner.close()


async def _aread_error_body(response: httpx.Response) -> None:
    """Read an unread non-2xx stream body of at most :data:`_ERROR_BODY_CAP`
    bytes, leaving the response as if it had been buffered. A larger body is put
    back as a replay stream, still unread."""
    inner = response.stream
    if not isinstance(inner, httpx.AsyncByteStream) or _is_read(response):
        return
    rest = inner.__aiter__()
    head: list[bytes] = []
    size = 0
    async for chunk in rest:
        head.append(chunk)
        size += len(chunk)
        if size > _ERROR_BODY_CAP:
            response.stream = _ReplayAsyncStream(head, rest, inner)
            return
    await inner.aclose()
    response.stream = httpx.ByteStream(b"".join(head))
    await response.aread()


def _read_error_body(response: httpx.Response) -> None:
    """Blocking twin of :func:`_aread_error_body`."""
    inner = response.stream
    if not isinstance(inner, httpx.SyncByteStream) or _is_read(response):
        return
    rest = iter(inner)
    head: list[bytes] = []
    size = 0
    for chunk in rest:
        head.append(chunk)
        size += len(chunk)
        if size > _ERROR_BODY_CAP:
            response.stream = _ReplaySyncStream(head, rest, inner)
            return
    inner.close()
    response.stream = httpx.ByteStream(b"".join(head))
    response.read()


class _SseUsageScanner:
    """Incrementally scans an SSE byte stream for the terminal ``usage`` event,
    keeping the latest observed token counts (#193, #307).

    Line-buffered, so it reconstructs ``data:`` lines across arbitrary chunk
    boundaries, and it only parses JSON for lines that mention ``usage`` — a
    cheap substring test skips the vast majority of delta events, so memory and
    CPU stay bounded no matter how long the completion is (buffering the whole
    body would defeat the point of streaming). ``counts`` holds the six usage
    fields (:func:`parse_usage`); a scanned value fills its field, so a later
    partial event never nulls a count already seen."""

    __slots__ = ("_buf", "counts")

    def __init__(self) -> None:
        self._buf: bytes = b""
        self.counts: dict[str, int | None] = parse_usage(None)

    def feed(self, chunk: bytes) -> None:
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            self._scan(line)

    def close(self) -> None:
        """Scan any trailing partial line (a final event without a newline)."""
        if self._buf:
            self._scan(self._buf)
            self._buf = b""

    def _scan(self, line: bytes) -> None:
        # ``"usage`` matches both OpenAI's ``"usage"`` and Gemini's ``"usageMetadata"``.
        if b'"usage' not in line:
            return
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            return
        payload = stripped[len(b"data:") :].strip()
        try:
            obj = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        usage = usage_mapping(obj)
        if usage is None:
            return
        for field, value in parse_usage(usage).items():
            if value is not None:
                self.counts[field] = value


class _SpanClosingStream:
    """Shared finalize logic for the stream wrappers: merge the scanned usage
    into ``donkey.last_call``, record it onto the detached span and end it,
    exactly once. The span recording is best-effort — telemetry must never break
    stream teardown, nor drop the usage ``last_call`` reports (#817)."""

    def __init__(self, gspan: GenAiSpan) -> None:
        self._gspan = gspan
        self._scanner = _SseUsageScanner()
        self._finalized = False

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._scanner.close()
        counts = self._scanner.counts
        try:
            self._gspan.record(
                input_tokens=counts["input_tokens"],
                output_tokens=counts["output_tokens"],
                cached_tokens=counts["cached_tokens"],
                cache_write_tokens=counts["cache_write_tokens"],
                reasoning_tokens=counts["reasoning_tokens"],
            )
        except Exception:  # noqa: BLE001 — telemetry must never break teardown
            _log.debug("recording streamed usage on the span failed", exc_info=True)
        # The record set in _on_response had no usage (the body was unread on a
        # stream); merge the terminal event's counts into it now (#307). The stream
        # is consumed in the same context that set the record, so this updates the
        # caller's own donkey.last_call. Outside the telemetry guard: a failing
        # span recorder must not drop the usage (#817).
        observe_usage(counts)
        self._gspan.end()


class _SpanClosingAsyncStream(_SpanClosingStream, httpx.AsyncByteStream):
    """Wraps a streaming response's byte stream so the GenAI span closes when the
    stream does — on full drain, mid-iteration abandonment, or exception — with
    ``gen_ai.usage.*`` filled from the terminal SSE event (#193)."""

    def __init__(self, inner: httpx.AsyncByteStream, gspan: GenAiSpan) -> None:
        super().__init__(gspan)
        self._inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._scanner.feed(chunk)
            yield chunk

    async def aclose(self) -> None:
        try:
            self._finalize()
        finally:
            await self._inner.aclose()


class _SpanClosingSyncStream(_SpanClosingStream, httpx.SyncByteStream):
    """Blocking twin of :class:`_SpanClosingAsyncStream`."""

    def __init__(self, inner: httpx.SyncByteStream, gspan: GenAiSpan) -> None:
        super().__init__(gspan)
        self._inner = inner

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._inner:
            self._scanner.feed(chunk)
            yield chunk

    def close(self) -> None:
        try:
            self._finalize()
        finally:
            self._inner.close()
