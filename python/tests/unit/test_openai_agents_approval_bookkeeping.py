"""The OpenAI Agents SDK approval mapping's bookkeeping, with no Agents SDK
installed (BG §2.3, #213).

``pending_approvals``, ``approve``, ``reject`` and the gate's ``approved_call``
hook only read an interruption's ``name`` / ``call_id`` / ``arguments`` /
``agent.tools`` and call ``RunState.get_interruptions`` / ``approve`` /
``reject`` (docs/verified-apis.md §8), so these tests drive them with doubles
of that shape. The round trip through a real ``Runner.run`` is
``test_openai_agents_approvals.py``; this file keeps the mapping's own logic
covered on the legs that do not install ``openai-agents``.
"""

from __future__ import annotations

import gc
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

from donkey_kit import Donkey
from donkey_kit.core.correlation import run_scope
from donkey_kit.core.toolspec import TOOL_MARKER, tool_spec
from donkey_kit.integrations import openai_agents as ddk_agents


@Donkey.tool(approval="required", risk="financial")
def issue_refund(ticket_id: str, amount: float = 140.0) -> str:
    """Refund a support ticket."""
    return f"refunded {ticket_id}"


@Donkey.tool
def lookup(ticket_id: str) -> str:
    """Look a ticket up."""
    return ticket_id


async def _invoke(ctx: Any, arguments: str) -> Any:
    return arguments


def _native(fn: Any, name: str | None = None) -> Any:
    """A ``FunctionTool`` double whose ``on_invoke_tool`` carries ``fn``'s spec,
    as ``openai_agents.function_tool`` leaves it."""

    async def invoke(ctx: Any, arguments: str) -> Any:
        return await _invoke(ctx, arguments)

    setattr(invoke, TOOL_MARKER, tool_spec(fn))
    return type(
        "FunctionTool",
        (),
        {
            "name": name or fn.__name__,
            "qualified_name": name or fn.__name__,
            "defer_loading": False,
            "on_invoke_tool": invoke,
        },
    )()


@dataclass
class _Item:
    """A ``ToolApprovalItem`` double."""

    name: str
    call_id: str | None
    arguments: str | None
    agent: Any
    tool_namespace: str | None = None

    @property
    def qualified_name(self) -> str:
        return self.name


@dataclass
class _State:
    """A ``RunState`` double that records the decisions handed to it."""

    items: list[_Item]
    approved: list[_Item] = field(default_factory=list)
    rejected: list[tuple[_Item, str | None]] = field(default_factory=list)

    def get_interruptions(self) -> list[_Item]:
        return list(self.items)

    def approve(self, item: _Item) -> None:
        self.approved.append(item)

    def reject(self, item: _Item, *, rejection_message: str | None = None) -> None:
        self.rejected.append((item, rejection_message))


def _agent(*tools: Any) -> Any:
    return type("Agent", (), {"tools": list(tools)})()


def _state(arguments: dict[str, Any] | str | None = None, call_id: str = "call-1") -> _State:
    raw = json.dumps({"ticket_id": "T-1"} if arguments is None else arguments)
    raw = arguments if isinstance(arguments, str) else raw
    return _State([_Item("issue_refund", call_id, raw, _agent(_native(issue_refund)))])


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        ddk_agents, "record_pending", lambda approval: events.append(("pending", approval))
    )

    def decision(approval: Any, *, approved: bool, by: str) -> None:
        events.append(("approved" if approved else "rejected", (approval, by)))

    monkeypatch.setattr(ddk_agents, "record_decision", decision)
    return events


# --- pending_approvals ----------------------------------------------------------
def test_pending_approvals_reads_a_result_and_records_each_gated_call(
    recorded: list[tuple[str, Any]],
) -> None:
    state = _state()
    result = type("RunResult", (), {"interruptions": state.items})()
    with run_scope("run-9"):
        (approval,) = ddk_agents.pending_approvals(result)

    assert approval.tool == "issue_refund"
    assert dict(approval.arguments) == {"ticket_id": "T-1", "amount": 140.0}
    assert (approval.risk, approval.correlation_id) == ("financial", "run-9")
    assert approval.expires_at == math.inf
    assert recorded == [("pending", approval)]


def test_pending_approvals_reads_a_run_state_too(recorded: list[tuple[str, Any]]) -> None:
    (approval,) = ddk_agents.pending_approvals(_state())  # type: ignore[arg-type]
    # Outside a run, the approval is its own correlation id.
    assert approval.correlation_id == approval.id
    assert len(recorded) == 1


def test_pending_approvals_skips_calls_it_did_not_gate(recorded: list[tuple[str, Any]]) -> None:
    plain = type("FunctionTool", (), {"name": "issue_refund", "on_invoke_tool": _invoke})()
    items = [
        _Item("issue_refund", "c1", "{}", _agent(plain)),  # the SDK's own tool
        _Item("lookup", "c2", "{}", _agent(_native(lookup))),  # ungated @donkey.tool
        _Item("issue_refund", "c3", "{}", _agent(_native(issue_refund, "other"))),
        _Item("issue_refund", "c4", "{}", object()),  # an agent with no tools
    ]
    assert ddk_agents.pending_approvals(_State(items)) == ()  # type: ignore[arg-type]
    assert recorded == []


def test_the_id_depends_on_the_call_the_tool_and_the_arguments() -> None:
    def one(state: _State) -> str:
        (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
        return approval.id

    first = one(_state())
    assert one(_state()) == first
    assert one(_state(call_id="call-2")) != first
    assert one(_state({"ticket_id": "T-2"})) != first
    # The id is taken from the call as the model sent it, which the pause and the
    # resumed call share, so a default sent explicitly is a different call.
    assert one(_state({"ticket_id": "T-1", "amount": 140.0})) != first


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("not json", {}),  # unparsable
        ("[1, 2]", {}),  # not an object
        ("", {}),
        # A keyword the tool does not take: shown as the model sent it.
        ('{"ticket_id": "T-1", "note": "x"}', {"ticket_id": "T-1", "note": "x"}),
    ],
)
def test_arguments_the_tool_cannot_bind_are_shown_as_sent(
    raw: str, expected: dict[str, Any]
) -> None:
    (approval,) = ddk_agents.pending_approvals(_state(raw))  # type: ignore[arg-type]
    assert dict(approval.arguments) == expected


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


def test_the_approval_id_does_not_depend_on_a_default_s_repr() -> None:
    item = _Item("tag_ticket", "call-1", '{"ticket_id": "T-1"}', _agent(_native(tag_ticket)))
    first = ddk_agents.pending_approvals(_State([item]))  # type: ignore[arg-type]
    again = ddk_agents.pending_approvals(_State([item]))  # type: ignore[arg-type]
    assert first[0].id == again[0].id


# --- approve / reject -------------------------------------------------------------
def test_approve_hands_the_item_to_the_state_and_records_the_approver(
    recorded: list[tuple[str, Any]],
) -> None:
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    decided = ddk_agents.approve(state, approval.id, by=" m.keller ")  # type: ignore[arg-type]

    assert state.approved == state.items
    assert decided.id == approval.id
    assert recorded[-1] == ("approved", (decided, "m.keller"))


def test_reject_sends_a_default_message_without_the_rejecter(
    recorded: list[tuple[str, Any]],
) -> None:
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    ddk_agents.reject(state, approval, by="a.ops")  # type: ignore[arg-type]

    ((item, told),) = state.rejected
    assert item is state.items[0]
    assert told is not None and "issue_refund" in told and approval.id not in told
    assert "a.ops" not in told
    assert recorded[-1][0] == "rejected" and recorded[-1][1][1] == "a.ops"


def test_reject_sends_the_callers_message(recorded: list[tuple[str, Any]]) -> None:
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    ddk_agents.reject(state, approval, by="a.ops", message="Ask a manager.")  # type: ignore[arg-type]
    assert state.rejected[0][1] == "Ask a manager."


@pytest.mark.parametrize("by", ["", "  ", None])
@pytest.mark.parametrize("decide", [ddk_agents.approve, ddk_agents.reject])
def test_a_blank_decider_decides_nothing(decide: Any, by: Any) -> None:
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="by must name who decided"):
        decide(state, approval, by=by)
    assert state.approved == [] and state.rejected == []


@pytest.mark.parametrize("decide", [ddk_agents.approve, ddk_agents.reject])
def test_an_approval_with_no_paused_call_is_a_key_error(decide: Any) -> None:
    with pytest.raises(KeyError, match="no paused call"):
        decide(_state(), "not-an-approval", by="m.keller")


# --- one decision per paused call --------------------------------------------------
@pytest.mark.parametrize(
    ("first", "second"),
    [
        (ddk_agents.approve, ddk_agents.approve),
        (ddk_agents.approve, ddk_agents.reject),
        (ddk_agents.reject, ddk_agents.approve),
        (ddk_agents.reject, ddk_agents.reject),
    ],
)
def test_a_call_is_decided_once_per_state(
    first: Any, second: Any, recorded: list[tuple[str, Any]]
) -> None:
    """The SDK still lists a decided call in ``get_interruptions()``, so a second
    decision would record a contradicting audit event: it is a ``KeyError``."""
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    first(state, approval, by="alice")
    with pytest.raises(KeyError, match="already decided"):
        second(state, approval.id, by="bob")

    assert len(state.approved) + len(state.rejected) == 1
    decisions = [by for kind, (_, by) in recorded[1:]]
    assert decisions == ["alice"]


def test_another_state_decides_on_its_own(recorded: list[tuple[str, Any]]) -> None:
    one, two = _state(), _state()
    (approval,) = ddk_agents.pending_approvals(one)  # type: ignore[arg-type]
    ddk_agents.approve(one, approval, by="alice")  # type: ignore[arg-type]
    ddk_agents.approve(two, approval, by="alice")  # type: ignore[arg-type]
    assert len(one.approved) == len(two.approved) == 1


def test_a_decision_the_state_refuses_can_be_made_again(
    recorded: list[tuple[str, Any]],
) -> None:
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]

    def broken(item: _Item) -> None:
        raise RuntimeError("state refused")

    state.approve = broken  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="state refused"):
        ddk_agents.approve(state, approval, by="alice")  # type: ignore[arg-type]
    assert [kind for kind, _ in recorded] == ["pending"]  # nothing recorded
    del state.approve  # back to the class's own method
    ddk_agents.approve(state, approval, by="alice")  # type: ignore[arg-type]
    assert state.approved == state.items


def test_a_collected_state_is_forgotten() -> None:
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    ddk_agents.approve(state, approval, by="alice")  # type: ignore[arg-type]
    key = id(state)
    assert key in ddk_agents._DECIDED
    del state
    gc.collect()
    assert key not in ddk_agents._DECIDED


# --- the decision keeps the approval's correlation -------------------------------
@pytest.mark.parametrize("decide", [ddk_agents.approve, ddk_agents.reject])
def test_a_decision_outside_the_run_keeps_the_pauses_correlation(
    decide: Any, recorded: list[tuple[str, Any]]
) -> None:
    state = _state()
    with run_scope("run-X"):
        (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    decided = decide(state, approval, by="alice")

    assert decided is approval
    assert (decided.correlation_id, decided.requested_at) == ("run-X", approval.requested_at)
    assert recorded[-1][1][0].correlation_id == "run-X"


def test_a_decision_by_id_is_the_approval_as_of_now(recorded: list[tuple[str, Any]]) -> None:
    state = _state()
    with run_scope("run-X"):
        (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    decided = ddk_agents.approve(state, approval.id, by="alice")  # type: ignore[arg-type]
    # An id carries no correlation: outside a run, the approval is its own.
    assert decided.id == approval.id
    assert decided.correlation_id == approval.id


# --- the gate's hook ----------------------------------------------------------------
def test_approved_call_runs_one_call_of_the_approved_tool() -> None:
    state = _state()
    (approval,) = ddk_agents.pending_approvals(state)  # type: ignore[arg-type]
    token = ddk_agents._APPROVED.set(approval)
    try:
        assert ddk_agents.approved_call("lookup", {}, None) is None  # another tool
        assert ddk_agents.approved_call("issue_refund", {}, None) is approval
        assert ddk_agents.approved_call("issue_refund", {}, None) is None  # spent
    finally:
        ddk_agents._APPROVED.reset(token)


def test_the_gate_asks_the_hook_only_once_agents_is_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from donkey_kit import donkey as donkey_mod

    monkeypatch.delitem(sys.modules, "agents", raising=False)
    assert donkey_mod._openai_agents_approval("issue_refund", {}, None) is None


# --- function_tool before the SDK is touched -----------------------------------
def test_function_tool_checks_its_tool_before_importing_the_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "agents", None)  # importing it would fail
    with pytest.raises(TypeError, match="takes a @donkey.tool"):
        ddk_agents.function_tool(_invoke)
    with pytest.raises(TypeError, match="drop needs_approval"):
        ddk_agents.function_tool(issue_refund, needs_approval=False)


def test_function_tool_without_the_extra_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "agents", None)
    with pytest.raises(ImportError, match="openai-agents"):
        ddk_agents.function_tool(issue_refund)
