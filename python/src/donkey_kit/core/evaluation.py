"""The run-end evaluation hook (BG §3.4, #232): a caller's score on the run span.

DDK does not score anything. ``donkey.evaluate(on="run_end", with_=scorer)``
registers a function the customer owns (Langfuse, Phoenix, Braintrust, or their
own). When a ``donkey.run()`` block exits, the scorer is called with the run's
ledger and its return value is written to :data:`DONKEY_EVALUATION_SCORE` on the
run span. Nothing else is recorded and no scoring model is shipped.

Opt-in and free when unset: a run only opens a run span when a scorer is
registered, so an unscored run does no extra work. A scorer that raises, or
returns something other than a number, is logged and the run is unaffected.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from contextvars import Token

    from .runledger import RunLedger

__all__ = [
    "DONKEY_EVALUATION_SCORE",
    "SPAN_RUN",
    "RunScorer",
    "TracerFactory",
    "begin_run_span",
    "current_run_span",
    "finish_run_span",
]

_log = logging.getLogger(__name__)

SPAN_RUN = "donkey.run"
DONKEY_EVALUATION_SCORE = "donkey.evaluation.score"

# What a customer's ``with_=`` scorer is: the run's ledger in, a score out (or None).
RunScorer = Callable[["RunLedger"], "float | None"]


class _Span(Protocol):
    """The two OpenTelemetry span methods the hook uses."""

    def set_attribute(self, key: str, value: float) -> None: ...

    def end(self) -> None: ...


#: Resolves the tracer, or ``None`` without OpenTelemetry. The facade passes
#: ``core.telemetry.tracer``: telemetry sits above correlation, which imports
#: this module, so the tracer is handed in rather than imported here.
TracerFactory = Callable[[], Any]

# The run span of the innermost scored ``donkey.run()`` block, per context. Set
# and reset by the scope that opened it, so a nested unscored run never sees it.
_run_span: ContextVar[_Span | None] = ContextVar("donkey_run_span", default=None)


def begin_run_span(tracer_factory: TracerFactory | None) -> list[Token[Any]]:
    """Open the run span for a scored run and make it the current span.

    Returns the context tokens the entry must reset on exit. Without OpenTelemetry
    there is no span to score, so the entry records ``None`` and scoring is skipped.
    """
    tracer = tracer_factory() if tracer_factory is not None else None
    if tracer is None:
        return [_run_span.set(None)]
    from opentelemetry import context, trace

    span: _Span = tracer.start_span(SPAN_RUN)
    return [context.attach(trace.set_span_in_context(span)), _run_span.set(span)]


def current_run_span() -> _Span | None:
    """The run span of the innermost scored run in this context, or ``None``."""
    return _run_span.get()


def finish_run_span(span: _Span | None, scorer: RunScorer, ledger: RunLedger | None) -> None:
    """Call ``scorer`` with the run's ledger and record its score on ``span``, then end it.

    A scorer error never reaches the caller. A ``None`` score, or no span, records nothing.
    """
    if span is None:
        return
    try:
        if ledger is not None:
            score = scorer(ledger)
            _record(span, score)
    except Exception:  # noqa: BLE001 — a customer scorer must never fail the run
        _log.warning("run-end scorer raised; the run's result is unaffected", exc_info=True)
    finally:
        span.end()


def _record(span: _Span, score: object) -> None:
    if score is None:
        return
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        _log.warning(
            "run-end scorer returned %s, not a number; no score recorded",
            type(score).__name__,
        )
        return
    span.set_attribute(DONKEY_EVALUATION_SCORE, float(score))
