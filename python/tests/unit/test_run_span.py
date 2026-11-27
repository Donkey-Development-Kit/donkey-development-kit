"""The run-end evaluation hook attaches a caller's score to the run span (BG §3.4, #232).

Every run's ``donkey.run`` span carries the cost rollup (#317, see
``test_run_rollup.py``); a scorer adds ``donkey.evaluation.score`` to it.

``donkey.evaluate(on="run_end", with_=scorer)`` calls ``scorer`` with the run's
ledger when a ``donkey.run()`` block exits and records the number it returns as
``donkey.evaluation.score`` on the run's ``donkey.run`` span. These tests run
against the in-process simulator and an in-memory span exporter.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytest.importorskip("openai")
pytest.importorskip("starlette")
pytest.importorskip("opentelemetry.sdk")

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import telemetry
from donkey_kit.core.runledger import RunLedger
from donkey_kit.core.runspan import DONKEY_EVALUATION_SCORE, SPAN_RUN
from donkey_kit.simulator import build_app

_MESSAGES = [{"role": "user", "content": "hi"}]


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


def _donkey_on_simulator() -> Donkey:
    donkey = Donkey(_cfg())
    async_client, _ = http_clients(donkey)
    swap_transport(async_client, httpx.ASGITransport(app=build_app()))
    return donkey


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("donkey_kit"))
    try:
        yield exporter
    finally:
        provider.shutdown()


def _run_spans(exporter: Any) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == SPAN_RUN]


async def test_scorer_receives_the_run_ledger_and_its_score_lands_on_the_run_span(
    spans: Any,
) -> None:
    seen: list[RunLedger] = []

    def scorer(run: RunLedger) -> float:
        seen.append(run)
        return 0.75

    donkey = _donkey_on_simulator()
    donkey.evaluate(on="run_end", with_=scorer)
    async with donkey.run(id="ticket-1"):
        await donkey.openai().chat.completions.create(model="gpt-4o", messages=_MESSAGES)

    (run_span,) = _run_spans(spans)
    assert run_span.attributes[DONKEY_EVALUATION_SCORE] == 0.75
    (ledger,) = seen
    assert ledger.run_id == "ticket-1"
    assert ledger.call_count == 1


async def test_model_call_spans_are_children_of_the_run_span(spans: Any) -> None:
    donkey = _donkey_on_simulator()
    donkey.evaluate(on="run_end", with_=lambda _run: 1.0)
    async with donkey.run():
        await donkey.openai().chat.completions.create(model="gpt-4o", messages=_MESSAGES)

    (run_span,) = _run_spans(spans)
    (llm_span,) = [s for s in spans.get_finished_spans() if s.name == telemetry.SPAN_LLM_CHAT]
    assert llm_span.parent is not None
    assert llm_span.parent.span_id == run_span.context.span_id


async def test_a_scorer_that_raises_does_not_fail_the_run(
    spans: Any, caplog: pytest.LogCaptureFixture
) -> None:
    def scorer(_run: RunLedger) -> float:
        raise RuntimeError("the eval tool is down")

    donkey = _donkey_on_simulator()
    donkey.evaluate(on="run_end", with_=scorer)
    with caplog.at_level(logging.WARNING, logger="donkey_kit.core.runspan"):
        async with donkey.run():
            await donkey.openai().chat.completions.create(model="gpt-4o", messages=_MESSAGES)

    (run_span,) = _run_spans(spans)
    assert DONKEY_EVALUATION_SCORE not in run_span.attributes
    assert any("run-end scorer raised" in r.getMessage() for r in caplog.records)


async def test_the_block_result_is_unchanged_when_the_scorer_raises() -> None:
    donkey = _donkey_on_simulator()
    donkey.evaluate(on="run_end", with_=lambda _run: 1 / 0)
    async with donkey.run(id="kept"):
        completion = await donkey.openai().chat.completions.create(
            model="gpt-4o", messages=_MESSAGES
        )
    assert completion.choices[0].message.content == "pong — how can I help?"


@pytest.mark.parametrize("returned", [None, True, "0.9", [0.9]])
async def test_a_non_numeric_or_none_score_records_no_attribute(
    spans: Any, returned: Any, caplog: pytest.LogCaptureFixture
) -> None:
    donkey = _donkey_on_simulator()
    donkey.evaluate(on="run_end", with_=lambda _run: returned)
    with caplog.at_level(logging.WARNING, logger="donkey_kit.core.runspan"):
        async with donkey.run():
            await donkey.openai().chat.completions.create(model="gpt-4o", messages=_MESSAGES)

    (run_span,) = _run_spans(spans)
    assert DONKEY_EVALUATION_SCORE not in run_span.attributes


async def test_an_unscored_run_has_its_rollup_span_but_no_score(spans: Any) -> None:
    donkey = _donkey_on_simulator()
    async with donkey.run():
        await donkey.openai().chat.completions.create(model="gpt-4o", messages=_MESSAGES)
    (run_span,) = _run_spans(spans)
    attributes = run_span.attributes or {}
    assert DONKEY_EVALUATION_SCORE not in attributes
    assert attributes["donkey.run.model_calls"] == 1


async def test_the_scorer_applies_to_run_from_headers_too(spans: Any) -> None:
    donkey = _donkey_on_simulator()
    donkey.evaluate(on="run_end", with_=lambda _run: 0.5)
    with donkey.run_from_headers({}):
        await donkey.openai().chat.completions.create(model="gpt-4o", messages=_MESSAGES)
    (run_span,) = _run_spans(spans)
    assert run_span.attributes[DONKEY_EVALUATION_SCORE] == 0.5


def test_evaluate_rejects_any_event_but_run_end() -> None:
    donkey = Donkey(_cfg())
    with pytest.raises(ValueError, match="run_end"):
        donkey.evaluate(on="run_start", with_=lambda _run: 1.0)  # type: ignore[arg-type]
