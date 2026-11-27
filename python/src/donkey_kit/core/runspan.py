"""The run span: one ``donkey.run`` span per ``donkey.run()`` block, carrying the
run's rollup (#317) and, when a scorer is registered, its score (BG §3.4, #232).

Every run with a tracer gets a ``donkey.run`` span, and every model call in the
block is its child. On exit the span records what the run cost, read from the
run ledger: call and tool-call counts, refusals by type, token totals, wall time,
and the gateway's spend when it reports one (never computed locally). A scorer
registered through ``donkey.evaluate(on="run_end", with_=...)`` is then called
with the same ledger, and the number it returns lands as
:data:`DONKEY_EVALUATION_SCORE`. DDK ships no scoring model.

Without OpenTelemetry there is no span, so nothing is recorded and the scorer is
not called. A scorer that raises, or returns something other than a number, is
logged and the run is unaffected.
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

# The rollup attributes on the run span (#317). Keys are flat, so a refusal type
# is ``donkey.run.refusals.<ErrorClass>`` beside the total ``donkey.run.refusals``.
_ATTR_PREFIX = "donkey.run."
_ATTR_LEDGER_AVAILABLE = _ATTR_PREFIX + "ledger_available"
_ATTR_DURATION = _ATTR_PREFIX + "duration_s"
_ATTR_MODEL_CALLS = _ATTR_PREFIX + "model_calls"
_ATTR_TOOL_CALLS = _ATTR_PREFIX + "tool_calls"
_ATTR_REFUSALS = _ATTR_PREFIX + "refusals"
_ATTR_INPUT_TOKENS = _ATTR_PREFIX + "input_tokens"
_ATTR_OUTPUT_TOKENS = _ATTR_PREFIX + "output_tokens"
_ATTR_TOTAL_TOKENS = _ATTR_PREFIX + "total_tokens"
_ATTR_CACHED_TOKENS = _ATTR_PREFIX + "cached_tokens"
_ATTR_CACHE_WRITE_TOKENS = _ATTR_PREFIX + "cache_write_tokens"
_ATTR_REASONING_TOKENS = _ATTR_PREFIX + "reasoning_tokens"
_ATTR_SPEND = _ATTR_PREFIX + "spend"

# What a customer's ``with_=`` scorer is: the run's ledger in, a score out (or None).
RunScorer = Callable[["RunLedger"], "float | None"]


class _Span(Protocol):
    """The two OpenTelemetry span methods the run span uses."""

    def set_attribute(self, key: str, value: bool | int | float | str) -> None: ...

    def end(self) -> None: ...


#: Resolves the tracer, or ``None`` without OpenTelemetry. The facade passes
#: ``core.telemetry.tracer``: telemetry sits above correlation, which imports
#: this module, so the tracer is handed in rather than imported here.
TracerFactory = Callable[[], Any]

# The run span of the innermost ``donkey.run()`` block, per context. Set and reset
# by the scope that opened it, so a nested run without a tracer never sees it.
_run_span: ContextVar[_Span | None] = ContextVar("donkey_run_span", default=None)


def begin_run_span(tracer_factory: TracerFactory | None) -> list[Token[Any]]:
    """Open the ``donkey.run`` span for a run and make it the current span.

    Returns the context tokens the entry must reset on exit. Without a tracer, or
    without OpenTelemetry, there is no span: the entry records ``None``.
    """
    tracer = tracer_factory() if tracer_factory is not None else None
    if tracer is None:
        return [_run_span.set(None)]
    from opentelemetry import context, trace

    span: _Span = tracer.start_span(SPAN_RUN)
    return [context.attach(trace.set_span_in_context(span)), _run_span.set(span)]


def current_run_span() -> _Span | None:
    """The run span of the innermost run in this context, or ``None``."""
    return _run_span.get()


def finish_run_span(span: _Span | None, scorer: RunScorer | None, ledger: RunLedger | None) -> None:
    """Record the run's rollup on ``span``, score it if a scorer is registered,
    then end the span.

    A scorer error never reaches the caller. A ``None`` score records nothing.
    """
    if span is None:
        return
    if ledger is not None:
        _record_rollup(span, ledger)
    try:
        if ledger is not None and scorer is not None:
            _record(span, scorer(ledger))
    except Exception:  # noqa: BLE001 — a customer scorer must never fail the run
        _log.warning("run-end scorer raised; the run's result is unaffected", exc_info=True)
    finally:
        span.end()


def _record_rollup(span: _Span, ledger: RunLedger) -> None:
    span.set_attribute(_ATTR_LEDGER_AVAILABLE, ledger.available)
    span.set_attribute(_ATTR_DURATION, ledger.duration_s)
    if not ledger.available:
        # A surface the ledger cannot see: its zeros would read as "no calls".
        return
    span.set_attribute(_ATTR_MODEL_CALLS, ledger.call_count)
    span.set_attribute(_ATTR_TOOL_CALLS, ledger.tool_calls)
    span.set_attribute(_ATTR_REFUSALS, ledger.refusal_count)
    for kind, count in ledger.refusal_counts.items():
        span.set_attribute(f"{_ATTR_REFUSALS}.{kind}", count)
    usage = ledger.usage
    span.set_attribute(_ATTR_INPUT_TOKENS, usage.input_tokens)
    span.set_attribute(_ATTR_OUTPUT_TOKENS, usage.output_tokens)
    span.set_attribute(_ATTR_TOTAL_TOKENS, usage.total_tokens)
    span.set_attribute(_ATTR_CACHED_TOKENS, usage.cached_tokens)
    span.set_attribute(_ATTR_CACHE_WRITE_TOKENS, usage.cache_write_tokens)
    span.set_attribute(_ATTR_REASONING_TOKENS, usage.reasoning_tokens)
    if ledger.spend is not None:
        span.set_attribute(_ATTR_SPEND, ledger.spend)


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
