"""Governance for the work an agent does on behalf of an inbound request
(BG §2.9.1, #225).

An agent served over A2A makes its downstream model and tool calls for a caller.
:func:`inbound_task` turns the caller's request headers into the run state an
outbound ``donkey.run()`` binds, so those calls join the caller's run:

* **Correlation.** The caller's ``X-Correlation-Id`` is adopted when it is a
  well-formed id, and a fresh one is minted when it is absent or malformed
  (:func:`correlation_id_from_headers`). Every downstream call in the task sends
  it, so the gateway joins the caller's request and the agent's calls on one key.
* **Cost tags and business group.** The configured tags ride every downstream
  call, as for any governed call. A ``donkey.run()`` or ``donkey.attribution()``
  inside the handler layers on top. A caller's own cost headers are not adopted:
  who pays is the serving agent's configuration, not the caller's claim.
* **One span per task.** A ``donkey.a2a.task`` SERVER span wraps the handler, so
  the ``donkey.llm.chat`` spans of its downstream calls are its children. A W3C
  ``traceparent`` from the caller makes it a child of the caller's span.
* **Typed refusals.** The typed-refusal bridge applies on exit, so a framework's
  wrapped refusal leaves the block as its typed ``DonkeyError``.

Framework-free: headers are a plain mapping, so the A2A server is one caller and
an inbound MCP or HTTP surface can be another.
"""

from __future__ import annotations

import contextlib
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from . import _verify, _wire
from .correlation import (
    CorrelationSource,
    current_correlation_id,
    current_correlation_source,
    current_cost_tags,
    new_correlation_id,
    run_scope,
)
from .telemetry import (
    DONKEY_CORRELATION_ID,
    DONKEY_CORRELATION_SOURCE,
    DONKEY_COST_ENV,
    DONKEY_COST_PROJECT,
    DONKEY_COST_TEAM,
)

if TYPE_CHECKING:
    from .cost import CostTags
    from .refusals import TypedRefusals

__all__ = [
    "DONKEY_A2A_QUALITY_EVALUATION",
    "DONKEY_A2A_QUALITY_EXPLANATION",
    "DONKEY_A2A_QUALITY_SCORE",
    "DONKEY_A2A_QUALITY_STATUS",
    "DONKEY_CORRELATION_SOURCE",
    "SPAN_A2A_CALL",
    "SPAN_A2A_TASK",
    "CorrelationSource",
    "InboundRun",
    "OutboundRun",
    "correlation_id_from_headers",
    "inbound_task",
    "outbound_call",
]

#: The span one outbound A2A call opens around card discovery and sending.
SPAN_A2A_CALL = "donkey.a2a.call"

# Stable DDK annotation keys; these describe the gateway verdict, not an eval framework.
DONKEY_A2A_QUALITY_EVALUATION = "donkey.a2a.quality.evaluation"
DONKEY_A2A_QUALITY_SCORE = "donkey.a2a.quality.score"
DONKEY_A2A_QUALITY_STATUS = "donkey.a2a.quality.status"
DONKEY_A2A_QUALITY_EXPLANATION = "donkey.a2a.quality.explanation"

#: The span one inbound A2A task opens around its handler.
SPAN_A2A_TASK = "donkey.a2a.task"

# What an adopted id may look like. It is sent on as a request header on every
# downstream call, so it must be header-safe, and it is the gateway's join key, so
# it must be short. The SDK's own ids (32 hex) and the gateway's dashed UUIDs fit.
_ADOPTABLE = re.compile(r"[A-Za-z0-9._:-]{1,128}")


@dataclass(frozen=True)
class InboundRun:
    """The run an inbound task is bound to.

    Attributes:
        correlation_id: The run id every downstream call in the task sends.
        source: ``"adopted"`` when the caller sent it, ``"minted"`` when not.
    """

    correlation_id: str
    source: CorrelationSource


@dataclass(frozen=True)
class OutboundRun:
    """The run and W3C headers for an outbound A2A call (#1395).

    Attributes:
        correlation_id: The shared run id for card discovery and sending.
        trace_headers: Immutable W3C trace-context headers, without baggage.
    """

    correlation_id: str
    trace_headers: Mapping[str, str]
    _span: Any = field(default=None, repr=False, compare=False)
    _capture_content: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_headers", MappingProxyType(dict(self.trace_headers)))

    def record_quality(self, headers: Mapping[str, str]) -> None:
        """Record captured quality annotations; explanation text requires opt-in (#1399).

        Missing or malformed values are omitted, never treated as call failures.
        Only captured evaluation/status values are exported without content opt-in.
        No score range is inferred from the single captured verdict.
        """
        if self._span is None:
            return
        values = {key.lower(): value for key, value in headers.items()}
        for header, attribute, expected in (
            (_wire.A2A_QUALITY_EVALUATION_HEADER, DONKEY_A2A_QUALITY_EVALUATION, "yes"),
            (_wire.A2A_QUALITY_STATUS_HEADER, DONKEY_A2A_QUALITY_STATUS, "completed"),
        ):
            if values.get(header) == expected:
                self._span.set_attribute(attribute, expected)
        score = values.get(_wire.A2A_QUALITY_SCORE_HEADER, "")
        if score and len(score) <= 64:
            try:
                number = float(score)
            except ValueError:
                pass
            else:
                if math.isfinite(number):
                    self._span.set_attribute(DONKEY_A2A_QUALITY_SCORE, number)
        explanation = values.get(_wire.A2A_QUALITY_EXPLANATION_HEADER)
        if self._capture_content and explanation:
            self._span.set_attribute(DONKEY_A2A_QUALITY_EXPLANATION, explanation[:4096])


@contextlib.contextmanager
def outbound_call(
    *, telemetry: bool, cost: CostTags | None = None, capture_content: bool = False
) -> Iterator[OutboundRun]:
    """Bind one outbound A2A call and open a DDK CLIENT span (#1395).

    Keep card discovery and sending inside this block to share a correlation id.
    An enclosing run's id and source are preserved; otherwise a fresh id is bound
    for this call only. The tracer is resolved per call, with no global setup.
    W3C headers describe this span, so the remote inbound task becomes its child.
    Telemetry off or absent yields an empty header mapping but still binds a run.

    A streaming client generator must hold this context across its yields and
    callers must exhaust it or explicitly await its ``aclose()`` in the same
    task/context on early exit. Merely breaking an async iteration does not close
    it. Cancellation and errors end the span, recording only the exception type,
    never its message. Explicit generator closure ends the span without an error.

    Args:
        telemetry: Whether to open the span and inject W3C trace context.
        capture_content: Opt in to quality explanation text, limited to 4096 characters.
        cost: Configured cost tags; current run overrides win per dimension.
            Only team, project and environment are recorded, never end-user ids.
            This does not change the cost state bound by the enclosing run.
    """
    from .telemetry import _tracer

    existing_id = current_correlation_id()
    run_id = existing_id or new_correlation_id()
    source = current_correlation_source() if existing_id is not None else "minted"
    tracer = _tracer() if telemetry else None
    with run_scope(run_id, source=source):
        if tracer is None:
            yield OutboundRun(run_id, {})
            return
        from opentelemetry.trace import SpanKind
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        attributes = {DONKEY_CORRELATION_ID: run_id}
        if source is not None:
            attributes[DONKEY_CORRELATION_SOURCE] = source
        overrides = current_cost_tags()
        tags = (
            cost.merge(overrides)
            if cost is not None and overrides is not None
            else cost or overrides
        )
        if tags is not None:
            for key, value in (
                (DONKEY_COST_TEAM, tags.team),
                (DONKEY_COST_PROJECT, tags.project),
                (DONKEY_COST_ENV, tags.env),
            ):
                if value is not None:
                    attributes[key] = value
        with tracer.start_as_current_span(
            SPAN_A2A_CALL,
            kind=SpanKind.CLIENT,
            attributes=attributes,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                headers: dict[str, str] = {}
                TraceContextTextMapPropagator().inject(headers)
                yield OutboundRun(run_id, headers, span, capture_content)
            except GeneratorExit:
                raise
            except BaseException as exc:
                _mark_failed(span, exc)
                raise


def correlation_id_from_headers(
    headers: Mapping[str, str], header: str | None = None
) -> InboundRun:
    """The caller's ``X-Correlation-Id`` (or the ``correlation_header`` setting's
    name, passed as ``header``), or a fresh id when it sent none or a malformed
    one. Header names match case-insensitively."""
    wanted = (header or _verify.CORRELATION_ID_HEADER).lower()
    for name, value in headers.items():
        if name.lower() == wanted:
            candidate = value.strip()
            if _ADOPTABLE.fullmatch(candidate):
                return InboundRun(candidate, "adopted")
            break
    return InboundRun(new_correlation_id(), "minted")


@contextlib.contextmanager
def inbound_task(
    headers: Mapping[str, str],
    *,
    telemetry: bool,
    refusals: TypedRefusals | None,
    header: str | None = None,
    attributes: Mapping[str, str] | None = None,
) -> Iterator[InboundRun]:
    """Bind the run for one inbound task and open its span.

    Yields the :class:`InboundRun`. The block runs inside ``run_scope`` with the
    adopted or minted id and its source, so every governed call in it sends that
    id and its span records where the id came from (#308), and inside
    the ``donkey.a2a.task`` span when ``telemetry`` is on and OpenTelemetry is
    installed. An exception leaving the block marks the span as an error with its
    type name only, never its message, which may repeat request content.

    Args:
        headers: The inbound request's headers.
        telemetry: The ``telemetry`` setting; off means no span.
        refusals: The typed-refusal bridge to apply on exit, or ``None``.
        header: The ``correlation_header`` setting; ``None`` reads ``X-Correlation-Id``.
        attributes: Extra span attributes, e.g. the A2A task id.
    """
    run = correlation_id_from_headers(headers, header)
    # Resolved per task, as every DDK span is, so a provider set later still wins.
    from .telemetry import _tracer

    tracer = _tracer() if telemetry else None
    if tracer is None:
        with run_scope(run.correlation_id, refusals=refusals, source=run.source):
            yield run
        return
    with tracer.start_as_current_span(
        SPAN_A2A_TASK,
        context=_parent(headers),
        kind=_server_kind(),
        attributes={
            DONKEY_CORRELATION_ID: run.correlation_id,
            DONKEY_CORRELATION_SOURCE: run.source,
            **(attributes or {}),
        },
        # OTel's own exception handling would copy the message into the span.
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            with run_scope(run.correlation_id, refusals=refusals, source=run.source):
                yield run
        except Exception as exc:
            _mark_failed(span, exc)
            raise


def _parent(headers: Mapping[str, str]) -> Any:
    """The caller's trace context from a W3C ``traceparent``, if it sent one."""
    from opentelemetry.propagate import extract

    return extract({name.lower(): value for name, value in headers.items()})


def _mark_failed(span: Any, exc: BaseException) -> None:
    from opentelemetry.trace import Status, StatusCode

    span.set_attribute("error.type", type(exc).__name__)
    span.set_status(Status(StatusCode.ERROR))


def _server_kind() -> Any:
    from opentelemetry.trace import SpanKind

    return SpanKind.SERVER
