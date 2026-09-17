"""Zero-config OTLP export bootstrap (BG §1.6, #194).

The span machinery from #192/#193 only ever rides the process's global
TracerProvider; on its own it exports nothing. This module tests the wiring that
turns "we build spans" into "spans reach the customer's sink", against the four
acceptance criteria:

- **AC #1** — ``Donkey.from_env()`` + ``OTEL_EXPORTER_OTLP_ENDPOINT`` installs an
  OTLP exporter, with no SDK-specific env var.
- **AC #2** — a single flag opts out: ``DONKEY_TELEMETRY=false``.
- **AC #4** — with no OTLP endpoint set, the bootstrap is inert and silent (it
  never builds a provider, so nothing connects and nothing prints).

The decision logic (:func:`_build_tracer_provider`) is pure w.r.t. the OTel
global singleton, so it is exercised in-process. Installation
(:func:`configure_otlp_export`) mutates that process-global provider, which OTel
lets you set exactly once — so those cases run in subprocesses to keep the
singleton clean for the rest of the suite. The overhead bar (AC #3) is a
separate benchmark under ``tests/benchmark`` (``pytest -m benchmark``).

The SDK+exporter live in the ``[otel]`` extra; every test ``importorskip``s them
so the base-only CI job skips this module cleanly.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
import textwrap
import warnings

import pytest

from donkey_kit.core import telemetry
from donkey_kit.core.config import DonkeyConfig

# The whole module needs the SDK and the http OTLP exporter (the [otel] extra).
pytest.importorskip("opentelemetry.sdk")
pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")

_ENDPOINT_VARS = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
_PROTOCOL_VARS = ("OTEL_EXPORTER_OTLP_PROTOCOL", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")


@pytest.fixture(autouse=True)
def _clean_otel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from a known OTel env and a fresh warning de-dupe set,
    so one test's endpoint/protocol/warning never leaks into the next."""
    for var in _ENDPOINT_VARS + _PROTOCOL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(telemetry, "_warned_missing_exporter", set())


# --- the inert-and-silent gate (AC #4) --------------------------------------


def test_endpoint_gate_reads_both_standard_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    assert telemetry._otlp_endpoint_configured() is False
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert telemetry._otlp_endpoint_configured() is True


def test_endpoint_gate_honours_the_traces_specific_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4318/v1/traces")
    assert telemetry._otlp_endpoint_configured() is True


def test_blank_endpoint_does_not_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """A set-but-empty var is not a configured endpoint — it stays inert."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "   ")
    assert telemetry._otlp_endpoint_configured() is False


def test_build_is_none_without_endpoint() -> None:
    """AC #4: no endpoint → no provider built, so export is inert and silent."""
    assert telemetry._build_tracer_provider(DonkeyConfig()) is None


# --- the single opt-out flag (AC #2) ----------------------------------------


def test_build_is_none_when_telemetry_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC #2: DONKEY_TELEMETRY=false is the one flag; even with an endpoint set,
    nothing is built."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    assert telemetry._build_tracer_provider(DonkeyConfig(telemetry=False)) is None


# --- the exporter wiring (AC #1) --------------------------------------------


def test_build_wires_http_otlp_exporter_behind_a_batch_processor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #1: endpoint set + telemetry on → a TracerProvider carrying a
    BatchSpanProcessor whose exporter is the http OTLP span exporter. Batch (not
    Simple) is what keeps export I/O off the request hot path — the basis of the
    <1ms overhead bar."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")
    provider = telemetry._build_tracer_provider(DonkeyConfig())
    try:
        assert isinstance(provider, TracerProvider)
        processors = provider._active_span_processor._span_processors
        assert len(processors) == 1
        assert isinstance(processors[0], BatchSpanProcessor)
        assert isinstance(processors[0].span_exporter, OTLPSpanExporter)
    finally:
        if provider is not None:
            provider.shutdown()


def test_default_protocol_is_http_protobuf(monkeypatch: pytest.MonkeyPatch) -> None:
    assert telemetry._otlp_protocol() == "http/protobuf"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    assert telemetry._otlp_protocol() == "grpc"


def test_traces_protocol_overrides_the_generic_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    assert telemetry._otlp_protocol() == "http/protobuf"


def test_grpc_without_grpc_exporter_warns_once_and_stays_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grpc is deliberately not in the [otel] extra (it drags in grpcio). When it
    is requested but unavailable, we warn ONCE and export nothing — never a
    silent drop, and never a crash. Skips if the grpc exporter happens to be
    installed in this environment."""
    if _grpc_exporter_available():
        pytest.skip("grpc OTLP exporter is installed; the missing-exporter path is unreachable")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")

    with pytest.warns(telemetry.TelemetryExportWarning):
        assert telemetry._build_tracer_provider(DonkeyConfig()) is None
    # De-duped: a second build in the same process is silent.
    with warnings_none(telemetry.TelemetryExportWarning):
        assert telemetry._build_tracer_provider(DonkeyConfig()) is None


def _grpc_exporter_available() -> bool:
    # find_spec raises (not returns None) when an intermediate package is absent,
    # so guard it — the whole point of this helper is "is it missing?".
    try:
        return (
            importlib.util.find_spec("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")
            is not None
        )
    except ModuleNotFoundError:
        return False


@contextlib.contextmanager
def warnings_none(category: type[Warning]):  # type: ignore[no-untyped-def]
    """Assert that no warning of ``category`` is emitted inside the block."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
    assert not [w for w in caught if issubclass(w.category, category)]


# --- global installation via configure_otlp_export (subprocess) -------------
#
# configure_otlp_export mutates the process-global TracerProvider, which OTel
# permits exactly once. Running each case in a fresh interpreter keeps the
# singleton pristine for the rest of the suite and mirrors how a real process
# starts up.


def _run_probe(body: str, **env: str) -> str:
    """Run ``body`` in a fresh interpreter with ``env`` overlaid on the current
    environment (minus the OTLP vars this suite controls) and return its stdout."""
    child_env = {k: v for k, v in os.environ.items() if k not in _ENDPOINT_VARS + _PROTOCOL_VARS}
    child_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        capture_output=True,
        text=True,
        env=child_env,
    )
    assert proc.returncode == 0, f"probe failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
    return proc.stdout


def test_donkey_from_env_installs_the_global_provider_with_endpoint() -> None:
    """AC #1 end to end: constructing a Donkey with only OTEL_EXPORTER_OTLP_ENDPOINT
    set installs an SDK TracerProvider with a live span processor — no
    SDK-specific env var."""
    out = _run_probe(
        """
        from donkey_kit import Donkey
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        Donkey.from_env()
        tp = trace.get_tracer_provider()
        print("SDK_PROVIDER", isinstance(tp, TracerProvider))
        print("HAS_PROCESSOR", tp._active_span_processor is not None)
        """,
        OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318",
    )
    assert "SDK_PROVIDER True" in out
    assert "HAS_PROCESSOR True" in out


def test_donkey_from_env_is_inert_without_an_endpoint() -> None:
    """AC #4: no endpoint → the global provider is left as OTel's default proxy,
    so no exporter, no connection, no output."""
    out = _run_probe(
        """
        from donkey_kit import Donkey
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        Donkey.from_env()
        tp = trace.get_tracer_provider()
        print("SDK_PROVIDER", isinstance(tp, TracerProvider))
        """,
    )
    assert "SDK_PROVIDER False" in out


def test_donkey_from_env_is_inert_when_telemetry_opted_out() -> None:
    """AC #2/#4: DONKEY_TELEMETRY=false keeps the bootstrap inert even with an
    endpoint set."""
    out = _run_probe(
        """
        from donkey_kit import Donkey
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        Donkey.from_env()
        print("SDK_PROVIDER", isinstance(trace.get_tracer_provider(), TracerProvider))
        """,
        OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318",
        DONKEY_TELEMETRY="false",
    )
    assert "SDK_PROVIDER False" in out


def test_configure_does_not_clobber_a_host_provider() -> None:
    """If a host already installed its own SDK provider (opentelemetry-instrument,
    a manual setup), Donkey rides it and never replaces it — our spans still flow
    through the host's export pipeline."""
    out = _run_probe(
        """
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        host = TracerProvider()
        trace.set_tracer_provider(host)
        from donkey_kit import Donkey
        Donkey.from_env()
        print("SAME", trace.get_tracer_provider() is host)
        """,
        OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318",
    )
    assert "SAME True" in out
