"""The run-scoped cost rollup (#317): what a ``donkey.run()`` block cost.

The run ledger accumulates call, tool-call, refusal, token and wall-time totals,
and the run's ``donkey.run`` span carries them as ``donkey.run.*`` attributes.
Spend is the gateway's to report (#315), so it is ``None`` and never computed.
These tests use a mock transport and an in-memory span exporter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from donkey_kit import AuthError
from donkey_kit.core import telemetry
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.correlation import run_scope
from donkey_kit.core.runledger import RunLedger, current_run_ledger, record_tool_call
from donkey_kit.core.runspan import SPAN_RUN
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.tools.session import _refusing

_CFG = DonkeyConfig(llm_proxy_url="https://proxy")
_USAGE = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}


def _handler(request: httpx.Request) -> httpx.Response:
    body = request.content.decode()
    if '"pii"' in body:
        return httpx.Response(403, json={"error": {"type": "pii_detected", "message": "x"}})
    if '"budget"' in body:
        return httpx.Response(429)
    return httpx.Response(200, json={"usage": _USAGE})


def _client() -> DonkeyAsyncClient:
    return DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(_handler))


async def _call(client: DonkeyAsyncClient, prompt: str = "hi") -> int:
    resp = await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": prompt})
    return resp.status_code


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("donkey_kit"))
    try:
        yield exporter
    finally:
        provider.shutdown()


def _run_spans(exporter: InMemorySpanExporter) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == SPAN_RUN]


def _scope(run_id: str = "run-1", **kwargs: Any) -> Any:
    return run_scope(run_id, tracer=telemetry.tracer, **kwargs)


# --- accumulation ----------------------------------------------------------------


async def test_every_field_accumulates_across_a_multi_call_run(
    exporter: InMemorySpanExporter,
) -> None:
    scope = _scope()
    async with _client() as client, scope:
        await _call(client)
        await _call(client)
        await _call(client, "pii")
        await _call(client, "budget")

    ledger = scope.ledger
    assert ledger is not None
    assert ledger.call_count == 4  # a refused call is still a model call
    assert ledger.usage.input_tokens == 22
    assert ledger.usage.output_tokens == 6
    assert ledger.usage.total_tokens == 28
    assert ledger.refusal_count == 2
    assert ledger.refusal_counts == {"PIIDetected": 1, "TokenBudgetExceeded": 1}
    assert ledger.duration_s >= 0
    assert ledger.spend is None


async def test_a_fan_out_counts_every_call_exactly(exporter: InMemorySpanExporter) -> None:
    n = 40
    scope = _scope()
    async with _client() as client, scope:

        async def worker(i: int) -> None:
            await asyncio.sleep(0)
            await _call(client, "pii" if i % 4 == 0 else "hi")
            record_tool_call()
            await asyncio.sleep(0)

        await asyncio.gather(*(worker(i) for i in range(n)))

    ledger = scope.ledger
    assert ledger is not None
    assert ledger.call_count == n
    assert ledger.tool_calls == n
    assert ledger.refusal_counts == {"PIIDetected": n // 4}
    assert ledger.usage.total_tokens == 14 * (n - n // 4)

    (span,) = _run_spans(exporter)
    attrs = span.attributes
    assert attrs["donkey.run.model_calls"] == n
    assert attrs["donkey.run.tool_calls"] == n
    assert attrs["donkey.run.refusals"] == n // 4
    assert attrs["donkey.run.refusals.PIIDetected"] == n // 4
    assert attrs["donkey.run.total_tokens"] == 14 * (n - n // 4)


# --- the span --------------------------------------------------------------------


async def test_the_rollup_lands_on_the_run_span(exporter: InMemorySpanExporter) -> None:
    async with _client() as client, _scope():
        await _call(client)
        await _call(client)

    (span,) = _run_spans(exporter)
    attrs = span.attributes
    assert attrs["donkey.run.ledger_available"] is True
    assert attrs["donkey.run.model_calls"] == 2
    assert attrs["donkey.run.tool_calls"] == 0
    assert attrs["donkey.run.refusals"] == 0
    assert attrs["donkey.run.input_tokens"] == 22
    assert attrs["donkey.run.output_tokens"] == 6
    assert attrs["donkey.run.total_tokens"] == 28
    assert attrs["donkey.run.cached_tokens"] == 0
    assert attrs["donkey.run.cache_write_tokens"] == 0
    assert attrs["donkey.run.reasoning_tokens"] == 0
    assert attrs["donkey.run.duration_s"] >= 0


async def test_spend_is_never_computed_so_the_span_has_none(
    exporter: InMemorySpanExporter,
) -> None:
    scope = _scope()
    async with _client() as client, scope:
        await _call(client)

    assert scope.ledger is not None and scope.ledger.spend is None
    (span,) = _run_spans(exporter)
    assert "donkey.run.spend" not in span.attributes


async def test_a_run_with_no_calls_reports_zeros(exporter: InMemorySpanExporter) -> None:
    scope = _scope()
    async with scope:
        pass

    ledger = scope.ledger
    assert ledger is not None
    assert ledger.call_count == 0 and ledger.tool_calls == 0 and ledger.refusal_count == 0
    assert ledger.refusal_counts == {}
    assert ledger.usage.total_tokens == 0
    (span,) = _run_spans(exporter)
    attrs = span.attributes
    assert attrs["donkey.run.model_calls"] == 0
    assert attrs["donkey.run.tool_calls"] == 0
    assert attrs["donkey.run.refusals"] == 0
    assert attrs["donkey.run.total_tokens"] == 0


async def test_an_unobservable_surface_is_marked_not_reported_as_zeros(
    exporter: InMemorySpanExporter,
) -> None:
    async with _scope(surface=lambda: "crewai"):
        pass

    (span,) = _run_spans(exporter)
    assert span.attributes["donkey.run.ledger_available"] is False
    assert "donkey.run.model_calls" not in span.attributes


async def test_no_tracer_means_no_span_and_an_intact_ledger(
    exporter: InMemorySpanExporter,
) -> None:
    scope = run_scope("run-1")
    async with _client() as client, scope:
        await _call(client)

    assert _run_spans(exporter) == []
    assert scope.ledger is not None and scope.ledger.call_count == 1


# --- nesting ---------------------------------------------------------------------


async def test_a_nested_run_rolls_up_to_its_own_scope(exporter: InMemorySpanExporter) -> None:
    outer = _scope("outer")
    inner = _scope("inner")
    async with _client() as client, outer:
        await _call(client)
        async with inner:
            await _call(client)
            await _call(client)
        await _call(client)

    assert outer.ledger is not None and outer.ledger.call_count == 2
    assert inner.ledger is not None and inner.ledger.call_count == 2

    run_spans = _run_spans(exporter)
    assert len(run_spans) == 2
    outer_span, inner_span = sorted(run_spans, key=lambda s: s.parent is not None)
    assert outer_span.parent is None
    assert inner_span.parent is not None
    assert inner_span.parent.span_id == outer_span.context.span_id
    assert outer_span.attributes["donkey.run.model_calls"] == 2
    assert inner_span.attributes["donkey.run.model_calls"] == 2


async def test_rebinding_the_runs_own_id_shares_the_ledger_and_clock(
    exporter: InMemorySpanExporter,
) -> None:
    # An approval resume rebinds the run's id (#211): one run, one total.
    async with _client() as client, run_scope("run-1", tracer=telemetry.tracer) as _:
        await _call(client)
        async with run_scope("run-1"):
            await _call(client)
        mid = current_run_ledger()
        assert mid is not None and mid.call_count == 2
        # The resume's exit must not stop the clock of the run still in progress.
        first = mid.duration_s
        await asyncio.sleep(0.01)
        later = current_run_ledger()
        assert later is not None and later.duration_s > first


async def test_duration_stops_when_the_block_exits() -> None:
    scope = run_scope("run-1")
    async with scope:
        await asyncio.sleep(0.01)
    ledger = scope.ledger
    assert ledger is not None and ledger.duration_s >= 0.01
    await asyncio.sleep(0.02)
    again = scope.ledger
    assert again is not None and again.duration_s == ledger.duration_s


# --- tool calls ------------------------------------------------------------------


class _Requester:
    def __init__(self, matches: bool) -> None:
        self.user = None
        self._matches = matches

    def matches_current_user(self) -> bool:
        return self._matches


async def _tool(*args: Any, **kwargs: Any) -> str:
    return "ok"


async def test_a_sent_tool_call_counts_and_a_refused_one_does_not() -> None:
    sent = _refusing(_tool, tool="t", server="s", bound_for=_Requester(True))  # type: ignore[arg-type]
    refused = _refusing(_tool, tool="t", server="s", bound_for=_Requester(False))  # type: ignore[arg-type]
    scope = run_scope("run-1")
    async with scope:
        assert await sent() == "ok"
        assert await sent() == "ok"
        with pytest.raises(AuthError):
            await refused()

    assert scope.ledger is not None and scope.ledger.tool_calls == 2


async def test_a_tool_call_outside_a_run_is_not_recorded() -> None:
    sent = _refusing(_tool, tool="t", server="s", bound_for=_Requester(True))  # type: ignore[arg-type]
    assert await sent() == "ok"
    assert current_run_ledger() is None


def test_the_ledger_snapshot_is_still_a_frozen_value() -> None:
    ledger = RunLedger(run_id="r")
    assert ledger.tool_calls == 0 and ledger.spend is None and ledger.refusal_counts == {}
