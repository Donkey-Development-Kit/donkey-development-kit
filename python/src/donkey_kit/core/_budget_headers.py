"""Reading the budget signal off a response, for :mod:`donkey_kit.core.budget`
(BG §1.3): the header parsers (#352) and the receive-time stamp that orders
observations (#311). Private: ``budget`` is the public face.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

__all__ = [
    "parse_go_duration",
    "parse_int",
    "parse_ratelimit_prose",
    "received_at",
    "stamp_received",
]

# Matches `… 10000 tokens remaining of 10000 limit. Reset in 56711ms.`. Requires
# all three values: a partial or reworded sentence fails to match and is treated
# as no signal (verification discipline — never guess at an unexpected wire shape). `search`, not
# `match`, so the leading `Token rate limit:` label is not load-bearing.
_RATELIMIT_RE = re.compile(
    r"(?P<remaining>\d+)\s+tokens?\s+remaining\s+of\s+(?P<limit>\d+)\s+limit\b"
    r".*?reset\s+in\s+(?P<reset>\d+)\s*ms",
    re.IGNORECASE | re.DOTALL,
)

# A Go duration (``time.ParseDuration``): one or more decimal numbers, each with a
# unit, e.g. ``12ms``, ``0s``, ``1m30s``, ``1.5h`` (#1025). Go's "µs" is accepted in
# both its micro sign (U+00B5) and Greek mu (U+03BC) spellings, as Go does.
_GO_UNIT_MS = {
    "ns": 1e-6,
    "us": 1e-3,
    "µs": 1e-3,
    "μs": 1e-3,
    "ms": 1.0,
    "s": 1e3,
    "m": 60e3,
    "h": 3600e3,
}
_GO_PART = r"(\d+(?:\.\d*)?|\.\d+)(ns|us|µs|μs|ms|s|m|h)"
_GO_DURATION_RE = re.compile(rf"(?:{_GO_PART})+")
_GO_PART_RE = re.compile(_GO_PART)

# The response extension holding when its headers arrived (#311). The governed
# transport sets it as the response comes back from the wire, before the body is
# read or any hook runs, so it orders observations by receipt, not by when
# ``observe()`` happens to run.
_RECEIVED_AT = "donkey_received_at"


def stamp_received(response: httpx.Response) -> httpx.Response:
    """Record on ``response`` the time its headers arrived, and return it. The
    governed transports call it; :meth:`Budget.observe` reads it (#311)."""
    response.extensions[_RECEIVED_AT] = datetime.now(timezone.utc)
    return response


def received_at(response: httpx.Response) -> datetime | None:
    stamp = response.extensions.get(_RECEIVED_AT)
    return stamp if isinstance(stamp, datetime) else None


def parse_int(raw: str | None) -> int | None:
    """A budget header as an int, or ``None`` if absent or non-numeric. Garbage is
    ignored rather than fatal (verification discipline: never let an unexpected wire value crash the
    caller's request path)."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_go_duration(raw: str | None) -> float | None:
    """A Go duration string (``12ms``, ``0s``, ``1m30s``) as milliseconds, or
    ``None`` if absent or not that shape (#1025).

    The upstream provider's ``x-ratelimit-reset-*`` passthrough uses this form,
    not the gateway's ms count. A unit is required: a bare number (Azure OpenAI
    sends ``0`` even while the window is part-spent) has no stated unit, so it is
    no signal rather than a guessed one. Like :func:`parse_int` it never raises.
    A leading sign is not accepted: a time *until* reset is never negative."""
    if raw is None:
        return None
    value = raw.strip()
    if _GO_DURATION_RE.fullmatch(value) is None:
        return None
    return sum(float(n) * _GO_UNIT_MS[unit] for n, unit in _GO_PART_RE.findall(value))


def parse_ratelimit_prose(raw: str | None) -> tuple[int | None, int | None, int | None]:
    """The prose ``x-llm-proxy-ratelimit`` header as ``(limit, remaining, reset_ms)``,
    or an all-``None`` tuple when the header is absent, partial, or reworded.

    Like :func:`parse_int` this never raises on unexpected input (verification discipline): an
    unparseable sentence is simply "no signal", never a crash on the caller's
    request path. All three values must be present in the recognised shape or the
    whole header is discarded — a half-parsed budget is worse than none."""
    if raw is None:
        return None, None, None
    m = _RATELIMIT_RE.search(raw)
    if m is None:
        return None, None, None
    return int(m["limit"]), int(m["remaining"]), int(m["reset"])
