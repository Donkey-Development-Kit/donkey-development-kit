"""Flush DDK's batched spans when the gateway kills the agent (BG §2.6, #220).

Spans leave the process from a ``BatchSpanProcessor`` thread on a timer, so an
agent that exits straight after a kill can take its last spans with it, the
kill's own span among them. :func:`flush` hands them to the exporter first, so
the final state is observable. It covers both providers DDK's spans can ride:
the DDK-scoped one :func:`~donkey_kit.core.telemetry.configure_otlp_export`
keeps here, and the process-global one (a host's, or DDK's installed by
opt-in). Bounded, and best effort: telemetry never turns a stop into a crash.

:func:`flush_owned` is the public flush behind ``Donkey.flush_telemetry()``
(#954): it flushes only the provider DDK built, scoped or installed as the
global by opt-in, and leaves a host's provider to the host.

It sits below telemetry and imports nothing from core, so the transport can
reach it without telemetry exposing its provider.
"""

from __future__ import annotations

from typing import Any, TypeVar

__all__ = ["FLUSH_TIMEOUT_MILLIS", "flush", "flush_owned", "keep"]

_Provider = TypeVar("_Provider")

#: The longest one provider's flush may hold up the caller.
FLUSH_TIMEOUT_MILLIS = 2_000

# The TracerProvider DDK built: the DDK-scoped one (telemetry keeps only its
# tracer, which cannot flush), or the one installed as the global by opt-in.
# Reset by `_reset_for_tests` below.
_owned: Any | None = None


def keep(provider: _Provider) -> _Provider:
    """Remember the ``provider`` DDK built for :func:`flush` and
    :func:`flush_owned`, and return it."""
    global _owned
    _owned = provider
    return provider


def _force_flush(provider: object, timeout_millis: int) -> bool:
    """``provider.force_flush(timeout_millis)``, ``True`` for a provider that holds
    nothing (the API's placeholders), and ``False`` if the flush raises."""
    force_flush = getattr(provider, "force_flush", None)
    if force_flush is None:
        return True
    try:
        return bool(force_flush(timeout_millis))
    except Exception:  # noqa: BLE001 — telemetry never raises into the caller
        return False


def flush(timeout_millis: int = FLUSH_TIMEOUT_MILLIS) -> None:
    """Export every span DDK's providers still hold, waiting up to
    ``timeout_millis`` for each. A no-op without ``[otel]``, and for the API's
    placeholder providers, which have no ``force_flush`` and hold nothing."""
    try:
        from opentelemetry import trace
    except ImportError:
        return
    providers = [trace.get_tracer_provider()]
    if _owned is not None and _owned is not providers[0]:
        providers.append(_owned)
    for provider in providers:
        _force_flush(provider, timeout_millis)  # a failed export must not mask the kill


def flush_owned(timeout_millis: int = FLUSH_TIMEOUT_MILLIS) -> bool:
    """Export every span the provider DDK built still holds, waiting up to
    ``timeout_millis``. ``True`` once they are exported, or when DDK built no
    provider (no endpoint, telemetry off, a host's provider already set, or no
    ``[otel]``); ``False`` if the flush timed out or failed."""
    return _owned is None or _force_flush(_owned, timeout_millis)


def _reset_for_tests() -> None:
    """Forget the kept provider so no test's export config outlives it."""
    global _owned
    _owned = None
