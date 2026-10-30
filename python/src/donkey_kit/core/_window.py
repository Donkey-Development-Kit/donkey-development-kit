"""One rate-limit window's observed state, and the two operations on it, for
:mod:`donkey_kit.core.budget` (BG §1.3). Every window the budget tracks (the
gateway's token and request windows, #974, and the upstream provider's, #1025)
shares them. Private: ``budget`` is the public face.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

__all__ = ["Window", "reached", "record"]

_log = logging.getLogger(__name__)


class Window:
    """One rate-limit window's observed state: the five fields every window
    shares."""

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
        calls) reports ``0.0`` rather than a negative fraction, and one below
        zero reports ``1.0``. ``remaining`` is clamped before the division, so a
        header too large for a float cannot overflow it."""
        if self.limit is None or self.remaining is None or self.limit <= 0:
            return None
        remaining = min(max(self.remaining, 0), self.limit)
        return (self.limit - remaining) / self.limit


def _reset_at(window: Window, reset_ms: float, ts: datetime) -> datetime | None:
    """``ts`` plus ``reset_ms``, or ``None`` when that is no reset time: negative
    (a time *until* reset never is), not a number, or past what a ``datetime``
    holds. The value is the upstream's, so it never fails the send (#1444)."""
    if reset_ms >= 0:  # False for NaN too
        try:
            return ts + timedelta(milliseconds=reset_ms)
        except OverflowError:
            pass
    _log.debug("ignored an out-of-range rate-limit reset on the %s window", type(window).__name__)
    return None


def record(
    window: Window,
    limit: int | None,
    remaining: int | None,
    reset_ms: float | None,
    ts: datetime,
    *,
    replace: bool = False,
) -> bool:
    """Apply whichever fields resolved to ``window``, and say whether it applied.
    All ``None`` is a no-op, so a response without this window's signal leaves
    ``observed_at`` untouched. So is an observation older than the window's
    ``observed_at``: the fresher state stays (#311). A reset with no
    representable time counts as ``None`` (#1444). ``replace`` clears the three
    fields first, so the observation stands alone (the upstream window, #1025)."""
    reset_at = None if reset_ms is None else _reset_at(window, reset_ms, ts)
    if limit is None and remaining is None and reset_at is None:
        return False
    if window.observed_at is not None and ts < window.observed_at:
        return False
    if replace:
        window.limit = window.remaining = window.reset_at = None
    window.observed_at = ts
    if limit is not None:
        window.limit = limit
    if remaining is not None:
        window.remaining = remaining
    if reset_at is not None:
        window.reset_at = reset_at
    return True


def reached(window: Window, reserve: float, current: datetime) -> float | None:
    """``window``'s used fraction if it has reached ``1.0 - reserve`` and has not
    yet reset, else ``None``."""
    used = window.fraction_used
    expired = window.reset_at is not None and current >= window.reset_at
    if used is not None and used >= 1.0 - reserve and not expired:
        return used
    return None
