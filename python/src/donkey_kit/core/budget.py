"""Budget — the token-budget window, parsed from the proxy's budget headers
(BG §1.3, piece 2 of the six-piece minimum).

The developer never parses a header::

    donkey.budget.limit          # int  — tokens per window
    donkey.budget.remaining      # int  — from the last response
    donkey.budget.reset_at       # datetime — observed_at + reset (ms delta)
    donkey.budget.observed_at    # datetime — freshness of the above
    donkey.budget.fraction_used  # 0.0-1.0 — (limit-remaining)/limit

The governed proxy publishes the same three values in **two shapes**, and which
one arrives depends on the response class (#352):

- the numeric trio ``x-token-limit`` / ``x-token-remaining`` / ``x-token-reset``,
  VERIFIED (LIVE) — but only on the ``429`` that pacing exists to *prevent*; and
- the prose ``x-llm-proxy-ratelimit`` header, e.g.
  ``Token rate limit: 10000 tokens remaining of 10000 limit. Reset in 56711ms.``
  — the only budget signal on a successful ``200`` (and on the ``403`` refusal)
  once the ``llm-token-rate-limit`` policy is applied.

:meth:`Budget.observe` reads both: the numeric trio wins where present, and the
prose header fills any field the trio leaves unset. Without the prose fallback a
``Budget`` could only ever be populated by a rate-limit rejection, leaving
:meth:`Budget.pace` inert against a live gateway until after the first ``429``.

Honest limitation (upstream gap #2): the gateway exposes budget **only in-band**.
There is no budget-query endpoint, so ``remaining`` is only as fresh as the last
call, and a brand-new process knows nothing until its first request returns.
``observed_at`` exists precisely so nobody mistakes stale data for live data — an
unobserved ``Budget`` (no call has returned yet) reports every field as ``None``,
never a misleading zero.

Both the numeric ``x-token-reset`` and the prose ``Reset in … ms`` are
**milliseconds *to* reset** — a delta, not an epoch (docs/verified-apis.md §4) —
so ``reset_at`` is anchored to ``observed_at`` the same way ``errors._retry_after``
treats the header.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx

from .errors import BudgetReserveReached

# The three numeric budget headers, VERIFIED (LIVE) against the token-rate-limit
# policy (docs/verified-apis.md §4, row `Token rate limiting`) — present on the
# 429. Named here so the one place that parses them is greppable.
LIMIT_HEADER = "x-token-limit"
REMAINING_HEADER = "x-token-remaining"
RESET_HEADER = "x-token-reset"

# The prose fallback: one header carrying all three values as an English sentence,
# emitted on the 200 (and the 403) that never carry the numeric trio (#352). Listed
# in the API's CORS `exposedHeaders`, so it is an intended part of the contract.
RATELIMIT_HEADER = "x-llm-proxy-ratelimit"

# Matches `… 10000 tokens remaining of 10000 limit. Reset in 56711ms.`. Requires
# all three values: a partial or reworded sentence fails to match and is treated
# as no signal (verification discipline — never guess at an unexpected wire shape). `search`, not
# `match`, so the leading `Token rate limit:` label is not load-bearing.
_RATELIMIT_RE = re.compile(
    r"(?P<remaining>\d+)\s+tokens?\s+remaining\s+of\s+(?P<limit>\d+)\s+limit\b"
    r".*?reset\s+in\s+(?P<reset>\d+)\s*ms",
    re.IGNORECASE | re.DOTALL,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_int(raw: str | None) -> int | None:
    """A budget header as an int, or ``None`` if absent or non-numeric. Garbage is
    ignored rather than fatal (verification discipline: never let an unexpected wire value crash the
    caller's request path)."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_ratelimit_prose(raw: str | None) -> tuple[int | None, int | None, int | None]:
    """The prose ``x-llm-proxy-ratelimit`` header as ``(limit, remaining, reset_ms)``,
    or an all-``None`` tuple when the header is absent, partial, or reworded.

    Like :func:`_parse_int` this never raises on unexpected input (verification discipline): an
    unparseable sentence is simply "no signal", never a crash on the caller's
    request path. All three values must be present in the recognised shape or the
    whole header is discarded — a half-parsed budget is worse than none."""
    if raw is None:
        return None, None, None
    m = _RATELIMIT_RE.search(raw)
    if m is None:
        return None, None, None
    return int(m["limit"]), int(m["remaining"]), int(m["reset"])


class Budget:
    """The token-budget window for one :class:`~donkey_kit.Donkey`, updated
    in-band from each response's budget headers (numeric ``x-token-*`` or the
    prose ``x-llm-proxy-ratelimit`` fallback, #352).

    Per-``Donkey``, never global: two instances with different credentials hold
    independent state. Construct empty (unobserved); :meth:`observe` mutates it
    from a response. Reads are cheap attribute/property access — no I/O.
    """

    def __init__(self) -> None:
        self.limit: int | None = None
        self.remaining: int | None = None
        self.reset_at: datetime | None = None
        self.observed_at: datetime | None = None

    @property
    def fraction_used(self) -> float | None:
        """Fraction of the window consumed, ``0.0``-``1.0``, or ``None`` while
        unobserved or when ``limit`` is non-positive (no window to divide by).
        Clamped: a ``remaining`` above ``limit`` (a window that reset between
        calls) reports ``0.0`` rather than a negative fraction."""
        if self.limit is None or self.remaining is None or self.limit <= 0:
            return None
        used = (self.limit - self.remaining) / self.limit
        return min(1.0, max(0.0, used))

    def observe(self, response: httpx.Response, *, now: datetime | None = None) -> None:
        """Update from a response's budget headers.

        Reads two shapes and merges them (#352): the numeric trio ``x-token-*``
        (present on the ``429``) wins where present, and the prose
        ``x-llm-proxy-ratelimit`` (the only signal on a ``200``/``403``) fills any
        field the trio leaves unset. Without the prose fallback a live ``Budget``
        would populate only from a rate-limit rejection.

        A response carrying **neither** shape is a defined no-op — the object stays
        exactly as it was (``observed_at`` unchanged), so a happy-path call without
        budget headers never resets freshness or raises. If any field is resolved,
        ``observed_at`` is stamped and each resolved field is applied; a missing or
        unparseable value leaves that field untouched.

        ``now`` is injectable for tests; production passes nothing and the wall
        clock (UTC) is used.
        """
        headers = response.headers
        limit = _parse_int(headers.get(LIMIT_HEADER))
        remaining = _parse_int(headers.get(REMAINING_HEADER))
        reset_ms = _parse_int(headers.get(RESET_HEADER))

        # Prose fallback for whatever the numeric trio did not supply. The trio is
        # preferred field-by-field: prose only fills a field left `None` above.
        if limit is None or remaining is None or reset_ms is None:
            p_limit, p_remaining, p_reset = _parse_ratelimit_prose(headers.get(RATELIMIT_HEADER))
            if limit is None:
                limit = p_limit
            if remaining is None:
                remaining = p_remaining
            if reset_ms is None:
                reset_ms = p_reset

        if limit is None and remaining is None and reset_ms is None:
            return  # no budget signal on this response; nothing observed

        ts = now if now is not None else _utcnow()
        self.observed_at = ts
        if limit is not None:
            self.limit = limit
        if remaining is not None:
            self.remaining = remaining
        if reset_ms is not None:
            self.reset_at = ts + timedelta(milliseconds=reset_ms)

    @asynccontextmanager
    async def pace(self, *, reserve: float = 0.0) -> AsyncIterator[None]:
        """Guard a request so it is refused *before* it crosses your reserve, not
        after a 429 comes back (BG §1.3, #186).

        ``reserve`` is the fraction of the window to keep in hand (``0.0``-``1.0``):
        ``reserve=0.10`` trips at 90% used, ``reserve=0.0`` (the default) only at
        full exhaustion. On entry, if the observed :attr:`fraction_used` has reached
        ``1.0 - reserve``, :class:`~donkey_kit.core.errors.BudgetReserveReached` is
        raised and the guarded block never runs — so the request that would cross
        the reserve is never issued. Recover with :meth:`wait_for_reset` and retry
        the same work::

            while True:
                try:
                    async with donkey.budget.pace(reserve=0.05):
                        await enrich(batch)
                except BudgetReserveReached:
                    await donkey.budget.wait_for_reset()
                    continue
                break

        An **unobserved** budget (no call has returned yet, so
        :attr:`fraction_used` is ``None``) lets the block through: with nothing
        observed there is no basis to refuse, and blocking forever on a cold start
        would be worse than one request that discovers the real headroom. An
        observation whose :attr:`reset_at` has elapsed is stale for the same reason,
        so :meth:`pace` no longer refuses requests. A later response carrying a
        recognised budget signal updates the observed fields; a refreshed
        :attr:`reset_at` in the future makes the reserve guard active again. A
        response that does not supply a refreshed future :attr:`reset_at` leaves the
        stale pass-through open. Before ``reset_at`` — or when no reset time was
        observed — the reserve guard remains active.
        """
        if not 0.0 <= reserve <= 1.0:
            raise ValueError(f"reserve must be within [0.0, 1.0], got {reserve!r}")
        used = self.fraction_used
        window_expired = self.reset_at is not None and _utcnow() >= self.reset_at
        if used is not None and used >= 1.0 - reserve and not window_expired:
            raise BudgetReserveReached(
                f"Budget reserve reached: {used:.1%} of the window used, "
                f"reserve is {reserve:.1%} (trips at {1.0 - reserve:.1%}).",
                fraction_used=used,
                reserve=reserve,
                reset_at=self.reset_at,
            )
        yield

    async def wait_for_reset(self, *, now: datetime | None = None) -> None:
        """Sleep until :attr:`reset_at`, then return — the recovery half of pacing
        (BG §1.3, #186).

        A single sleep, never a spin loop. If the window is unobserved
        (:attr:`reset_at` is ``None``) or already past, this returns immediately —
        there is nothing to wait for. ``now`` is injectable for tests; production
        passes nothing and the wall clock (UTC) is used.
        """
        if self.reset_at is None:
            return
        current = now if now is not None else _utcnow()
        delay = (self.reset_at - current).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
