"""``@donkey.tool(approval="required")`` inside a LangGraph graph (BG §2.3, #212).

Inside a graph run with a checkpointer, a gated tool pauses with LangGraph's
native ``interrupt()``, not an exception the framework cannot resume.
``Command(resume={"approved": True, "by": ...})`` records the approver and runs
the tool; ``{"approved": False, ...}`` raises ``ApprovalRejected``. The pause
lives in the checkpointer, so it survives a process restart, and a refusal and
the pause compose without either swallowing the other. The resumed tool runs as
the user who paused the graph, whoever resumes it (#1416).
"""

from __future__ import annotations

import pickle
import sys
from collections import defaultdict
from typing import Any

import pytest

from donkey_kit import (
    ApprovalRejected,
    ApprovalRequired,
    Approvals,
    AuthError,
    Donkey,
    DonkeyConfig,
    DonkeyError,
    PIIDetected,
)
from donkey_kit.core import approvals as approvals_mod
from donkey_kit.core import telemetry
from donkey_kit.core.correlation import current_business_group
from donkey_kit.core.identity import current_user
from donkey_kit.core.telemetry import current_correlation_id

pytest.importorskip("langgraph")
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from donkey_kit.integrations.langgraph import (
    APPROVAL_INTERRUPT,
    approval_interrupt,
)

_APPROVE = {"approved": True, "by": "m.keller"}


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


def _thread(name: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": name}}


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


def _events(exporter: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.name, dict(event.attributes or {}))
        for span in exporter.get_finished_spans()
        for event in span.events
    ]


def _plain(value: Any) -> Any:
    return {k: _plain(v) for k, v in value.items()} if isinstance(value, defaultdict) else value


def _fill(target: Any, source: Any) -> None:
    for key, value in source.items():
        nested = isinstance(target, defaultdict) and target.default_factory is not None
        if nested and isinstance(target[key], dict):
            _fill(target[key], value)
        else:
            target[key] = value


def _restarted(saver: MemorySaver) -> MemorySaver:
    """A fresh saver loaded from ``saver``'s stored checkpoints, round-tripped
    through bytes as a durable checkpointer would persist them. ``MemorySaver``
    itself does not pickle, and no durable saver is a test dependency."""
    tables = ("storage", "writes", "blobs")
    stored = pickle.dumps({name: _plain(getattr(saver, name)) for name in tables})
    restored = MemorySaver()
    for name, table in pickle.loads(stored).items():
        _fill(getattr(restored, name), table)
    return restored


def _refund_graph(ran: list[tuple[str, float, str | None]]) -> StateGraph:
    """A one-node graph whose node calls a gated refund tool."""

    @Donkey.tool(approval="required", risk="financial")
    def issue_refund(ticket_id: str, amount: float) -> str:
        """Refund a support ticket."""
        ran.append((ticket_id, amount, current_correlation_id()))
        return f"refunded {ticket_id}"

    def refund(state: dict) -> dict:
        return {"result": issue_refund(state["ticket_id"], state["amount"])}

    builder = StateGraph(dict)
    builder.add_node("refund", refund)
    builder.add_edge(START, "refund")
    builder.add_edge("refund", END)
    return builder


# --- the pause ---------------------------------------------------------------
def test_a_gated_tool_in_a_graph_pauses_with_a_native_interrupt(spans: Any) -> None:
    ran: list[tuple[str, float, str | None]] = []
    graph = _refund_graph(ran).compile(checkpointer=MemorySaver())

    paused = graph.invoke({"ticket_id": "T-1", "amount": 140.0}, _thread("t-pause"))

    assert ran == []
    (pause,) = paused["__interrupt__"]
    assert pause.value == {
        "type": APPROVAL_INTERRUPT,
        "approval_id": pause.value["approval_id"],
        "tool": "issue_refund",
        "arguments": {"ticket_id": "T-1", "amount": 140.0},
        "risk": "financial",
        "correlation_id": pause.value["correlation_id"],
    }
    # The checkpointer holds the pause; the in-memory registry is not used.
    assert Approvals().pending() == ()
    ((name, attrs),) = _events(spans)
    assert name == "donkey.approval.pending"
    assert attrs["donkey.approval.id"] == pause.value["approval_id"]
    assert attrs["donkey.approval.risk"] == "financial"
    # LangGraph holds the pause with no expiry, so the event carries none.
    assert "donkey.approval.expires_at" not in attrs
    assert "T-1" not in repr(spans.get_finished_spans()[0].to_json())


def test_resume_records_the_approver_and_runs_the_tool_once(spans: Any) -> None:
    ran: list[tuple[str, float, str | None]] = []
    graph = _refund_graph(ran).compile(checkpointer=MemorySaver())
    thread = _thread("t-resume")

    with Donkey(_cfg()) as donkey:
        with donkey.run(id="run-9"):
            paused = graph.invoke({"ticket_id": "T-1", "amount": 140.0}, thread)
        # The decision arrives later, in another run scope.
        with donkey.run(id="run-9"):
            done = graph.invoke(Command(resume=_APPROVE), thread)

    assert done["result"] == "refunded T-1"
    assert ran == [("T-1", 140.0, "run-9")]
    approval_id = paused["__interrupt__"][0].value["approval_id"]
    assert [(n, a["donkey.approval.id"]) for n, a in _events(spans)] == [
        ("donkey.approval.pending", approval_id),
        ("donkey.approval.approved", approval_id),
    ]
    approved = _events(spans)[1][1]
    assert approved["donkey.approval.approved_by"] == "m.keller"
    assert approved["donkey.correlation_id"] == "run-9"


def _identity_graph(seen: list[tuple[str | None, str | None]]) -> StateGraph:
    """A one-node graph whose gated tool records the user and business group it runs as."""

    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        user = current_user()
        seen.append((user.token if user else None, current_business_group()))
        return f"refunded {ticket_id}"

    def refund(state: dict) -> dict:
        return {"result": issue_refund(state["ticket_id"])}

    builder = StateGraph(dict)
    builder.add_node("refund", refund)
    builder.add_edge(START, "refund")
    builder.add_edge("refund", END)
    return builder


async def test_the_resumed_tool_runs_as_the_user_who_paused_the_graph() -> None:
    seen: list[tuple[str | None, str | None]] = []
    graph = _identity_graph(seen).compile(checkpointer=MemorySaver())
    thread = _thread("t-identity")

    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token="token-alice"):
            with donkey.attribution(business_group="risk-eu"):
                graph.invoke({"ticket_id": "T-1"}, thread)
        # The approver's handler resumes the graph, outside as_user().
        with donkey.attribution(business_group="risk-us"):
            done = graph.invoke(Command(resume=_APPROVE), thread)

    assert done["result"] == "refunded T-1"
    assert seen == [("token-alice", "risk-eu")]


async def test_resuming_the_graph_as_another_user_is_refused() -> None:
    seen: list[tuple[str | None, str | None]] = []
    graph = _identity_graph(seen).compile(checkpointer=MemorySaver())
    thread = _thread("t-identity-other")

    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token="token-alice"):
            graph.invoke({"ticket_id": "T-1"}, thread)
        async with donkey.as_user(token="token-bob"):
            with pytest.raises(AuthError, match="as_user"):
                graph.invoke(Command(resume=_APPROVE), thread)

    assert seen == []


def test_outside_a_run_the_pause_and_the_tool_share_a_correlation_id() -> None:
    ran: list[tuple[str, float, str | None]] = []
    graph = _refund_graph(ran).compile(checkpointer=MemorySaver())
    thread = _thread("t-corr")

    paused = graph.invoke({"ticket_id": "T-1", "amount": 140.0}, thread)
    graph.invoke(Command(resume=_APPROVE), thread)

    correlation = paused["__interrupt__"][0].value["correlation_id"]
    assert correlation
    assert ran == [("T-1", 140.0, correlation)]


def test_the_pause_survives_a_process_restart(spans: Any) -> None:
    """Everything DDK held in memory is gone after the pause: the tool, the
    graph, the approval registry. Only the checkpoint, round-tripped through
    bytes as a durable saver would store it, carries the pause."""
    saver = MemorySaver()
    first: list[tuple[str, float, str | None]] = []
    _refund_graph(first).compile(checkpointer=saver).invoke(
        {"ticket_id": "T-1", "amount": 140.0}, _thread("t-restart")
    )
    restored = _restarted(saver)
    approvals_mod._reset_for_tests()

    second: list[tuple[str, float, str | None]] = []
    graph = _refund_graph(second).compile(checkpointer=restored)
    done = graph.invoke(Command(resume=_APPROVE), _thread("t-restart"))

    assert done["result"] == "refunded T-1"
    assert (first, [r[:2] for r in second]) == ([], [("T-1", 140.0)])
    pending, approved = _events(spans)
    assert pending[0] == "donkey.approval.pending"
    assert approved[0] == "donkey.approval.approved"
    # The id is derived from the thread, tool and arguments, so it joins the two.
    assert pending[1]["donkey.approval.id"] == approved[1]["donkey.approval.id"]


# --- rejection and bad resume values ----------------------------------------
def test_a_rejected_resume_raises_approval_rejected(spans: Any) -> None:
    ran: list[tuple[str, float, str | None]] = []
    graph = _refund_graph(ran).compile(checkpointer=MemorySaver())
    thread = _thread("t-reject")
    graph.invoke({"ticket_id": "T-1", "amount": 140.0}, thread)

    with pytest.raises(ApprovalRejected) as info:
        graph.invoke(Command(resume={"approved": False, "by": "a.ops"}), thread)

    assert ran == []
    assert isinstance(info.value, DonkeyError)
    assert info.value.approval.tool == "issue_refund"
    assert info.value.remediation.strip()
    assert "T-1" not in str(info.value)
    name, attrs = _events(spans)[-1]
    assert (name, attrs["donkey.approval.rejected_by"]) == ("donkey.approval.rejected", "a.ops")


@pytest.mark.parametrize(
    "resume",
    [
        "yes",
        True,
        {"approved": "yes", "by": "m.keller"},
        {"approved": True},
        {"approved": True, "by": " "},
    ],
)
def test_a_malformed_resume_value_is_a_value_error(resume: object) -> None:
    ran: list[tuple[str, float, str | None]] = []
    graph = _refund_graph(ran).compile(checkpointer=MemorySaver())
    thread = _thread("t-bad")
    graph.invoke({"ticket_id": "T-1", "amount": 140.0}, thread)

    with pytest.raises(ValueError, match="Command\\(resume="):
        graph.invoke(Command(resume=resume), thread)
    assert ran == []


# --- where the native pause does not apply ----------------------------------
def test_outside_a_graph_the_hook_declines() -> None:
    assert approval_interrupt("issue_refund", {}, None) is None


def test_outside_a_graph_a_gated_tool_still_raises_approval_required() -> None:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    with pytest.raises(ApprovalRequired):
        issue_refund("T-1")


def test_a_graph_without_a_checkpointer_falls_back_to_approval_required() -> None:
    """With no thread to resume, LangGraph cannot hold the pause, so the gate
    raises ``ApprovalRequired`` out of the graph and the registry holds it."""
    ran: list[tuple[str, float, str | None]] = []
    graph = _refund_graph(ran).compile()

    with pytest.raises(ApprovalRequired) as info:
        graph.invoke({"ticket_id": "T-1", "amount": 140.0})

    assert Approvals().resolve(info.value.approval.id, approved_by="m.keller") == "refunded T-1"
    assert [r[:2] for r in ran] == [("T-1", 140.0)]


# --- ToolNode and async graphs ----------------------------------------------
def test_a_gated_tool_in_a_tool_node_pauses_and_resumes() -> None:
    pytest.importorskip("langchain_core")
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool as lc_tool
    from langgraph.graph import MessagesState
    from langgraph.prebuilt import ToolNode

    @Donkey.tool(approval="required", risk="financial")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        return f"refunded {ticket_id}"

    def agent(_state: MessagesState) -> dict:
        call = {"name": "issue_refund", "args": {"ticket_id": "T-1"}, "id": "call-1"}
        return {"messages": [AIMessage("", tool_calls=[call])]}

    builder = StateGraph(MessagesState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode([lc_tool(issue_refund)]))
    builder.add_edge(START, "agent")
    builder.add_edge("agent", "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=MemorySaver())
    thread = _thread("t-toolnode")

    paused = graph.invoke({"messages": []}, thread)
    assert paused["__interrupt__"][0].value["arguments"] == {"ticket_id": "T-1"}
    done = graph.invoke(Command(resume=_APPROVE), thread)
    assert done["messages"][-1].content == "refunded T-1"


# On 3.10 LangGraph's get_config() cannot see the run under ainvoke() (#865),
# so the native pause is unavailable there and the gate falls back.
@pytest.mark.skipif(sys.version_info < (3, 11), reason="interrupt() under ainvoke() (#865)")
async def test_an_async_gated_tool_pauses_and_resumes_under_ainvoke() -> None:
    ran: list[str] = []

    @Donkey.tool(approval="required")
    async def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        ran.append(ticket_id)
        return f"refunded {ticket_id}"

    async def refund(state: dict) -> dict:
        return {"result": await issue_refund(state["ticket_id"])}

    builder = StateGraph(dict)
    builder.add_node("refund", refund)
    builder.add_edge(START, "refund")
    builder.add_edge("refund", END)
    graph = builder.compile(checkpointer=MemorySaver())
    thread = _thread("t-async")

    paused = await graph.ainvoke({"ticket_id": "T-1"}, thread)
    assert "__interrupt__" in paused
    assert ran == []
    done = await graph.ainvoke(Command(resume=_APPROVE), thread)
    assert (done["result"], ran) == ("refunded T-1", ["T-1"])


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="the 3.10 fallback (#865)")
async def test_on_3_10_an_async_graph_falls_back_to_approval_required() -> None:
    @Donkey.tool(approval="required")
    async def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        return ticket_id

    async def refund(state: dict) -> dict:
        return {"result": await issue_refund(state["ticket_id"])}

    builder = StateGraph(dict)
    builder.add_node("refund", refund)
    builder.add_edge(START, "refund")
    builder.add_edge("refund", END)
    graph = builder.compile(checkpointer=MemorySaver())

    with pytest.raises(ApprovalRequired):
        await graph.ainvoke({"ticket_id": "T-1"}, _thread("t-310"))


# --- a refusal and the pause compose ----------------------------------------
def test_an_approved_tools_refusal_surfaces_typed_out_of_the_graph(spans: Any) -> None:
    """The pause does not swallow a refusal: the resumed tool's model call is
    refused, and the typed refusal leaves the graph after the approval was
    recorded. The approval does not lift it."""
    pytest.importorskip("openai")

    with Donkey(_cfg()) as donkey:

        @donkey.tool(approval="required")
        def summarise(ticket_id: str) -> str:
            """Summarise a ticket with the model."""
            llm = donkey.openai(sync=True)
            llm.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": ticket_id}]
            )
            return "summarised"

        def node(state: dict) -> dict:
            return {"result": summarise(state["ticket_id"])}

        builder = StateGraph(dict)
        builder.add_node("summarise", node)
        builder.add_edge(START, "summarise")
        builder.add_edge("summarise", END)
        graph = builder.compile(checkpointer=MemorySaver())
        thread = _thread("t-refused")

        with donkey.run(id="run-r"):
            paused = graph.invoke({"ticket_id": "T-1"}, thread)
        assert "__interrupt__" in paused
        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as refused:
            with donkey.run(id="run-r"):
                graph.invoke(Command(resume=_APPROVE), thread)

    assert refused.value.correlation_id == "run-r"
    assert [n for n, _ in _events(spans)] == ["donkey.approval.pending", "donkey.approval.approved"]


def test_a_refusal_before_the_gated_tool_is_not_swallowed_by_the_pause(spans: Any) -> None:
    """The refusal does not swallow the pause either way round: a node refused
    before the gated tool raises typed, and no approval is requested."""
    pytest.importorskip("langchain_openai")
    ran: list[str] = []

    with Donkey(_cfg()) as donkey:
        model = donkey.langgraph.chat_model("gpt-4o")

        @donkey.tool(approval="required")
        def issue_refund(ticket_id: str) -> str:
            """Refund a support ticket."""
            ran.append(ticket_id)
            return ticket_id

        def plan(_state: dict) -> dict:
            model.invoke([("user", "plan the refund")])
            return {}

        def refund(state: dict) -> dict:
            return {"result": issue_refund(state["ticket_id"])}

        builder = StateGraph(dict)
        builder.add_node("plan", plan)
        builder.add_node("refund", refund)
        builder.add_edge(START, "plan")
        builder.add_edge("plan", "refund")
        builder.add_edge("refund", END)
        graph = builder.compile(checkpointer=MemorySaver())

        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected), donkey.run():
            graph.invoke({"ticket_id": "T-1"}, _thread("t-first"))

    assert ran == []
    assert _events(spans) == []
