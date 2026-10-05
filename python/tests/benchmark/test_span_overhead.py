"""Per-call span overhead stays under 1 ms (BG §1.6, #194, AC #3).

The promise behind "instrument every governed call" is that it costs
essentially nothing on the request path. This benchmark measures the overhead
the GenAI span adds to one call — creating the span, setting the full
dual-namespace attribute set, and ending it — against a hard 1 ms ceiling, so a
regression that makes instrumentation expensive fails the build.

It runs against a **real** OTel SDK TracerProvider with a BatchSpanProcessor (the
production shape), because that is where the honest cost lives: the batch
processor's ``on_end`` only enqueues, and the network flush happens on its own
background thread — which is exactly why the per-call cost is small. Overhead is
``instrumented − no-op`` so the measurement isolates the SDK's contribution from
the surrounding Python loop, and the minimum over several repeats is used (the
least-noisy estimator of the true cost under a noisy CI runner).

Off by default (``benchmark`` marker); run with ``pytest -m benchmark`` in its
own CI job. ``importorskip``s the SDK so a base-only environment skips it.

Trending (#753): CI's ``benchmark`` job only enforces this fixed 1 ms budget,
whose ~55x headroom over the measured ~18 us/call would not catch a 10x
regression. When ``DONKEY_BENCHMARK_JSON`` names a path, the measured result is
also written there as a small JSON record; the workflow writes that file only
on ``main``-branch runs and hands it to ``scripts/compare_benchmark_trend.py``,
which diffs it against the previous ``main`` run's recorded result and alerts
on a relative regression beyond its ``--alert-threshold``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")

from donkey_kit.core import telemetry

pytestmark = pytest.mark.benchmark

# Enough iterations to average out per-call jitter; repeated so we can take the
# min (a noisy runner only ever makes a sample slower, never faster).
_ITERATIONS = 2000
_REPEATS = 5
_OVERHEAD_BUDGET_S = 1e-3  # 1 ms per call — the BG §1.6 / #194 bar.

# A representative record: the full dual-namespace payload a real governed call
# lands on the span, so the benchmark reflects the true hot-path attribute cost.
_PAYLOAD = dict(
    system="openai",
    request_model="gpt-4o",
    response_model="gpt-4o-2024-11-20",
    routing_type="fallback",
    fallback=False,
    input_tokens=1420,
    output_tokens=310,
    cached_tokens=128,
    reasoning_tokens=64,
    decision=telemetry.POLICY_DECISION_ALLOW,
    budget_remaining=18450,
    cost_team="support",
    cost_project="triage-v2",
    cost_env="prod",
    correlation_id="0123456789abcdef0123456789abcdef",
)


def _in_memory_batch_tracer():  # type: ignore[no-untyped-def]
    """A real tracer wired through a BatchSpanProcessor (production shape) to an
    in-memory exporter, so no network or disk I/O enters the measurement."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider, provider.get_tracer("donkey_kit.benchmark")


def _time_loop(enabled: bool) -> float:
    """Best-of-``_REPEATS`` wall time for ``_ITERATIONS`` span open+record+close
    cycles at the given ``enabled`` setting."""
    best = float("inf")
    for _ in range(_REPEATS):
        start = time.perf_counter()
        for _ in range(_ITERATIONS):
            with telemetry.genai_span(enabled=enabled) as span:
                span.record(**_PAYLOAD)
        best = min(best, time.perf_counter() - start)
    return best


def test_genai_span_overhead_under_1ms_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, tracer = _in_memory_batch_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)
    try:
        # Warm up import/JIT-ish caches so the first timed loop is not penalised.
        _time_loop(enabled=True)

        baseline = _time_loop(enabled=False)
        instrumented = _time_loop(enabled=True)
    finally:
        provider.shutdown()

    overhead_per_call = (instrumented - baseline) / _ITERATIONS
    print(
        f"\nGenAI span overhead: {overhead_per_call * 1e6:.2f} µs/call "
        f"(budget {_OVERHEAD_BUDGET_S * 1e6:.0f} µs) over {_ITERATIONS} iters × {_REPEATS} repeats"
    )
    assert overhead_per_call < _OVERHEAD_BUDGET_S, (
        f"GenAI span instrumentation adds {overhead_per_call * 1e6:.2f} µs/call, over the "
        f"{_OVERHEAD_BUDGET_S * 1e6:.0f} µs/call bar (BG §1.6, #194). The span hot path "
        "regressed — export I/O must stay on the BatchSpanProcessor thread, not the call path."
    )

    # #753: record the result for cross-run trending when the workflow asks for
    # it (main-branch runs only — see scripts/compare_benchmark_trend.py). A
    # fixed budget alone has ~55x headroom and would not catch a 10x regression.
    result_path = os.environ.get("DONKEY_BENCHMARK_JSON")
    if result_path:
        Path(result_path).write_text(
            json.dumps(
                {
                    "name": "genai_span_overhead_us_per_call",
                    "value": overhead_per_call * 1e6,
                    "unit": "us/call",
                }
            )
        )
