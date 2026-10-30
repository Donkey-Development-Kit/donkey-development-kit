"""The Strands approval mapping's bookkeeping, with no Strands installed (BG §2.3,
#1234).

:class:`~donkey_kit.integrations.strands.ApprovalHook` only reads a
``BeforeToolCallEvent``'s ``selected_tool`` / ``tool_use`` / ``agent``, calls its
``interrupt(...)`` and sets its ``cancel_tool``, and imports
``strands.interrupt.InterruptException`` and the three event classes from
``strands.hooks`` (docs/verified-apis.md §8). These tests drive it with doubles
of that shape and stand-in modules. The round trip through a real Strands agent
is ``test_strands_approvals.py``; this file keeps the mapping's own logic covered
on the legs that do not install ``strands``.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from donkey_kit import ApprovalRequired, Donkey, PIIDetected
from donkey_kit.core.approvals import Approval, Approvals
from donkey_kit.core.correlation import run_scope
from donkey_kit.integrations import strands as strands_mod
from donkey_kit.integrations.strands import (
    APPROVAL_INTERRUPT,
    ApprovalHook,
    GovernedToolHook,
    approve,
    approved_call,
    pending_approvals,
    reject,
)


class _Interrupted(Exception):
    """Stands in for ``strands.interrupt.InterruptException``."""


class _BeforeToolCall:
    pass


class _AfterToolCall:
    pass


class _AfterInvocation:
    pass


@pytest.fixture(autouse=True)
def fake_strands(monkeypatch: pytest.MonkeyPatch) -> None:
    """The three ``strands`` modules the hook imports, as stand-ins."""
    root = ModuleType("strands")
    interrupt = ModuleType("strands.interrupt")
    interrupt.InterruptException = _Interrupted  # type: ignore[attr-defined]
    hooks = ModuleType("strands.hooks")
    hooks.BeforeToolCallEvent = _BeforeToolCall  # type: ignore[attr-defined]
    hooks.AfterToolCallEvent = _AfterToolCall  # type: ignore[attr-defined]
    hooks.AfterInvocationEvent = _AfterInvocation  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "strands", sys.modules.get("strands", root))
    monkeypatch.setitem(sys.modules, "strands.interrupt", interrupt)
    monkeypatch.setitem(sys.modules, "strands.hooks", hooks)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(strands_mod, "record_pending", lambda a: events.append(("pending", a)))

    def decision(approval: Any, *, approved: bool, by: str) -> None:
        events.append(("approved" if approved else "rejected", (approval, by)))

    monkeypatch.setattr(strands_mod, "record_decision", decision)
    return events


ran: list[str] = []


@Donkey.tool(approval="required", risk="financial")
def issue_refund(ticket_id: str, amount: float = 140.0) -> str:
    """Refund a support ticket."""
    ran.append(ticket_id)
    return f"refunded {ticket_id}"


@Donkey.tool
def lookup(ticket_id: str) -> str:
    """Look a ticket up."""
    return ticket_id


class _Unstable:
    """A default whose repr changes on every call, as an address-bearing repr
    changes from one process to the next."""

    calls = 0

    def __repr__(self) -> str:
        _Unstable.calls += 1
        return f"<_Unstable {_Unstable.calls}>"


@Donkey.tool(approval="required")
def tag_ticket(ticket_id: str, marker: Any = _Unstable()) -> str:  # noqa: B008
    """Tag a support ticket."""
    return ticket_id


class _Agent:
    """Hashable and weak-referenceable, as a Strands ``Agent`` is."""


@dataclass
class _Event:
    """A ``BeforeToolCallEvent`` double. ``response`` is what the interrupt
    returns on resume; ``None`` pauses."""

    selected_tool: Any
    tool_use: dict[str, Any]
    agent: Any = field(default_factory=_Agent)
    response: Any = None
    cancel_tool: Any = False
    asked: list[tuple[str, Any]] = field(default_factory=list)

    def interrupt(self, name: str, reason: Any = None) -> Any:
        self.asked.append((name, reason))
        if self.response is None:
            raise _Interrupted(name)
        return self.response


def _event(
    tool: Any = issue_refund,
    arguments: dict[str, Any] | None = None,
    tool_use_id: str = "tu-1",
    **kw: Any,
) -> _Event:
    name = getattr(tool, "__name__", "tool")
    use = {"toolUseId": tool_use_id, "name": name, "input": arguments or {"ticket_id": "T-1"}}
    return _Event(selected_tool=tool, tool_use=use, **kw)


def _pause(hook: ApprovalHook, event: _Event) -> dict[str, Any]:
    with pytest.raises(_Interrupted):
        hook._before_tool(event)
    ((name, reason),) = event.asked[-1:]
    assert name == APPROVAL_INTERRUPT
    return dict(reason)


@dataclass
class _Interrupt:
    """A Strands ``Interrupt`` double."""

    id: str
    name: str = APPROVAL_INTERRUPT
    reason: Any = None


# --- registration -----------------------------------------------------------------
def test_the_hook_registers_its_three_callbacks() -> None:
    added: list[tuple[type, Any]] = []
    registry = type("Registry", (), {"add_callback": lambda _, e, cb: added.append((e, cb))})()
    hook = ApprovalHook()
    hook.register_hooks(registry)
    assert [e for e, _ in added] == [_BeforeToolCall, _AfterToolCall, _AfterInvocation]


def test_a_refusal_clears_the_approval_before_it_leaves_the_hook() -> None:
    hook = ApprovalHook()
    hook._before_tool(_event(response={"approved": True, "by": "reviewer"}))
    refusal = PIIDetected("refused")
    with pytest.raises(PIIDetected) as caught:
        hook._after_tool(SimpleNamespace(exception=refusal))
    assert caught.value is refusal
    assert approved_call("issue_refund") is None


def test_an_ordinary_exception_is_not_re_raised() -> None:
    ApprovalHook()._after_tool(SimpleNamespace(exception=ValueError("ordinary tool error")))


# --- GovernedToolHook: typed tool refusals without approvals (#1446) -------------
def test_the_governed_tool_hook_registers_only_the_after_tool_callback() -> None:
    added: list[tuple[type, Any]] = []
    registry = type("Registry", (), {"add_callback": lambda _, e, cb: added.append((e, cb))})()
    GovernedToolHook().register_hooks(registry)
    assert [e for e, _ in added] == [_AfterToolCall]


def test_the_governed_tool_hook_re_raises_a_refusal_typed() -> None:
    refusal = PIIDetected("refused")
    with pytest.raises(PIIDetected) as caught:
        GovernedToolHook()._after_tool(SimpleNamespace(exception=refusal))
    assert caught.value is refusal
    GovernedToolHook()._after_tool(SimpleNamespace(exception=ValueError("ordinary tool error")))
    GovernedToolHook()._after_tool(SimpleNamespace(exception=None))


def test_an_approval_hook_is_a_governed_tool_hook() -> None:
    assert isinstance(ApprovalHook(), GovernedToolHook)


# --- the pause --------------------------------------------------------------------
def test_an_ungated_or_unknown_tool_is_not_paused(recorded: list[tuple[str, Any]]) -> None:
    hook = ApprovalHook()
    for event in (_event(lookup), _event(None)):
        hook._before_tool(event)
        assert event.asked == [] and event.cancel_tool is False
    assert recorded == []


def test_a_call_the_tool_cannot_take_is_left_to_the_gate(
    recorded: list[tuple[str, Any]],
) -> None:
    event = _event(arguments={"nope": 1})
    ApprovalHook()._before_tool(event)
    assert event.asked == [] and recorded == []


def test_a_gated_call_pauses_and_records_the_pending_approval_once(
    recorded: list[tuple[str, Any]],
) -> None:
    hook = ApprovalHook()
    with run_scope("run-1"):
        reason = _pause(hook, _event())
        again = _pause(hook, _event())  # paused again before a decision
    assert reason == again
    assert reason["tool"] == "issue_refund" and reason["risk"] == "financial"
    assert reason["arguments"] == {"ticket_id": "T-1", "amount": 140.0}
    assert reason["correlation_id"] == "run-1"
    ((kind, approval),) = recorded
    assert kind == "pending" and approval.id == reason["approval_id"]


def test_the_approval_id_depends_on_the_call_as_sent() -> None:
    def one(**kw: Any) -> str:
        return str(_pause(ApprovalHook(), _event(**kw))["approval_id"])

    first = one()
    assert one() == first
    assert one(tool_use_id="tu-2") != first
    assert one(arguments={"ticket_id": "T-2"}) != first
    # A default's repr never reaches the id, so a restored session matches it.
    assert one(tool=tag_ticket) == one(tool=tag_ticket)


# --- the decision -----------------------------------------------------------------
def test_an_approved_call_runs_once_under_the_approval(
    recorded: list[tuple[str, Any]],
) -> None:
    hook = ApprovalHook()
    with run_scope("run-1"):
        reason = _pause(hook, _event())
    event = _event(response={"approved": True, "by": " m.keller "})
    hook._before_tool(event)

    approval = strands_mod._APPROVED.get()
    assert approval is not None and approval.id == reason["approval_id"]
    assert approval.correlation_id == "run-1"
    assert approved_call("lookup") is None
    assert recorded[-1] == ("approved", (approval, "m.keller"))
    assert event.cancel_tool is False

    ran.clear()
    assert issue_refund("T-1") == "refunded T-1"  # the gate lets it through
    assert ran == ["T-1"]
    # One approval runs one call, even before AfterToolCallEvent (#1438).
    assert approved_call("issue_refund") is None
    with run_scope("run-2", client=Approvals()), pytest.raises(ApprovalRequired):
        issue_refund("T-1")
    assert ran == ["T-1"]
    hook._after_tool(object())


def test_a_retried_call_records_its_decision_once_per_invocation(
    recorded: list[tuple[str, Any]],
) -> None:
    hook = ApprovalHook()
    agent = _Agent()
    for _ in range(2):  # a hook's retry re-fires BeforeToolCallEvent
        hook._before_tool(_event(agent=agent, response={"approved": True, "by": "m.keller"}))
    assert [kind for kind, _ in recorded] == ["approved"]

    hook._after_invocation(type("Done", (), {"agent": agent})())
    hook._before_tool(_event(agent=agent, response={"approved": True, "by": "m.keller"}))
    assert [kind for kind, _ in recorded] == ["approved", "approved"]
    hook._after_tool(object())


def test_a_rejected_call_is_cancelled_without_naming_the_rejecter(
    recorded: list[tuple[str, Any]],
) -> None:
    hook = ApprovalHook()
    event = _event(response={"approved": False, "by": "a.ops"})
    hook._before_tool(event)
    assert isinstance(event.cancel_tool, str)
    assert "issue_refund" in event.cancel_tool and "a.ops" not in event.cancel_tool
    assert recorded[-1][0] == "rejected" and recorded[-1][1][1] == "a.ops"
    assert approved_call("issue_refund") is None

    told = _event(response={"approved": False, "by": "a.ops", "message": "over limit"})
    ApprovalHook()._before_tool(told)
    assert told.cancel_tool == "over limit"


@pytest.mark.parametrize(
    "response",
    [
        "yes",
        {"approved": "yes", "by": "m.keller"},
        {"approved": True, "by": " "},
        {"approved": True},
        {"approved": False, "by": "a.ops", "message": 3},
    ],
)
def test_a_malformed_response_is_refused(response: Any, recorded: list[tuple[str, Any]]) -> None:
    with pytest.raises(ValueError, match="approve"):
        ApprovalHook()._before_tool(_event(response=response))
    assert recorded == []


# --- the helpers ------------------------------------------------------------------
def _paused_on(reason: dict[str, Any], interrupt_id: str = "i-1") -> Approval:
    (approval,) = pending_approvals(
        SimpleNamespace(interrupts=[_Interrupt(interrupt_id, reason=reason)])
    )
    return approval


def test_pending_approvals_reads_ddk_interrupts_in_order_as_approvals() -> None:
    with run_scope("run-1"):
        reason = _pause(ApprovalHook(), _event())
    ours = _Interrupt("i-1", reason=reason)
    theirs = _Interrupt("i-2", name="other", reason=reason)
    later = _Interrupt("i-3", reason={**reason, "approval_id": "a-3"})
    result = type("Result", (), {"interrupts": [ours, theirs, later]})()

    first, second = pending_approvals(result)
    assert (first.native, second.native) == (ours, later)
    assert first.id == reason["approval_id"] and second.id == "a-3"
    assert first.tool == "issue_refund" and first.risk == "financial"
    assert dict(first.arguments) == {"ticket_id": "T-1", "amount": 140.0}
    assert first.correlation_id == "run-1"
    assert first.requested_at == reason["requested_at"]
    assert first.expires_at == float("inf")
    assert pending_approvals(object()) == ()


def test_an_interrupt_with_a_partial_reason_still_reads() -> None:
    # A reason stored before requested_at was added, or edited by hand.
    approval = _paused_on({"approval_id": "a-1", "arguments": "nope", "risk": 3})
    assert approval.id == approval.correlation_id == "a-1"
    assert dict(approval.arguments) == {} and approval.risk is None
    assert approval.requested_at > 0
    assert _paused_on({}).id == "i-1"


def test_native_is_left_out_of_equality_and_the_repr() -> None:
    reason = {"approval_id": "a-1", "tool": "issue_refund", "requested_at": 1.0}
    one, other = _paused_on(reason, "i-1"), _paused_on(reason, "i-2")
    assert one.native != other.native
    assert one == other and hash(one.id) == hash(other.id)
    assert "native" not in repr(one) and "i-1" not in repr(one)


def test_approve_and_reject_return_the_approval_with_its_response() -> None:
    approval = _paused_on({"approval_id": "a-1", "tool": "issue_refund"})
    approved = approve(approval, by=" m.keller ")
    assert isinstance(approved, Approval) and approved == approval
    assert approved.native == {
        "interruptResponse": {
            "interruptId": "i-1",
            "response": {"approved": True, "by": "m.keller"},
        }
    }
    assert reject(approval, by="a.ops").native["interruptResponse"]["response"] == {
        "approved": False,
        "by": "a.ops",
    }
    told = reject(approval, by="a.ops", message="no").native
    assert json.loads(json.dumps(told))["interruptResponse"]["response"]["message"] == "no"
    # The input keeps its interrupt: it can be decided again, e.g. after a retry.
    assert isinstance(approval.native, _Interrupt)


def test_decisions_need_an_approver_and_a_paused_approval() -> None:
    approval = _paused_on({"approval_id": "a-1"})
    with pytest.raises(ValueError, match="by must name"):
        approve(approval, by=" ")
    with pytest.raises(ValueError, match="by must name"):
        reject(approval, by=None)  # type: ignore[arg-type]
    other = dataclasses.replace(approval, native=_Interrupt("i-2", name="other"))
    with pytest.raises(ValueError, match="not a paused Strands approval"):
        approve(other, by="m.keller")
    # An approval from donkey.approvals, or one the helpers did not build.
    with pytest.raises(ValueError, match="not a paused Strands approval"):
        reject(dataclasses.replace(approval, native=None), by="m.keller")
