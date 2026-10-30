"""Governed inbound for ``donkey.serve()`` (BG §2.9.1, #225).

An inbound A2A task binds a run: the caller's correlation ID (or a fresh one),
the configured cost tags on every downstream call, one ``donkey.a2a.task`` span
parenting the downstream spans, and a downstream refusal surfacing as a typed,
structured A2A failure. Raw JSON-RPC over ``httpx.ASGITransport`` for the inbound
side; ``httpx.MockTransport`` behind the governed client for the downstream side.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
import pytest

pytest.importorskip("a2a", reason="needs the [a2a] extra")

from donkey_kit import A2ATask, Donkey
from donkey_kit._testing import swap_transport
from donkey_kit.a2a import build_app
from donkey_kit.core import telemetry
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.cost import CostTags
from donkey_kit.core.inbound import correlation_id_from_headers

_CHAT = {
    "id": "c",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
}
_PII_BODY = {"error": {"type": "pii_detected", "message": 'blocked: [{"pii_type": "EMAIL"}]'}}
_HEX32 = re.compile(r"[0-9a-f]{32}")


def _send(text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": text}]}},
    }


async def _calls(app: Any, *headers: dict[str, str]) -> list[dict[str, Any]]:
    """One inbound task per ``headers`` mapping, all inside one ASGI lifespan."""
    transport = httpx.ASGITransport(app=app)
    tasks: list[dict[str, Any]] = []
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://agent") as client,
    ):
        for extra in headers:
            response = await client.post(
                "/", json=_send("hi"), headers={"A2A-Version": "1.0", **extra}
            )
            assert response.status_code == 200, response.text
            payload: dict[str, Any] = response.json()
            assert "error" not in payload, payload
            tasks.append(payload["result"]["task"])
    return tasks


async def _call(app: Any, headers: dict[str, str] | None = None) -> dict[str, Any]:
    (task,) = await _calls(app, headers or {})
    return task


def _id_echo() -> Any:
    @Donkey.agent(name="id-echo")
    async def id_echo(task: A2ATask) -> str:
        """Returns the task's correlation ID."""
        return task.correlation_id

    return id_echo


def _artifact_text(task: dict[str, Any]) -> str:
    assert task["status"]["state"] == "TASK_STATE_COMPLETED", task
    text: str = task["artifacts"][0]["parts"][0]["text"]
    return text


def _donkey(
    seen: list[httpx.Request],
    response: httpx.Response | None = None,
    *,
    send_cost_headers: bool = False,
) -> Donkey:
    donkey = Donkey(
        DonkeyConfig(
            llm_proxy_url="https://proxy",
            llm_proxy_client_id="cid",
            llm_proxy_client_secret="csecret",
            cost=CostTags(team="support", project="triage"),
            send_cost_headers=send_cost_headers,
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response if response is not None else httpx.Response(200, json=_CHAT)

    swap_transport(donkey._http, httpx.MockTransport(handler))
    return donkey


def _calling(donkey: Donkey) -> Any:
    client = donkey.llm.client()

    @Donkey.agent(name="caller")
    async def caller(task: A2ATask) -> str:
        """Makes one downstream model call."""
        await client.chat.completions.create(
            model="m", messages=[{"role": "user", "content": task.input}]
        )
        return "done"

    return caller


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
    return exporter


# --- correlation: adopt the caller's, mint when absent -----------------------


async def test_the_callers_correlation_id_is_adopted() -> None:
    task = await _call(build_app(_id_echo()), {"X-Correlation-Id": "caller-run-42"})
    assert _artifact_text(task) == "caller-run-42"


async def test_a_correlation_id_is_minted_when_the_caller_sends_none() -> None:
    task = await _call(build_app(_id_echo()))
    assert _HEX32.fullmatch(_artifact_text(task))


async def test_a_malformed_correlation_id_is_replaced_not_forwarded() -> None:
    task = await _call(build_app(_id_echo()), {"X-Correlation-Id": "bad id; x" + "y" * 200})
    assert _HEX32.fullmatch(_artifact_text(task))


async def test_the_configured_correlation_header_is_the_one_read() -> None:
    app = build_app(_id_echo(), correlation_header="X-Run-Id")
    task = await _call(app, {"X-Run-Id": "via-custom-header", "X-Correlation-Id": "ignored"})
    assert _artifact_text(task) == "via-custom-header"


@pytest.mark.parametrize(
    ("headers", "source"),
    [
        ({"x-correlation-id": "abc-123"}, "adopted"),
        ({"X-CORRELATION-ID": " abc-123 "}, "adopted"),
        ({}, "minted"),
        ({"X-Correlation-Id": ""}, "minted"),
        ({"X-Correlation-Id": "a\r\nb"}, "minted"),
    ],
)
def test_correlation_id_from_headers(headers: dict[str, str], source: str) -> None:
    run = correlation_id_from_headers(headers)
    assert run.source == source
    if source == "adopted":
        assert run.correlation_id == "abc-123"


# --- downstream calls carry the run and the cost tags ------------------------


async def test_downstream_calls_send_the_inbound_correlation_id() -> None:
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        task = await _call(build_app(_calling(donkey)), {"X-Correlation-Id": "caller-run-7"})
    finally:
        await donkey.aclose()
    assert _artifact_text(task) == "done"
    (request,) = seen
    assert request.headers["X-Correlation-Id"] == "caller-run-7"


async def test_downstream_calls_carry_the_configured_cost_tags(spans: Any) -> None:
    """The ``donkey.cost.*`` span attributes are the authoritative carrier
    (docs/verified-apis.md §3); the headers go out only with send_cost_headers."""
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen, send_cost_headers=True)
    try:
        await _call(build_app(_calling(donkey)))
    finally:
        await donkey.aclose()
    (chat_span,) = [s for s in spans.get_finished_spans() if s.name == "donkey.llm.chat"]
    assert chat_span.attributes["donkey.cost.team"] == "support"
    assert chat_span.attributes["donkey.cost.project"] == "triage"
    (request,) = seen
    assert request.headers["X-Anypoint-Cost-Team"] == "support"
    assert request.headers["X-Anypoint-Cost-Project"] == "triage"


async def test_two_inbound_tasks_never_share_a_minted_run() -> None:
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        await _calls(build_app(_calling(donkey)), {}, {})
    finally:
        await donkey.aclose()
    first, second = (r.headers["X-Correlation-Id"] for r in seen)
    assert first != second


# --- one span per task, parenting the downstream spans ------------------------


async def test_one_task_span_parents_the_downstream_model_span(spans: Any) -> None:
    pytest.importorskip("openai")
    from opentelemetry.trace import SpanKind

    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        await _call(build_app(_calling(donkey)), {"X-Correlation-Id": "caller-run-9"})
    finally:
        await donkey.aclose()
    finished = spans.get_finished_spans()
    (task_span,) = [s for s in finished if s.name == "donkey.a2a.task"]
    (chat_span,) = [s for s in finished if s.name == "donkey.llm.chat"]
    assert task_span.kind is SpanKind.SERVER
    assert task_span.attributes["donkey.correlation_id"] == "caller-run-9"
    assert task_span.attributes["donkey.correlation_id.source"] == "adopted"
    assert task_span.attributes["donkey.a2a.agent"] == "caller"
    assert chat_span.parent is not None
    assert chat_span.parent.span_id == task_span.context.span_id
    assert chat_span.context.trace_id == task_span.context.trace_id


async def test_a_traceparent_from_the_caller_parents_the_task_span(spans: Any) -> None:
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    traceparent = f"00-{trace_id}-00f067aa0ba902b7-01"
    await _call(build_app(_id_echo()), {"traceparent": traceparent})
    (task_span,) = [s for s in spans.get_finished_spans() if s.name == "donkey.a2a.task"]
    assert format(task_span.context.trace_id, "032x") == trace_id
    assert task_span.parent is not None
    assert format(task_span.parent.span_id, "016x") == "00f067aa0ba902b7"
    assert task_span.attributes["donkey.correlation_id.source"] == "minted"


async def test_telemetry_off_opens_no_task_span(spans: Any) -> None:
    task = await _call(build_app(_id_echo(), telemetry=False), {"X-Correlation-Id": "r1"})
    assert _artifact_text(task) == "r1"  # the run is still bound
    assert [s.name for s in spans.get_finished_spans()] == []


async def test_a_failed_task_marks_its_span_with_the_error_type_only(spans: Any) -> None:
    from opentelemetry.trace import StatusCode

    @Donkey.agent(name="broken")
    async def broken(task: A2ATask) -> str:
        """Always fails."""
        raise RuntimeError("db password is hunter2")

    await _call(build_app(broken))
    (task_span,) = [s for s in spans.get_finished_spans() if s.name == "donkey.a2a.task"]
    assert task_span.status.status_code is StatusCode.ERROR
    assert task_span.attributes["error.type"] == "RuntimeError"
    assert "hunter2" not in str(task_span.to_json())


# --- a downstream refusal is a typed, well-formed A2A failure ----------------


async def test_a_downstream_refusal_fails_the_task_with_its_typed_name() -> None:
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen, httpx.Response(403, json=_PII_BODY))
    try:
        task = await _call(build_app(_calling(donkey)), {"X-Correlation-Id": "caller-run-3"})
    finally:
        await donkey.aclose()
    assert task["status"]["state"] == "TASK_STATE_FAILED"
    text, error = task["status"]["message"]["parts"]
    assert text["text"] == "the agent's downstream call was refused: PIIDetected"
    details = error["data"]["error"]
    assert details["type"] == "PIIDetected"
    assert details["refusal"] is True
    assert details["correlation_id"] == "caller-run-3"
    assert details["call_id"] == seen[0].headers["X-Donkey-Request-Id"]
    assert "EMAIL" not in str(task)  # the gateway's body is not echoed to the caller
    assert "Traceback" not in str(task)


async def test_donkey_serve_bridge_types_a_refusal_with_framework_translators() -> None:
    """``Donkey.serve()`` passes ``typed_refusals()``; ``build_app(refusals=...)``
    applies whatever bridge it is given."""
    from donkey_kit import typed_refusals

    pytest.importorskip("openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen, httpx.Response(403, json=_PII_BODY))
    try:
        task = await _call(build_app(_calling(donkey), refusals=typed_refusals()))
    finally:
        await donkey.aclose()
    assert task["status"]["message"]["parts"][1]["data"]["error"]["type"] == "PIIDetected"


async def test_the_run_reaches_a_sync_handlers_worker_thread() -> None:
    from donkey_kit.core.correlation import current_correlation_id

    @Donkey.agent(name="sync-id")
    def sync_id(task: A2ATask) -> str:
        """Reports the run bound in its worker thread."""
        return current_correlation_id() or "unbound"

    task = await _call(build_app(sync_id), {"X-Correlation-Id": "threaded-run"})
    assert _artifact_text(task) == "threaded-run"


# --- multi-hop: a supervisor's run reaches its sub-agent's calls (#308) -----


async def test_a_supervisors_run_id_reaches_the_sub_agents_downstream_calls() -> None:
    """Supervisor → A2A → sub-agent → model: one correlation ID end to end. The
    supervisor's A2A client is not the governed transport, so it sends the ID via
    ``correlation_headers()``; the served sub-agent adopts it."""
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []
    sub_agent = _donkey(seen)
    supervisor = Donkey(DonkeyConfig())
    try:
        async with supervisor.run(id="supervisor-run-1"):
            task = await _call(build_app(_calling(sub_agent)), supervisor.correlation_headers())
    finally:
        await sub_agent.aclose()
    assert _artifact_text(task) == "done"
    (request,) = seen
    assert request.headers["X-Correlation-Id"] == "supervisor-run-1"


async def test_the_sub_agents_model_span_records_the_adopted_id(spans: Any) -> None:
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    try:
        await _call(build_app(_calling(donkey)), {"X-Correlation-Id": "caller-run-11"})
        await _call(build_app(_calling(donkey)))
    finally:
        await donkey.aclose()
    adopted, minted = (s for s in spans.get_finished_spans() if s.name == "donkey.llm.chat")
    assert adopted.attributes["donkey.correlation_id"] == "caller-run-11"
    assert adopted.attributes["donkey.correlation_id.source"] == "adopted"
    assert minted.attributes["donkey.correlation_id.source"] == "minted"
