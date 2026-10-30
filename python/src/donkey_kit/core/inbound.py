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
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import _verify
from .correlation import CorrelationSource, new_correlation_id, run_scope
from .telemetry import DONKEY_CORRELATION_ID, DONKEY_CORRELATION_SOURCE

if TYPE_CHECKING:
    from .refusals import TypedRefusals

__all__ = [
    "DONKEY_CORRELATION_SOURCE",
    "SPAN_A2A_TASK",
    "CorrelationSource",
    "InboundRun",
    "correlation_id_from_headers",
    "inbound_task",
]

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


def _mark_failed(span: Any, exc: Exception) -> None:
    from opentelemetry.trace import Status, StatusCode

    span.set_attribute("error.type", type(exc).__name__)
    span.set_status(Status(StatusCode.ERROR))


def _server_kind() -> Any:
    from opentelemetry.trace import SpanKind

    return SpanKind.SERVER
