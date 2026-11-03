"""Refusal diagnostic privacy (BG §1.2/§1.6, #1398; captured ledger §4)."""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.a2a._server import _failure
from donkey_kit.core import telemetry
from donkey_kit.core._refusal_body import pii_spans, pii_summary
from donkey_kit.core.errors import PIIDetected, ToolInvocationError, classify
from donkey_kit.core.inbound import outbound_call
from donkey_kit.core.refusals import RefusalCapture, TypedRefusals, translate

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"
_SECRET = "capture@example.com"


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Any:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("donkey_kit.test")
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)
    yield exporter
    provider.shutdown()


@pytest.mark.parametrize("name", ["v03-pii-send", "v1-pii-send", "v1-pii-stream"])
def test_captured_a2a_pii_diagnostics_stay_only_on_response(
    name: str, spans: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Ledger §4: ambiguous PII stays ToolInvocationError, with raw evidence retained."""
    lines = (_FIXTURES / f"{name}.headers.txt").read_text().splitlines()
    raw = (_FIXTURES / f"{name}.body.json").read_bytes()
    request = httpx.Request("POST", "https://agent.example.com/")
    request.extensions["donkey_call_id_header"] = "X-Donkey-Request-Id"
    response = httpx.Response(
        int(lines[0].split()[1]),
        headers=[
            (key, value.strip())
            for key, value in (line.split(":", 1) for line in lines[1:] if line)
        ],
        content=raw,
        request=request,
    )
    assert _SECRET in response.text
    wrapper = httpx.HTTPStatusError(response.text, request=request, response=response)
    with caplog.at_level(logging.DEBUG, logger="donkey_kit"):
        with pytest.raises(ToolInvocationError) as caught:
            with outbound_call(telemetry=True), TypedRefusals():
                raise wrapper
        error = caught.value
        logging.getLogger("donkey_kit.test").error("refused: %s", error, exc_info=error)
    assert error.response is response
    assert response.content == raw
    assert error.framework_error is wrapper
    assert type(classify(response)) is ToolInvocationError
    span = spans.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert span.attributes["error.type"] == "ToolInvocationError"
    assert not span.events
    rendered = [
        str(error),
        repr(error),
        str(error.args),
        "".join(traceback.format_exception(type(error), error, error.__traceback__)),
        caplog.text,
        span.to_json(),
        json.dumps(_failure(error, None)),
    ]
    assert all(_SECRET not in text for text in rendered)


@pytest.mark.parametrize("truncated", [False, True])
def test_untrusted_pii_type_labels_are_not_rendered(truncated: bool) -> None:
    message = json.dumps(
        [
            {"pii_type": "Email", "start": 1, "end": 4},
            {"pii_type": _SECRET, "start": 5, "end": 8},
        ]
    )
    if truncated:
        message = message[:-1]
    response = httpx.Response(403, json={"error": {"type": "pii_detected", "message": message}})
    error = classify(response)
    assert isinstance(error, PIIDetected)
    assert error.entities == ["Email", "Custom"]
    assert _SECRET not in str(error)
    assert _SECRET not in repr(pii_spans(message))
    assert _SECRET not in pii_summary(403, [(_SECRET, 5, 8)])
    assert error.gateway_message == message
    assert error.response is response


@pytest.mark.parametrize("capture_content", [False, True])
def test_genai_span_never_records_exception_diagnostics(spans: Any, capture_content: bool) -> None:
    error = RuntimeError(_SECRET)
    error.__cause__ = ValueError("raw gateway diagnostic")
    with pytest.raises(RuntimeError) as caught:
        with telemetry.genai_span(enabled=True, capture_content=capture_content):
            raise error
    assert caught.value is error
    assert error.__cause__ is not None
    (span,) = spans.get_finished_spans()
    assert span.status.status_code.name == "ERROR"
    assert span.status.description is None
    assert not span.events
    assert _SECRET not in span.to_json()


def test_failed_translator_logs_types_without_hook_repr_or_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenTranslator:
        def __repr__(self) -> str:
            return _SECRET

        def __call__(self, exc: BaseException) -> None:
            raise ValueError(_SECRET) from exc

    original = RuntimeError(_SECRET)
    with caplog.at_level(logging.DEBUG, logger="donkey_kit.core.refusals"):
        assert translate(original, [BrokenTranslator()]) is None
    assert "BrokenTranslator" in caplog.text and "ValueError" in caplog.text
    assert _SECRET not in caplog.text
    assert all(record.exc_info is None and record.exc_text is None for record in caplog.records)


def test_failed_capture_tag_logs_no_raw_exception_chain(caplog: pytest.LogCaptureFixture) -> None:
    class Untaggable(RuntimeError):
        def __setattr__(self, name: str, value: object) -> None:
            raise TypeError(_SECRET)

    capture = RefusalCapture()
    capture.record(PIIDetected("safe"))
    original = Untaggable(_SECRET)
    with caplog.at_level(logging.DEBUG, logger="donkey_kit.core.refusals"):
        try:
            raise original
        except Untaggable as exc:
            capture.tag(exc)
    assert "Untaggable" in caplog.text
    assert _SECRET not in caplog.text
    assert all(record.exc_info is None and record.exc_text is None for record in caplog.records)
