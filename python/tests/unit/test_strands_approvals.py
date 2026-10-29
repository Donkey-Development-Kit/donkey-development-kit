"""``@donkey.tool(approval="required")`` in a Strands agent (BG §2.3, #1234).

With an :class:`ApprovalHook` on the agent, a gated tool pauses the agent with
Strands' own interrupt from ``BeforeToolCallEvent``: the run stops with
``stop_reason == "interrupt"`` and the tool has not run. Resuming the agent on
``approve(...)`` records the approver once and runs the tool once, under the
approval's correlation id, on whichever thread Strands runs it; ``reject(...)``
cancels the call, so the model gets the rejection as an error tool result.
Without the hook nothing pauses: the gate raises ``ApprovalRequired`` inside the
tool, and Strands turns it into an error tool result. An approved call runs as
the user who paused it, whoever resumes the agent (#1416).
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest

from donkey_kit import (
    ApprovalRequired,
    AuthError,
    Donkey,
    DonkeyConfig,
    PIIDetected,
    TokenBudgetExceeded,
)
from donkey_kit.core import approvals as approvals_mod
from donkey_kit.core import telemetry
from donkey_kit.core.approvals import Approval
from donkey_kit.core.identity import current_user
from donkey_kit.core.telemetry import current_correlation_id

pytest.importorskip("strands")
from strands import Agent, tool
from strands.hooks import AfterToolCallEvent, HookProvider
from strands.models.model import Model
from strands.tools.executors import ConcurrentToolExecutor, SequentialToolExecutor
from strands.types.exceptions import EventLoopException

from donkey_kit.integrations import strands as strands_mod
from donkey_kit.integrations.strands import (
    APPROVAL_INTERRUPT,
    ApprovalHook,
    approve,
    approved_call,
    pending_approvals,
    reject,
)

_RUN = "run-strands-approval"


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


class _ScriptedModel(Model):
    """A Strands model that asks for ``calls`` (``(tool_use_id, name, input)``)
    on its first turn and answers ``done`` after. No gateway: the pause and the
    resume are all this file tests."""

    def __init__(self, *calls: tuple[str, str, dict[str, Any]]) -> None:
        self.calls = calls
        self.turns: list[list[Any]] = []

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError
        yield

    async def stream(self, messages: Any, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self.turns.append(list(messages))
        yield {"messageStart": {"role": "assistant"}}
        if len(self.turns) == 1:
            for tool_use_id, name, arguments in self.calls:
                start = {"toolUse": {"toolUseId": tool_use_id, "name": name}}
                yield {"contentBlockStart": {"start": start}}
                yield {
                    "contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}
                }
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        yield {"contentBlockDelta": {"delta": {"text": "done"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}

    def tool_results(self) -> dict[str, dict[str, Any]]:
        """The tool results the model was sent on its last turn, by tool-use id."""
        return {
            block["toolResult"]["toolUseId"]: block["toolResult"]
            for message in self.turns[-1]
            for block in message["content"]
            if "toolResult" in block
        }


_REFUND = ("tu-1", "issue_refund", {"ticket_id": "T-1"})

Ran = list[tuple[str, str | None, str]]


def _refund_tool(ran: Ran, *, is_async: bool = False) -> Any:
    if is_async:

        @Donkey.tool(approval="required", risk="financial")
        async def issue_refund(ticket_id: str, note: str = "") -> str:
            """Refund a ticket."""
            ran.append((ticket_id, current_correlation_id(), threading.current_thread().name))
            return "refunded"

    else:

        @Donkey.tool(approval="required", risk="financial")
        def issue_refund(ticket_id: str, note: str = "") -> str:
            """Refund a ticket."""
            ran.append((ticket_id, current_correlation_id(), threading.current_thread().name))
            return "refunded"

    return tool(issue_refund)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Approval, str | None]]:
    """Every ``record_pending`` / ``record_decision`` the hook makes, with the
    ``Approval`` object it passed."""
    calls: list[tuple[str, Approval, str | None]] = []

    def pending(approval: Approval) -> None:
        calls.append(("pending", approval, None))

    def decision(approval: Approval, *, approved: bool, by: str) -> None:
        calls.append(("approved" if approved else "rejected", approval, by))

    monkeypatch.setattr(strands_mod, "record_pending", pending)
    monkeypatch.setattr(strands_mod, "record_decision", decision)
    return calls


def _agent(model: _ScriptedModel, *tools: Any, hooks: tuple[Any, ...] = (), **kw: Any) -> Agent:
    return Agent(model=model, tools=list(tools), hooks=list(hooks), callback_handler=None, **kw)


async def _paused(agent: Agent) -> Any:
    async with Donkey(_cfg()).run(id=_RUN):
        result = await agent.invoke_async("refund T-1")
    assert result.stop_reason == "interrupt"
    return result


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
async def test_an_approved_call_pauses_then_runs_once_under_the_approval(
    is_async: bool, recorded: list[tuple[str, Approval, str | None]]
) -> None:
    ran: Ran = []
    model = _ScriptedModel(_REFUND)
    agent = _agent(model, _refund_tool(ran, is_async=is_async), hooks=(ApprovalHook(),))

    result = await _paused(agent)
    assert ran == []
    (interrupt,) = pending_approvals(result)
    assert interrupt.name == APPROVAL_INTERRUPT
    assert interrupt.reason == {
        "approval_id": interrupt.reason["approval_id"],
        "tool": "issue_refund",
        # Bound to the tool's parameters, defaults included.
        "arguments": {"ticket_id": "T-1", "note": ""},
        "risk": "financial",
        "correlation_id": _RUN,
    }
    [(kind, pending, _)] = recorded
    assert kind == "pending"
    assert pending.id == interrupt.reason["approval_id"]
    assert pending.expires_at == float("inf")

    # Resumed outside any run: the tool still runs under the approval's id.
    resumed = await agent.invoke_async([approve(interrupt, by=" m.keller ")])

    assert resumed.stop_reason == "end_turn"
    assert [(t, c) for t, c, _ in ran] == [("T-1", _RUN)]
    if not is_async:
        # Strands runs a sync tool with asyncio.to_thread: the approval
        # crossed into the worker thread through the copied context.
        assert ran[0][2] != threading.current_thread().name
    assert model.tool_results()["tu-1"]["status"] == "success"
    # One decision, on the very Approval the pause recorded: id, correlation
    # id and requested_at survive the resume.
    assert recorded[1:] == [("approved", pending, "m.keller")]
    assert approved_call("issue_refund") is None


def _user_refund_tool(users: list[str | None]) -> Any:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        user = current_user()
        users.append(user.token if user else None)
        return "refunded"

    return tool(issue_refund)


async def _paused_as_alice(donkey: Donkey, users: list[str | None]) -> tuple[Agent, Any]:
    model = _ScriptedModel(_REFUND)
    agent = _agent(model, _user_refund_tool(users), hooks=(ApprovalHook(),))
    async with donkey.as_user(token="token-alice"):
        result = await agent.invoke_async("refund T-1")
    (interrupt,) = pending_approvals(result)
    return agent, interrupt


async def test_an_approved_call_runs_as_the_user_who_paused_it() -> None:
    users: list[str | None] = []
    async with Donkey(_cfg()) as donkey:
        agent, interrupt = await _paused_as_alice(donkey, users)
        # The approver's handler resumes the agent, outside as_user().
        resumed = await agent.invoke_async([approve(interrupt, by="m.keller")])
    assert resumed.stop_reason == "end_turn"
    assert users == ["token-alice"]


async def test_resuming_as_another_user_is_refused() -> None:
    users: list[str | None] = []
    async with Donkey(_cfg()) as donkey:
        agent, interrupt = await _paused_as_alice(donkey, users)
        async with donkey.as_user(token="token-bob"):
            with pytest.raises(AuthError, match="as_user"):
                async with donkey.run():
                    await agent.invoke_async([approve(interrupt, by="m.keller")])
    assert users == []


async def test_a_rejected_call_is_not_run_and_the_model_is_told(
    recorded: list[tuple[str, Approval, str | None]],
) -> None:
    ran: Ran = []
    model = _ScriptedModel(_REFUND)
    agent = _agent(model, _refund_tool(ran), hooks=(ApprovalHook(),))
    (interrupt,) = pending_approvals(await _paused(agent))

    resumed = await agent.invoke_async(
        [reject(interrupt, by="m.keller", message="no refunds on Sundays")]
    )

    assert resumed.stop_reason == "end_turn"
    assert ran == []
    result = model.tool_results()["tu-1"]
    assert result["status"] == "error"
    assert result["content"] == [{"text": "no refunds on Sundays"}]
    assert [(k, by) for k, _, by in recorded] == [("pending", None), ("rejected", "m.keller")]


async def test_a_rejection_without_a_message_does_not_name_the_approver(
    recorded: list[tuple[str, Approval, str | None]],
) -> None:
    model = _ScriptedModel(_REFUND)
    agent = _agent(model, _refund_tool([]), hooks=(ApprovalHook(),))
    (interrupt,) = pending_approvals(await _paused(agent))

    await agent.invoke_async([reject(interrupt, by="m.keller")])

    (text,) = model.tool_results()["tu-1"]["content"]
    assert "m.keller" not in text["text"] and "issue_refund" in text["text"]


@pytest.mark.parametrize("executor", [ConcurrentToolExecutor, SequentialToolExecutor])
async def test_each_call_runs_under_its_own_decision(
    executor: type[Any], recorded: list[tuple[str, Approval, str | None]]
) -> None:
    """Two gated calls and an ungated one in one turn. Each paused call gets its
    own decision, and the approval set for one call never leaks into another,
    whether Strands runs them concurrently or in sequence."""
    ran: Ran = []
    plain: list[str] = []

    @tool
    def lookup(ticket_id: str) -> str:
        """Look a ticket up."""
        # No approval leaks into an ungated tool's context either.
        plain.append(str(approved_call("issue_refund")))
        return "found"

    model = _ScriptedModel(
        ("tu-1", "issue_refund", {"ticket_id": "T-1"}),
        ("tu-2", "issue_refund", {"ticket_id": "T-2"}),
        ("tu-3", "lookup", {"ticket_id": "T-3"}),
    )
    agent = _agent(
        model,
        _refund_tool(ran),
        lookup,
        hooks=(ApprovalHook(),),
        tool_executor=executor(),
    )
    # The concurrent executor pauses both calls at once; the sequential one
    # pauses on each in turn.
    decide = {"T-1": lambda i: approve(i, by="m.keller"), "T-2": lambda i: reject(i, by="j.doe")}
    result = await _paused(agent)
    seen: list[str] = []
    while result.stop_reason == "interrupt":
        paused = pending_approvals(result)
        seen += [i.reason["approval_id"] for i in paused]
        responses = [decide[i.reason["arguments"]["ticket_id"]](i) for i in paused]
        result = await agent.invoke_async(responses)
    assert len(set(seen)) == 2

    assert [t for t, _, _ in ran] == ["T-1"]
    results = model.tool_results()
    assert results["tu-1"]["status"] == "success"
    assert results["tu-2"]["status"] == "error"
    assert plain == ["None"]
    decisions = sorted((k, a.arguments["ticket_id"], by) for k, a, by in recorded if k != "pending")
    assert decisions == [("approved", "T-1", "m.keller"), ("rejected", "T-2", "j.doe")]
    assert sorted(a.arguments["ticket_id"] for k, a, _ in recorded if k == "pending") == [
        "T-1",
        "T-2",
    ]


async def test_a_retried_call_records_its_decision_once(
    recorded: list[tuple[str, Approval, str | None]],
) -> None:
    """A hook that retries the tool fires ``BeforeToolCallEvent`` again with the
    stored response; the decision is still recorded once."""

    class RetryOnce(HookProvider):
        def __init__(self) -> None:
            self.retried = False

        def register_hooks(self, registry: Any, **kwargs: Any) -> None:
            registry.add_callback(AfterToolCallEvent, self.after)

        def after(self, event: AfterToolCallEvent) -> None:
            if not self.retried:
                self.retried = True
                event.retry = True

    ran: Ran = []
    agent = _agent(_ScriptedModel(_REFUND), _refund_tool(ran), hooks=(ApprovalHook(), RetryOnce()))
    (interrupt,) = pending_approvals(await _paused(agent))

    await agent.invoke_async([approve(interrupt, by="m.keller")])

    assert len(ran) == 2  # the retry ran the approved call again
    assert [k for k, _, _ in recorded] == ["pending", "approved"]


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("executor", [ConcurrentToolExecutor, SequentialToolExecutor])
@pytest.mark.parametrize("error", [TokenBudgetExceeded, PIIDetected])
async def test_a_tool_refusal_stops_the_agent_without_retrying(
    is_async: bool, executor: type[Any], error: type[Exception]
) -> None:
    refusal = error("governed tool refused")
    calls: list[str] = []

    if is_async:

        @Donkey.tool(approval="required")
        async def issue_refund(ticket_id: str) -> str:
            """Refund a ticket."""
            calls.append(ticket_id)
            raise refusal

    else:

        @Donkey.tool(approval="required")
        def issue_refund(ticket_id: str) -> str:
            """Refund a ticket."""
            calls.append(ticket_id)
            raise refusal

    class RetryErrors(HookProvider):
        retried = False

        def register_hooks(self, registry: Any, **kwargs: Any) -> None:
            registry.add_callback(AfterToolCallEvent, self.after)

        def after(self, event: AfterToolCallEvent) -> None:
            if event.exception is not None and not self.retried:
                event.retry = True
                self.retried = True

    model = _ScriptedModel(_REFUND)
    agent = _agent(
        model, tool(issue_refund), hooks=(ApprovalHook(), RetryErrors()), tool_executor=executor()
    )
    (interrupt,) = pending_approvals(await _paused(agent))

    async with Donkey(_cfg()) as donkey:
        with pytest.raises(error) as caught:
            async with donkey.run():
                await agent.invoke_async([approve(interrupt, by="m.keller")])

    assert caught.value is refusal
    assert calls == ["T-1"]
    assert len(model.turns) == 1  # no refusal text or subsequent model request
    assert approved_call("issue_refund") is None
    # A failed approved call must not leak its approval into the next call.
    with pytest.raises(ApprovalRequired):
        if is_async:
            await issue_refund("T-2")
        else:
            issue_refund("T-2")
    assert calls == ["T-1"]


@pytest.mark.parametrize("executor", [ConcurrentToolExecutor, SequentialToolExecutor])
async def test_an_ungated_tool_refusal_also_stops_the_agent(executor: type[Any]) -> None:
    refusal = PIIDetected("governed tool refused")

    @tool
    def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        raise refusal

    model = _ScriptedModel(_REFUND)
    agent = _agent(model, issue_refund, hooks=(ApprovalHook(),), tool_executor=executor())
    async with Donkey(_cfg()) as donkey:
        with pytest.raises(PIIDetected) as caught:
            async with donkey.run():
                await agent.invoke_async("refund T-1")

    assert caught.value is refusal
    assert len(model.turns) == 1
    assert approved_call("issue_refund") is None


async def test_an_ordinary_tool_failure_still_reaches_the_model() -> None:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        raise ValueError("ticket not found")

    model = _ScriptedModel(_REFUND)
    agent = _agent(model, tool(issue_refund), hooks=(ApprovalHook(),))
    (interrupt,) = pending_approvals(await _paused(agent))
    result = await agent.invoke_async([approve(interrupt, by="m.keller")])

    assert result.stop_reason == "end_turn"
    assert model.tool_results()["tu-1"]["status"] == "error"
    assert "ticket not found" in model.tool_results()["tu-1"]["content"][0]["text"]
    assert approved_call("issue_refund") is None


async def test_a_fresh_hook_still_resumes_a_paused_call(
    recorded: list[tuple[str, Approval, str | None]],
) -> None:
    """A restored agent gets a new hook, which never saw the pause: it rebuilds
    the approval from the call and records the decision under the same id."""
    ran: Ran = []
    model = _ScriptedModel(_REFUND)
    agent = _agent(model, _refund_tool(ran), hooks=(ApprovalHook(),))
    (interrupt,) = pending_approvals(await _paused(agent))
    agent.hooks = type(agent.hooks)()
    agent.hooks.add_hook(ApprovalHook())

    await agent.invoke_async([approve(interrupt, by="m.keller")])

    assert [t for t, _, _ in ran] == ["T-1"]
    (_, decided, _) = recorded[-1]
    assert decided.id == interrupt.reason["approval_id"]


async def test_without_the_hook_nothing_pauses() -> None:
    """The gate raises ``ApprovalRequired`` inside the tool; Strands turns it
    into an error tool result, and the call waits in ``donkey.approvals``."""
    ran: Ran = []
    model = _ScriptedModel(_REFUND)
    agent = _agent(model, _refund_tool(ran))

    async with Donkey(_cfg()) as donkey, donkey.run(id=_RUN):
        result = await agent.invoke_async("refund T-1")
        (pending,) = donkey.approvals.pending()

    assert result.stop_reason == "end_turn"
    assert ran == []
    assert model.tool_results()["tu-1"]["status"] == "error"
    assert pending.tool == "issue_refund"


async def test_an_ungated_tool_is_not_paused(
    recorded: list[tuple[str, Approval, str | None]],
) -> None:
    @tool
    def lookup(ticket_id: str) -> str:
        """Look a ticket up."""
        return "found"

    model = _ScriptedModel(("tu-1", "lookup", {"ticket_id": "T-1"}))
    result = await _agent(model, lookup, hooks=(ApprovalHook(),)).invoke_async("look up T-1")

    assert result.stop_reason == "end_turn"
    assert recorded == []


async def test_the_pending_and_decision_events_share_the_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through the real span events, as the audit trail sees them."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("t"))

    agent = _agent(_ScriptedModel(_REFUND), _refund_tool([]), hooks=(ApprovalHook(),))
    (interrupt,) = pending_approvals(await _paused(agent))
    await agent.invoke_async([approve(interrupt, by="m.keller")])

    events = [
        (e.name, dict(e.attributes or {})) for s in exporter.get_finished_spans() for e in s.events
    ]
    (pending, pending_attrs), (approved, approved_attrs) = events
    assert (pending, approved) == ("donkey.approval.pending", "donkey.approval.approved")
    assert pending_attrs["donkey.approval.id"] == approved_attrs["donkey.approval.id"]
    assert approved_attrs["donkey.approval.approved_by"] == "m.keller"
    assert approved_attrs["donkey.correlation_id"] == _RUN
    # A framework-held pause has no expiry to record.
    assert "donkey.approval.expires_at" not in pending_attrs
    assert approvals_mod.approvals.pending() == ()


def test_decisions_need_an_approver_and_an_approval_interrupt() -> None:
    from strands.interrupt import Interrupt

    ours = Interrupt(id="i-1", name=APPROVAL_INTERRUPT)
    assert approve(ours, by="m.keller") == {
        "interruptResponse": {
            "interruptId": "i-1",
            "response": {"approved": True, "by": "m.keller"},
        }
    }
    assert reject(ours, by="m.keller", message="no")["interruptResponse"]["response"] == {
        "approved": False,
        "by": "m.keller",
        "message": "no",
    }
    with pytest.raises(ValueError, match="by must name"):
        approve(ours, by="  ")
    with pytest.raises(ValueError, match="not a DDK approval interrupt"):
        approve(Interrupt(id="i-2", name="someone_else"), by="m.keller")
    assert pending_approvals(object()) == ()


async def test_a_malformed_response_is_refused(
    recorded: list[tuple[str, Approval, str | None]],
) -> None:
    ran: Ran = []
    agent = _agent(_ScriptedModel(_REFUND), _refund_tool(ran), hooks=(ApprovalHook(),))
    (interrupt,) = pending_approvals(await _paused(agent))

    bad = {"interruptResponse": {"interruptId": interrupt.id, "response": "yes"}}
    # Strands' event loop wraps a hook's error in its own EventLoopException.
    with pytest.raises(EventLoopException) as refused:
        await agent.invoke_async([bad])
    assert isinstance(refused.value.__cause__, ValueError)
    assert "resume a DDK approval" in str(refused.value.__cause__)

    assert ran == []
    assert [k for k, _, _ in recorded] == ["pending"]
