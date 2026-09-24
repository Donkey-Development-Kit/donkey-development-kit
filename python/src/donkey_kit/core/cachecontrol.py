"""Cache steering — the per-request controls that steer the gateway's semantic
cache (docs/verified-apis.md §2 "Semantic caching", BG §1.1, #587).

The Anypoint LLM Gateway can front a proxy with a **semantic-caching policy**:
it embeds each prompt, does an approximate-nearest-neighbour lookup, and on a
hit serves the stored completion with no provider round-trip. The policy takes
five request headers that steer it per call (skip it, look-up-but-don't-store,
override the TTL / similarity threshold, or partition the similarity filter by a
principal id) and reports what it did on the response (surfaced on
``donkey.last_call`` — see :mod:`donkey_kit.core.lastcall`).

This is **not** client-side semantic caching (on the *Do not build* list). The
SDK caches nothing and computes no embeddings; it is the single header-injection
point that makes the gateway's own cache *steerable*, and the single
response-parse point that makes it *observable*.

**The ergonomic mirrors ``donkey.run(...)``.** A :class:`CacheControls` is bound
to a :class:`~contextvars.ContextVar` for a block; the transport reads it per
send and injects the ``x-cache-*`` headers (VERIFIED lowercase, #588). Being a
contextvar, it reaches every governed call in the block — including calls on
framework-spawned ``asyncio`` tasks, which copy the current context — with no
threading through framework state, exactly like the run id and cost tags::

    with donkey.cache(skip=True):
        ...                          # every governed call in the block bypasses the cache

The same documented degradation as the run id / cost tags applies (BG §1.8): a
``connection_kwargs()`` / LiteLLM-backed adapter that does not route through the
shared transport never sees the contextvar, so its calls are not steered.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, fields
from typing import Any

from . import _verify
from .errors import ConfigError

# A principal-id long enough to be a smuggled document is refused — the same
# ceiling and control-char rule cost tags apply, and for the same reason: the
# value is emitted as a request header, so a newline would corrupt the request.
_MAX_PRINCIPAL_ID_LEN = 256


def _validate_principal_id(value: str) -> None:
    """Reject a ``principal_id`` that could not be a legitimate id: empty,
    over-long, or carrying a control character (a newline is header injection).
    Raises :class:`ConfigError`."""
    if value == "":
        raise ConfigError(
            "Cache principal_id is empty. Omit it, or give it a value — an empty "
            "principal id is a mistake, not 'no principal' (that is unset)."
        )
    if len(value) > _MAX_PRINCIPAL_ID_LEN:
        raise ConfigError(
            f"Cache principal_id is {len(value)} chars; the maximum is "
            f"{_MAX_PRINCIPAL_ID_LEN}. It is a short identifier, not a payload."
        )
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ConfigError(
            "Cache principal_id contains a control character. It is emitted as a "
            "request header, so a newline or control char would corrupt the request."
        )


@dataclass(frozen=True)
class CacheControls:
    """The per-request semantic-cache steering controls (docs/verified-apis.md §2, #587).

    Every field is optional and defaults to ``None`` (unset — the header is
    omitted, and the gateway applies its configured default). Construction
    validates every set value, so an invalid control is caught where it is set,
    not on the first request.

    * ``skip`` — bypass the cache policy entirely (passthrough to the provider).
    * ``no_store`` — look up, but do not write the result on a miss.
    * ``ttl`` — override the entry time-to-live, in seconds (a positive int).
    * ``threshold`` — override the similarity threshold, a float in ``[0.0, 1.0]``.
    * ``principal_id`` — override the id the similarity filter partitions on.
    """

    skip: bool | None = None
    no_store: bool | None = None
    ttl: int | None = None
    threshold: float | None = None
    principal_id: str | None = None

    def __post_init__(self) -> None:
        # ``bool`` subclasses ``int``, so guard the numeric fields against a
        # ``True``/``False`` slipping in where a number is meant.
        if self.skip is not None and not isinstance(self.skip, bool):
            raise ConfigError(f"Cache 'skip' must be a bool or None, got {self.skip!r}.")
        if self.no_store is not None and not isinstance(self.no_store, bool):
            raise ConfigError(f"Cache 'no_store' must be a bool or None, got {self.no_store!r}.")
        if self.ttl is not None:
            if isinstance(self.ttl, bool) or not isinstance(self.ttl, int) or self.ttl <= 0:
                raise ConfigError(
                    f"Cache 'ttl' must be a positive int (seconds), got {self.ttl!r}."
                )
        if self.threshold is not None:
            if isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float)):
                raise ConfigError(
                    f"Cache 'threshold' must be a number in [0.0, 1.0], got {self.threshold!r}."
                )
            if not 0.0 <= float(self.threshold) <= 1.0:
                raise ConfigError(
                    f"Cache 'threshold' must be within [0.0, 1.0], got {self.threshold!r}."
                )
        if self.principal_id is not None:
            _validate_principal_id(self.principal_id)

    @property
    def is_empty(self) -> bool:
        """True when no control is set — the transport injects nothing and any
        outer scope's controls are left in place (mirrors ``CostTags.is_empty``)."""
        return all(getattr(self, f.name) is None for f in fields(self))

    def headers(self) -> Iterator[tuple[str, str]]:
        """Yield ``(header_name, value)`` for each control to inject, using the
        VERIFIED lowercase ``x-cache-*`` names from :mod:`donkey_kit.core._verify`.

        The two boolean controls are emitted as the literal ``"true"`` **only when
        ``True``** — the gateway steers on the header's *presence* (the live
        capture only ever sent ``true``), so a ``False``/``None`` omits the header
        (do not steer) rather than inventing an untested ``x-cache-skip: false``
        (verification discipline)."""
        if self.skip is True:
            yield _verify.CACHE_SKIP_HEADER.get(), "true"
        if self.no_store is True:
            yield _verify.CACHE_NO_STORE_HEADER.get(), "true"
        if self.ttl is not None:
            yield _verify.CACHE_TTL_HEADER.get(), str(self.ttl)
        if self.threshold is not None:
            # Plain float text (e.g. ``"0.5"``); the gateway accepts it and echoes
            # a four-dp form (``"0.5000"``) — matching the live capture (#588).
            yield _verify.CACHE_THRESHOLD_HEADER.get(), str(self.threshold)
        if self.principal_id is not None:
            yield _verify.CACHE_PRINCIPAL_ID_HEADER.get(), self.principal_id


# Per-request cache-steering controls bound by ``donkey.cache(...)`` (#587). Like
# the run id and cost tags it is contextvar-bound, so a block's controls reach
# every model call inside — including calls on framework-spawned asyncio tasks,
# which copy the current context — with no threading through framework state.
_cache_controls: ContextVar[CacheControls | None] = ContextVar(
    "donkey_cache_controls", default=None
)


def current_cache_controls() -> CacheControls | None:
    """The cache-steering controls bound by the enclosing ``donkey.cache(...)``
    block, or ``None`` outside one. The transport reads this per send and injects
    the ``x-cache-*`` headers for each set control."""
    return _cache_controls.get()


class CacheScope:
    """A **dual sync/async** context manager that binds :class:`CacheControls` to
    :data:`_cache_controls` for the block (BG §1.1, #587).

    This is what ``donkey.cache(...)`` returns, so the same object works under both
    ``with donkey.cache(...)`` and ``async with donkey.cache(...)`` — binding a
    contextvar needs no ``await``, so both entry paths share one implementation
    (identical to :class:`~donkey_kit.core.telemetry.RunScope`). Nested scopes
    rebind and restore via the contextvar token, so an inner block's controls
    shadow an outer block's and the outer controls are restored on exit.

    An empty controls object binds nothing, so a no-argument ``donkey.cache()`` is
    an inert block that leaves any outer scope's controls in place (mirrors
    ``RunScope``'s handling of empty cost tags)."""

    __slots__ = ("_controls", "_token")

    def __init__(self, controls: CacheControls) -> None:
        self._controls = controls if not controls.is_empty else None
        self._token: Any = None

    def _bind(self) -> None:
        if self._controls is not None:
            self._token = _cache_controls.set(self._controls)

    def _unbind(self) -> None:
        if self._token is not None:
            _cache_controls.reset(self._token)
            self._token = None

    def __enter__(self) -> CacheControls | None:
        self._bind()
        return self._controls

    def __exit__(self, *exc: Any) -> None:
        self._unbind()

    async def __aenter__(self) -> CacheControls | None:
        self._bind()
        return self._controls

    async def __aexit__(self, *exc: Any) -> None:
        self._unbind()


def cache_scope(controls: CacheControls) -> CacheScope:
    """Build a :class:`CacheScope` — the dual sync/async binding behind
    ``donkey.cache(...)`` (#587)."""
    return CacheScope(controls)
