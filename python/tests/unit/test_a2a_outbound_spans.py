"""Framework-free outbound A2A tracing seam (#1395)."""

from __future__ import annotations

import asyncio
import builtins
import re
from collections.abc import AsyncIterator, Iterator
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from donkey_kit.core import telemetry
from donkey_kit.core.correlation import (
    current_correlation_id,
    current_correlation_source,
    current_cost_tags,
    request_correlation_id,
    run_scope,
)
from donkey_kit.core.cost import CostTags
from donkey_kit.core.inbound import OutboundRun, inbound_task, outbound_call


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("donkey_kit"))
    try:
        yield exporter
    finally:
        provider.shutdown()


def test_mints_one_id_for_card_and_send_then_restores() -> None:
    with outbound_call(telemetry=False) as run:
        assert re.fullmatch(r"[0-9a-f]{32}", run.correlation_id)
        assert request_correlation_id() == run.correlation_id  # card
        assert request_correlation_id() == run.correlation_id  # send
        assert current_correlation_source() == "minted"
        assert run.trace_headers == {}
    assert current_correlation_id() is None
    assert current_correlation_source() is None
    with outbound_call(telemetry=False) as second:
        assert second.correlation_id != run.correlation_id


@pytest.mark.parametrize("source", [None, "adopted", "minted"])
def test_preserves_enclosing_run_id_and_source(source: Any) -> None:
    with run_scope("existing-run", source=source):
        with outbound_call(telemetry=False) as run:
            assert run.correlation_id == "existing-run"
            assert current_correlation_source() == source
        assert current_correlation_id() == "existing-run"
        assert current_correlation_source() == source
    assert current_correlation_id() is None


def test_run_and_headers_are_immutable_snapshots() -> None:
    headers = {"traceparent": "original"}
    run = OutboundRun("r1", headers)
    headers["traceparent"] = "changed"
    assert run.trace_headers == {"traceparent": "original"}
    with pytest.raises(FrozenInstanceError):
        run.correlation_id = "changed"  # type: ignore[misc]  # frozen dataclass contract
    with pytest.raises(TypeError):
        run.trace_headers["traceparent"] = "changed"  # type: ignore[index]  # immutable mapping


def test_disabled_telemetry_never_resolves_tracer(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected() -> None:
        pytest.fail("telemetry off must not resolve a tracer")

    monkeypatch.setattr(telemetry, "_tracer", unexpected)
    with outbound_call(telemetry=False) as run:
        assert run.trace_headers == {}
        assert current_correlation_id() == run.correlation_id


def test_missing_otel_still_binds_run(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def without_otel(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("opentelemetry"):
            raise ImportError("optional dependency absent")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_otel)
    with outbound_call(telemetry=True) as run:
        assert run.trace_headers == {}
        assert current_correlation_id() == run.correlation_id
    assert current_correlation_id() is None


def test_client_span_parents_remote_task_and_downstream_call(spans: Any) -> None:
    from opentelemetry.trace import SpanKind

    with inbound_task({}, telemetry=True, refusals=None) as caller:
        with outbound_call(telemetry=True) as outbound:
            assert outbound.correlation_id == caller.correlation_id
            assert set(outbound.trace_headers) == {"traceparent"}
            headers = {**outbound.trace_headers, "X-Correlation-Id": outbound.correlation_id}
            with inbound_task(headers, telemetry=True, refusals=None) as remote:
                assert remote.correlation_id == outbound.correlation_id
                with telemetry.genai_span(enabled=True) as chat:
                    chat.record(request_model="m")
    chat, remote_span, call, caller_span = spans.get_finished_spans()
    assert call.name == "donkey.a2a.call"
    assert call.kind is SpanKind.CLIENT
    assert call.instrumentation_scope.name == "donkey_kit"
    assert call.parent.span_id == caller_span.context.span_id
    assert remote_span.parent.span_id == call.context.span_id
    assert chat.parent.span_id == remote_span.context.span_id
    assert len({s.context.trace_id for s in spans.get_finished_spans()}) == 1
    assert call.attributes == {
        "donkey.correlation_id": outbound.correlation_id,
        "donkey.correlation_id.source": "minted",
    }


def test_cost_overrides_exclude_enduser_and_leave_scope_unchanged(spans: Any) -> None:
    overrides = CostTags(project="run-project", enduser_id="private-user")
    with run_scope("r1", cost=overrides):
        with outbound_call(
            telemetry=True, cost=CostTags(team="team", project="config-project", env="test")
        ):
            assert current_cost_tags() is overrides
    (call,) = spans.get_finished_spans()
    assert call.attributes == {
        "donkey.correlation_id": "r1",
        "donkey.cost.team": "team",
        "donkey.cost.project": "run-project",
        "donkey.cost.env": "test",
    }


def test_w3c_tracestate_is_propagated_but_baggage_is_not(spans: Any) -> None:
    from opentelemetry import baggage, context
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    parent = TraceContextTextMapPropagator().extract(
        {
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            "tracestate": "vendor=opaque",
        }
    )
    token = context.attach(baggage.set_baggage("user", "private-user", context=parent))
    try:
        with outbound_call(telemetry=True) as run:
            assert run.trace_headers["tracestate"] == "vendor=opaque"
            assert "baggage" not in run.trace_headers
    finally:
        context.detach(token)
    (call,) = spans.get_finished_spans()
    assert call.parent.span_id == int("00f067aa0ba902b7", 16)


def test_exception_records_only_type_and_restores_scope(spans: Any) -> None:
    with pytest.raises(ValueError, match="private body"), outbound_call(telemetry=True):
        raise ValueError("private body and error message")
    (call,) = spans.get_finished_spans()
    assert call.attributes["error.type"] == "ValueError"
    assert call.status.status_code.name == "ERROR"
    assert call.status.description is None
    assert not call.events
    assert "private" not in str(call.attributes)
    assert current_correlation_id() is None


async def test_cancellation_ends_span_and_restores_scope(spans: Any) -> None:
    started = asyncio.Event()

    async def send() -> None:
        try:
            with outbound_call(telemetry=True):
                started.set()
                await asyncio.Event().wait()
        finally:
            assert current_correlation_id() is None

    task = asyncio.create_task(send())
    await started.wait()
    assert not spans.get_finished_spans()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (call,) = spans.get_finished_spans()
    assert call.attributes["error.type"] == "CancelledError"
    assert call.status.status_code.name == "ERROR"
    assert not call.events


@pytest.mark.parametrize("close_early", [False, True])
async def test_stream_keeps_span_alive_until_exhaustion_or_aclose(
    spans: Any, close_early: bool
) -> None:
    async def stream() -> AsyncIterator[OutboundRun]:
        with outbound_call(telemetry=True) as run:
            yield run
            yield run

    iterator = stream()
    first = await anext(iterator)
    assert current_correlation_id() == first.correlation_id
    assert not spans.get_finished_spans()
    if close_early:
        await iterator.aclose()
    else:
        assert await anext(iterator) is first
        assert not spans.get_finished_spans()
        with pytest.raises(StopAsyncIteration):
            await anext(iterator)
    (call,) = spans.get_finished_spans()
    assert call.status.status_code.name == "UNSET"
    assert current_correlation_id() is None


def test_tracer_is_resolved_again_for_each_call(
    spans: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = telemetry._tracer
    monkeypatch.setattr(telemetry, "_tracer", lambda: None)
    with outbound_call(telemetry=True) as first:
        assert first.trace_headers == {}
    monkeypatch.setattr(telemetry, "_tracer", resolver)
    with outbound_call(telemetry=True) as second:
        assert "traceparent" in second.trace_headers
    assert len(spans.get_finished_spans()) == 1


def test_telemetry_off_does_not_propagate_ambient_trace(spans: Any) -> None:
    with inbound_task({}, telemetry=True, refusals=None):
        with outbound_call(telemetry=False) as run:
            assert run.trace_headers == {}
    assert [span.name for span in spans.get_finished_spans()] == ["donkey.a2a.task"]


async def test_concurrent_calls_keep_independent_runs(spans: Any) -> None:
    ready = asyncio.Event()
    ids: list[str] = []

    async def send() -> None:
        with outbound_call(telemetry=True) as run:
            ids.append(run.correlation_id)
            if len(ids) == 2:
                ready.set()
            await ready.wait()
            assert current_correlation_id() == run.correlation_id
        assert current_correlation_id() is None

    await asyncio.gather(send(), send())
    assert len(set(ids)) == 2
    assert {span.attributes["donkey.correlation_id"] for span in spans.get_finished_spans()} == set(
        ids
    )


def test_noop_tracer_yields_no_trace_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("opentelemetry.trace")
    from opentelemetry.trace import NoOpTracerProvider

    monkeypatch.setattr(telemetry, "_tracer", lambda: NoOpTracerProvider().get_tracer("donkey_kit"))
    with outbound_call(telemetry=True) as run:
        assert run.trace_headers == {}
        assert current_correlation_id() == run.correlation_id
