"""In-process refusal injection for ``donkey.simulate()`` (#190, BG §1.5).

The in-process sibling of the donkey mock server (BG §1.4): instead of a TCP
server, it swaps a fixture-returning transport onto a live ``DonkeyAsyncClient``
/ ``DonkeyClient`` for the next N calls, so a unit test can drive the refusal
branch of an agent with **no network and no server**. It replays the **same
captured fixtures** ``core.errors.classify()`` and the simulator app are tested
against, so the injected refusal is the real shape — and it asserts that
round-trip at enter time ("same files, both fail together", BG §1.4/§1.5).

Framework isolation (§1.1): this module imports only ``httpx``, the
framework-free ``core.errors`` taxonomy, and the sibling fixtures loader — never
a web framework — so it is safe on the base import path. It is a dev-only
simulator sibling, kept out of the five production layers by the import-linter
contract; ``donkey.py`` (the orchestrator) imports it lazily inside
:meth:`Donkey.simulate`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from typing import Any, Protocol, cast

import httpx

from ..core.errors import (
    AuthError,
    ContentSafetyBlocked,
    DonkeyError,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
    UpstreamModelError,
    UpstreamRequestError,
    classify,
)
from .app import SIMULATOR_HEADER
from .fixtures import Fixture, load, replay_headers

# Exception type -> the fixture shape whose captured bytes classify() maps back
# to that exception. The inverse of the simulator's model-id sentinel table; the
# classify() round-trip asserted in :func:`_resolve` proves the mapping still
# holds. Keys are matched by EXACT type (a base and its subclass map to different
# shapes), so ``PolicyViolation`` selects the generic content-moderation shape
# while its subclasses select their specific captures.
#
# ``PromptInjectionBlocked`` maps to the injection-protection shape; the
# Regex-Prompt-Guard shape (#289) also classifies to it but is not the injected
# representative. ``ContentSafetyBlocked`` maps to the documented content-safety
# shape (#289); an *undiscriminated* moderation 4xx still falls through to the
# generic ``PolicyViolation`` (the "content-moderation" shape).
_EXC_TO_SHAPE: dict[type[DonkeyError], str] = {
    TokenBudgetExceeded: "token-rate-limit",
    PIIDetected: "pii-detected",
    PromptInjectionBlocked: "injection-protection",
    ContentSafetyBlocked: "content-safety",
    UpstreamRequestError: "model-not-found",
    UpstreamModelError: "upstream-5xx",
    AuthError: "client-id-missing",
    PolicyViolation: "content-moderation",
}


class _SwappableClient(Protocol):
    """The transport-swap seam both Donkey HTTP clients expose (§2.3, BG §1.1).
    Typed loosely on purpose — the sync and async clients carry different
    ``httpx`` transport types, and :class:`_FixtureTransport` satisfies both."""

    _transport: Any

    def _swap_transport(self, transport: Any) -> None: ...


class _Countdown:
    """A shared refusal budget. One instance is shared by every target client in
    a single ``simulate()`` block, so ``times`` counts calls across the async and
    sync surfaces together — 'the next N calls through a Donkey'."""

    def __init__(self, remaining: int) -> None:
        self.remaining = remaining

    def take(self) -> bool:
        if self.remaining > 0:
            self.remaining -= 1
            return True
        return False


class _FixtureTransport(httpx.AsyncBaseTransport, httpx.BaseTransport):
    """Fronts a wrapped transport, returning the fixture for the first ``times``
    *logical* calls, then delegating so call N+1 proceeds normally.

    Implements both the sync and async transport protocols so one instance can
    front either a ``DonkeyClient`` or a ``DonkeyAsyncClient``.

    ``times`` counts logical calls, not wire sends: the SDK's own retry loop
    re-sends the *same* ``httpx.Request`` object, and a retryable injected shape
    (503) would otherwise burn one count per retry. Keying the 'already served'
    check on request identity means the whole retry sequence of one logical call
    consumes exactly one count.
    """

    def __init__(self, inner: Any, fixture: Fixture, countdown: _Countdown) -> None:
        self._inner = inner
        self._fixture = fixture
        self._countdown = countdown
        # Identity of the last request we served, so its retries stay served
        # without decrementing the shared countdown again.
        self._served_id: int | None = None

    def _should_serve(self, request: httpx.Request) -> bool:
        if self._served_id == id(request):
            return True  # a retry of an already-counted logical call
        if self._countdown.take():
            self._served_id = id(request)
            return True
        return False

    def _build(self, request: httpx.Request) -> httpx.Response:
        headers = replay_headers(self._fixture)
        if self._fixture.content_type is not None:
            headers["content-type"] = self._fixture.content_type
        # BG §1.4 honesty rule: a simulated refusal is identifiable as simulated
        # in a log/trace, never mistakable for a real gateway response.
        headers[SIMULATOR_HEADER] = "true"
        return httpx.Response(
            status_code=self._fixture.status,
            headers=headers,
            content=self._fixture.body,
            request=request,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._should_serve(request):
            return self._build(request)
        # ``_inner`` is Any (it is either transport kind; this instance only ever
        # gets the matching one), so the delegated result needs a cast.
        return cast(httpx.Response, self._inner.handle_request(request))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._should_serve(request):
            return self._build(request)
        return cast(httpx.Response, await self._inner.handle_async_request(request))


def _resolve(error: type[DonkeyError]) -> Fixture:
    """Resolve the requested exception type to the fixture that classify() maps
    back to it, asserting that round-trip. Raises ``TypeError`` for a non-error
    type and ``ValueError`` for an unmapped one (§0.3: no silent miss)."""
    if not (isinstance(error, type) and issubclass(error, DonkeyError)):
        raise TypeError(
            f"simulate() expects a DonkeyError subclass, got {error!r}. "
            f"Supported: {', '.join(sorted(e.__name__ for e in _EXC_TO_SHAPE))}."
        )
    shape = _EXC_TO_SHAPE.get(error)
    if shape is None:
        raise ValueError(
            f"simulate() cannot inject {error.__name__}: no captured fixture maps "
            f"back to it via classify(). Supported: "
            f"{', '.join(sorted(e.__name__ for e in _EXC_TO_SHAPE))}."
        )
    fixture = load(shape)
    # 'Same files, both fail together': the injected bytes MUST classify() back
    # to the requested type, or the fixture and the taxonomy have drifted apart.
    built = httpx.Response(
        status_code=fixture.status,
        headers={
            **replay_headers(fixture),
            **({"content-type": fixture.content_type} if fixture.content_type else {}),
        },
        content=fixture.body,
    )
    classified = classify(built)
    if not isinstance(classified, error):
        raise AssertionError(  # pragma: no cover — a drift guard, not a runtime path
            f"simulate() fixture {shape!r} classifies to {type(classified).__name__}, "
            f"not the requested {error.__name__}; the fixture and classify() have drifted."
        )
    return fixture


@contextlib.contextmanager
def simulate(
    clients: Sequence[_SwappableClient],
    error: type[DonkeyError],
    *,
    times: int = 1,
) -> Iterator[None]:
    """Swap a fixture-returning transport onto each client for the next ``times``
    calls, restoring every client's previous transport on exit (even on error).

    ``clients`` share one countdown, so ``times`` counts calls across them
    together. Nesting composes: an inner block wraps the outer block's transport
    and restores it on exit.
    """
    if times < 1:
        raise ValueError(f"times must be >= 1, got {times}")
    fixture = _resolve(error)
    countdown = _Countdown(times)
    restore: list[tuple[_SwappableClient, Any]] = []
    try:
        for client in clients:
            previous = client._transport
            client._swap_transport(_FixtureTransport(previous, fixture, countdown))
            restore.append((client, previous))
        yield
    finally:
        # Restore in reverse so composed swaps unwind cleanly.
        for client, previous in reversed(restore):
            client._swap_transport(previous)
