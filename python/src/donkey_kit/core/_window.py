"""One rate-limit window's observed state, and the two operations on it, for
:mod:`donkey_kit.core.budget` (BG §1.3). Every window the budget tracks (the
gateway's token and request windows, #974, and the upstream provider's, #1025)
shares them. Private: ``budget`` is the public face.
"""

from __future__ import annotations

from datetime import datetime, timedelta

__all__ = ["Window", "reached", "record"]


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
        calls) reports ``0.0`` rather than a negative fraction."""
        if self.limit is None or self.remaining is None or self.limit <= 0:
            return None
        used = (self.limit - self.remaining) / self.limit
        return min(1.0, max(0.0, used))


def record(
    window: Window,
    limit: int | None,
    remaining: int | None,
    reset_ms: float | None,
    ts: datetime,
) -> bool:
    """Apply whichever fields resolved to ``window``, and say whether it applied.
    All ``None`` is a no-op, so a response without this window's signal leaves
    ``observed_at`` untouched. So is an observation older than the window's
    ``observed_at``: the fresher state stays (#311)."""
    if limit is None and remaining is None and reset_ms is None:
        return False
    if window.observed_at is not None and ts < window.observed_at:
        return False
    window.observed_at = ts
    if limit is not None:
        window.limit = limit
    if remaining is not None:
        window.remaining = remaining
    if reset_ms is not None:
        window.reset_at = ts + timedelta(milliseconds=reset_ms)
    return True


def reached(window: Window, reserve: float, current: datetime) -> float | None:
    """``window``'s used fraction if it has reached ``1.0 - reserve`` and has not
    yet reset, else ``None``."""
    used = window.fraction_used
    expired = window.reset_at is not None and current >= window.reset_at
    if used is not None and used >= 1.0 - reserve and not expired:
        return used
    return None
