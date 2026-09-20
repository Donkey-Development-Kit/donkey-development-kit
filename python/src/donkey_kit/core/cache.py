"""In-memory TTL cache for tokens and registry lookups (the framework-free
core, cache.py).

Deliberately tiny and dependency-free. Honours the ``DONKEY_NO_CACHE=1`` escape
hatch (BG §2.7). Not thread-safe across processes — it is a per-process cache for a
single agent run, which is all the SDK needs.

Time is injected (``clock``) so tests do not sleep and so the module stays pure.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


def _cache_disabled() -> bool:
    return os.environ.get("DONKEY_NO_CACHE", "").strip() in ("1", "true", "yes")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, *, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._clock = clock
        self._store: dict[str, _Entry[T]] = {}

    def get(self, key: str) -> T | None:
        if _cache_disabled():
            return None
        entry = self._store.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, key: str, value: T, *, ttl_s: float | None = None) -> None:
        ttl = self._ttl if ttl_s is None else ttl_s
        self._store[key] = _Entry(value=value, expires_at=self._clock() + ttl)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)
