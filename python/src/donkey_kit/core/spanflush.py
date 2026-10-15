"""Flush DDK's batched spans when the gateway kills the agent (BG §2.6, #220).

Spans leave the process from a ``BatchSpanProcessor`` thread on a timer, so an
agent that exits straight after a kill can take its last spans with it, the
kill's own span among them. :func:`flush` hands them to the exporter first, so
the final state is observable. It covers both providers DDK's spans can ride:
the DDK-scoped one :func:`~donkey_kit.core.telemetry.configure_otlp_export`
keeps here, and the process-global one (a host's, or DDK's installed by
opt-in). Bounded, and best effort: telemetry never turns a stop into a crash.

It sits below telemetry and imports nothing from core, so the transport can
reach it without telemetry exposing its provider.
"""

from __future__ import annotations

import contextlib
from typing import Any, TypeVar

__all__ = ["FLUSH_TIMEOUT_MILLIS", "flush", "keep"]

_Provider = TypeVar("_Provider")

#: The longest one provider's flush may hold up the caller.
FLUSH_TIMEOUT_MILLIS = 2_000

# The DDK-scoped TracerProvider: telemetry keeps only its tracer, which cannot
# flush. Reset by `_reset_for_tests` below.
_scoped: Any | None = None


def keep(provider: _Provider) -> _Provider:
    """Remember the DDK-scoped ``provider`` for :func:`flush`, and return it."""
    global _scoped
    _scoped = provider
    return provider


def flush(timeout_millis: int = FLUSH_TIMEOUT_MILLIS) -> None:
    """Export every span DDK's providers still hold, waiting up to
    ``timeout_millis`` for each. A no-op without ``[otel]``, and for the API's
    placeholder providers, which have no ``force_flush`` and hold nothing."""
    try:
        from opentelemetry import trace
    except ImportError:
        return
    providers = [trace.get_tracer_provider()]
    if _scoped is not None and _scoped is not providers[0]:
        providers.append(_scoped)
    for provider in providers:
        force_flush = getattr(provider, "force_flush", None)
        if force_flush is None:
            continue
        with contextlib.suppress(Exception):  # a failed export must not mask the kill
            force_flush(timeout_millis)


def _reset_for_tests() -> None:
    """Forget the kept provider so no test's export config outlives it."""
    global _scoped
    _scoped = None
