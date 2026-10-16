"""The ``approval_round_trip`` scenario (BG §2.3, #211, #212).

An approval-gated tool that calls the model pauses, is approved, and resumes,
under each framework's own pause primitive: the raw client raises
``ApprovalRequired`` and resumes on ``donkey.approvals.resolve(...)``; LangGraph
pauses with ``interrupt()`` and resumes on ``Command(resume=...)``. The pause
sends nothing to the gateway, the approver lands on the approval's span event,
and the resumed tool's model call is still judged by the gateway: an armed
refusal reaches user code typed, under the correlation id the decision event
recorded. The approval never overrides it.

``importorskip``-guarded per framework and for the OTel SDK. CrewAI is exempt
(``KNOWN_LIMITATIONS``): its provider owns the transport, so the resumed call
carries neither the approval's correlation id nor a typed refusal. The OpenAI
Agents SDK is exempt too: it turns the gate's ``ApprovalRequired`` into a
tool-result string for the model, so there is no pause to resume (#213).
:func:`test_the_agents_sdk_swallows_the_pause` pins that, so the exemption
cannot outlive the behaviour it records.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

# Sibling data module (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS

from donkey_kit import ApprovalRequired, Donkey, DonkeyConfig, PIIDetected
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import telemetry

_SCENARIO = "approval_round_trip"
_APPROVAL_SPAN = "donkey.tool.approval"
_PAUSE_RUN = "run-approval"

#: Pause the gated tool inside ``donkey.run(id=_PAUSE_RUN)``; return a resume
#: callable that approves it as ``m.keller``.
Pause = Callable[[Donkey], Awaitable[Callable[[], Awaitable[None]]]]


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


async def _openai(donkey: Donkey) -> Callable[[], Awaitable[None]]:
    pytest.importorskip("openai")
    client = donkey.openai()

    @donkey.tool(approval="required", risk="financial")
    async def issue_refund(ticket_id: str) -> None:
        """Refund a ticket, after asking the model to word the reply."""
        await client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": ticket_id}]
        )

    with pytest.raises(ApprovalRequired) as asked:
        async with donkey.run(id=_PAUSE_RUN):
            await issue_refund("T-1")
    approval_id = asked.value.approval.id

    async def resume() -> None:
        # A fresh run: the resumed call still runs under the approval's.
        async with donkey.run():
            await donkey.approvals.resolve(approval_id, approved_by="m.keller")

    return resume


async def _langgraph(donkey: Donkey) -> Callable[[], Awaitable[None]]:
    pytest.importorskip("langchain_openai")
    graph_mod = pytest.importorskip("langgraph.graph")
    saver = pytest.importorskip("langgraph.checkpoint.memory").MemorySaver()
    command = pytest.importorskip("langgraph.types").Command
    model = donkey.langgraph.chat_model("gpt-4o")

    @donkey.tool(approval="required", risk="financial")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket, after asking the model to word the reply."""
        model.invoke([("user", ticket_id)])

    def refund(state: dict[str, Any]) -> dict[str, Any]:
        issue_refund(state["ticket_id"])
        return {}

    builder = graph_mod.StateGraph(dict)
    builder.add_node("refund", refund)
    builder.add_edge(graph_mod.START, "refund")
    builder.add_edge("refund", graph_mod.END)
    graph = builder.compile(checkpointer=saver)
    thread = {"configurable": {"thread_id": "approval-round-trip"}}

    # Sync invoke: interrupt() under ainvoke() needs Python 3.11+ (#865).
    async with donkey.run(id=_PAUSE_RUN):
        paused = graph.invoke({"ticket_id": "T-1"}, thread)
    assert paused["__interrupt__"][0].value["tool"] == "issue_refund"

    async def resume() -> None:
        # LangGraph re-runs the node, so the resumed call runs under the run
        # that resumes it; resume under the same run id to join the two.
        async with donkey.run(id=_PAUSE_RUN):
            graph.invoke(command(resume={"approved": True, "by": "m.keller"}), thread)

    return resume


# The conformance-tested roster (BG §1.8) minus its asserted exemption: the raw
# client and LangGraph. The OpenAI Agents SDK's case is the test at the end.
SURFACES: dict[str, Pause] = {"openai": _openai, "langgraph": _langgraph}


def _exporter(monkeypatch: pytest.MonkeyPatch) -> Any:
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


def _approval_events(exporter: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.name, dict(event.attributes or {}))
        for span in exporter.get_finished_spans()
        if span.name == _APPROVAL_SPAN
        for event in span.events
    ]


def test_scenario_is_registered() -> None:
    assert _SCENARIO in CONFORMANCE_SCENARIOS
    exempt = {a for a, limits in KNOWN_LIMITATIONS.items() if _SCENARIO in limits}
    assert exempt == {"crewai", "openai_agents"}


@pytest.mark.parametrize("surface", SURFACES)
async def test_an_approved_tool_resumes_and_is_still_judged_by_the_gateway(
    surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(monkeypatch)

    async with Donkey(_cfg()) as donkey:
        resume = await SURFACES[surface](donkey)

        # The pause sent nothing: the only span is the pending approval.
        assert {s.name for s in exporter.get_finished_spans()} == {_APPROVAL_SPAN}
        ((pending, pending_attrs),) = _approval_events(exporter)
        assert pending == "donkey.approval.pending"
        assert pending_attrs["donkey.approval.risk"] == "financial"
        assert pending_attrs["donkey.correlation_id"] == _PAUSE_RUN

        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as refused:
            await resume()

    decisions = [(n, a) for n, a in _approval_events(exporter) if n != pending]
    ((approved, approved_attrs),) = decisions
    assert approved == "donkey.approval.approved"
    assert approved_attrs["donkey.approval.approved_by"] == "m.keller"
    assert approved_attrs["donkey.approval.id"] == pending_attrs["donkey.approval.id"]
    # The resumed call went to the gateway under the decision's correlation id,
    # and its refusal came back typed: the approval did not lift it.
    assert refused.value.correlation_id == approved_attrs["donkey.correlation_id"] == _PAUSE_RUN


def _scripted_tool_call(sent: list[dict[str, Any]]) -> httpx.MockTransport:
    """A gateway whose model asks for ``issue_refund`` once, then answers."""

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            call = {"name": "issue_refund", "arguments": json.dumps({"ticket_id": "T-1"})}
            message: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function", "function": call}],
            }
        else:
            message = {"role": "assistant", "content": "done"}
        choice = {"index": 0, "message": message, "finish_reason": "stop"}
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        body = {"id": "c", "object": "chat.completion", "created": 0, "model": "gpt-4o"}
        return httpx.Response(200, json={**body, "choices": [choice], "usage": usage})

    return httpx.MockTransport(reply)


async def test_the_agents_sdk_swallows_the_pause() -> None:
    """The OpenAI Agents SDK's asserted exemption, pinned (#213).

    Under ``Runner.run`` the gate raises ``ApprovalRequired`` inside a
    ``function_tool``, and the SDK's default ``failure_error_function`` hands the
    model a fixed error string instead: the run completes, the tool never ran,
    and nothing paused. When #213 maps approvals onto the SDK's native
    ``needs_approval`` interruption this fails, and the exemption goes."""
    agents = pytest.importorskip("agents")
    from agents.tool import default_tool_error_function

    sent: list[dict[str, Any]] = []
    ran: list[str] = []
    async with Donkey(_cfg()) as donkey:
        for client in http_clients(donkey):
            swap_transport(client, _scripted_tool_call(sent))

        @donkey.tool(approval="required", risk="financial")
        async def issue_refund(ticket_id: str) -> str:
            """Refund a ticket."""
            ran.append(ticket_id)
            return "refunded"

        agent = agents.Agent(
            name="refunds",
            model=donkey.openai_agents.model("gpt-4o"),
            tools=[agents.function_tool(issue_refund)],
        )
        async with donkey.run(id=_PAUSE_RUN):
            result = await agents.Runner.run(
                agent, "refund T-1", run_config=agents.RunConfig(tracing_disabled=True)
            )

    assert result.final_output == "done"
    assert ran == []
    tool_results = [m["content"] for m in sent[1]["messages"] if m["role"] == "tool"]
    # The SDK's fixed text, which never names the error it replaced.
    assert tool_results == [default_tool_error_function(None, RuntimeError())]
