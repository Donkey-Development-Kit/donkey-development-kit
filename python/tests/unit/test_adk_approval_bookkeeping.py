"""The ADK approval mapping's bookkeeping, with no ADK installed (BG §2.3, #1235).

The plugin :func:`~donkey_kit.integrations.adk.approval_plugin` builds only
reads a tool's ``func`` and ``_context_param_name``, the ``ToolContext``'s
``function_call_id`` / ``invocation_id`` / ``tool_confirmation`` / ``actions``
/ ``session.events``, and calls its ``request_confirmation(hint=, payload=)``; it subclasses
``google.adk.plugins.BasePlugin``, and :func:`approve` / :func:`reject` build
``google.genai.types`` parts (docs/verified-apis.md §8). These tests drive it
with doubles of that shape and stand-in modules. The round trip through a real
ADK app is ``test_an_adk_agent_pauses_with_its_own_tool_confirmation`` in
``tests/conformance/test_approval_round_trip.py``; this file keeps the
mapping's own logic covered on the legs that do not install ``google-adk``.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from collections.abc import Coroutine
from dataclasses import dataclass, field
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from donkey_kit import ApprovalRequired, Donkey
from donkey_kit import donkey as donkey_mod
from donkey_kit.core.approvals import Approval
from donkey_kit.core.correlation import run_scope
from donkey_kit.integrations import adk as adk_mod
from donkey_kit.integrations.adk import (
    APPROVAL_KIND,
    approval_plugin,
    approve,
    approved_call,
    pending_approvals,
    reject,
)


class _BasePlugin:
    """Stands in for ``google.adk.plugins.BasePlugin``."""

    def __init__(self, name: str) -> None:
        self.name = name


@pytest.fixture(autouse=True)
def fake_adk(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The ``google.adk.plugins`` and ``google.genai.types`` modules the mapping
    imports, as stand-ins; the plugin class is rebuilt on them."""
    plugins = ModuleType("google.adk.plugins")
    plugins.BasePlugin = _BasePlugin  # type: ignore[attr-defined]
    genai = ModuleType("google.genai")
    genai.types = SimpleNamespace(Part=SimpleNamespace, FunctionResponse=SimpleNamespace)  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules, "google.adk", sys.modules.get("google.adk", ModuleType("google.adk"))
    )
    monkeypatch.setitem(sys.modules, "google.adk.plugins", plugins)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    adk_mod._approval_plugin_class.cache_clear()
    yield
    adk_mod._approval_plugin_class.cache_clear()
    adk_mod._APPROVED.set(None)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    monkeypatch.setattr(adk_mod, "record_pending", lambda a: events.append(("pending", a)))

    def decision(approval: Any, *, approved: bool, by: str) -> None:
        events.append(("approved" if approved else "rejected", (approval, by)))

    monkeypatch.setattr(adk_mod, "record_decision", decision)
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


@dataclass
class _Event:
    """An ADK ``Event`` double."""

    calls: list[Any] = field(default_factory=list)
    responses: list[Any] = field(default_factory=list)
    author: str = "refunds"
    #: ``actions.requested_tool_confirmations``: function-call id to payload.
    asked: dict[str, Any] = field(default_factory=dict)

    @property
    def actions(self) -> Any:
        confirmations = {k: SimpleNamespace(payload=v) for k, v in self.asked.items()}
        return SimpleNamespace(requested_tool_confirmations=confirmations)

    def get_function_calls(self) -> list[Any]:
        return self.calls

    def get_function_responses(self) -> list[Any]:
        return self.responses


@dataclass
class _Context:
    """A ``ToolContext`` double. ``confirmation`` is the ``ToolConfirmation`` a
    resume carries; ``None`` pauses. ``events`` is the session's history."""

    confirmation: Any = None
    function_call_id: str = "fc-1"
    invocation_id: str = "inv-1"
    events: list[Any] = field(default_factory=list)
    actions: Any = field(default_factory=lambda: SimpleNamespace(skip_summarization=False))
    asked: list[dict[str, Any]] = field(default_factory=list)

    @property
    def session(self) -> Any:
        return SimpleNamespace(events=self.events)

    @property
    def tool_confirmation(self) -> Any:
        return self.confirmation

    def request_confirmation(self, *, hint: str, payload: Any) -> None:
        self.asked.append({"hint": hint, "payload": payload})


def _confirmed(approved: Any, payload: Any) -> Any:
    return SimpleNamespace(confirmed=approved, payload=payload)


def _paused(payload: dict[str, Any], call_id: str = "fc-1") -> list[Any]:
    """The session history a pause of ``payload`` leaves: the agent's event
    holding the requested confirmation and the call's placeholder result."""
    placeholder = {"status": "awaiting approval", "approval_id": payload["approval_id"]}
    response = SimpleNamespace(id=call_id, response=placeholder)
    return [_Event(responses=[response], asked={call_id: payload})]


def _resume(payload: dict[str, Any], approved: Any = True, **decision: Any) -> _Context:
    """The context of the resumed call, after a pause of ``payload``."""
    answer = {**payload, "by": "m.keller", **decision}
    return _Context(_confirmed(approved, answer), events=_paused(payload))


def _sync(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a callback that never suspends in this context, so what it sets in
    a context variable stays visible here, as on ADK's per-call task."""
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    raise AssertionError("the callback suspended")


def _before(
    plugin: Any,
    context: _Context,
    tool: Any = issue_refund,
    args: dict[str, Any] | None = None,
    context_param: str | None = None,
) -> Any:
    adk_tool = SimpleNamespace(func=tool, _context_param_name=context_param)
    return _sync(
        plugin.before_tool_callback(
            tool=adk_tool, tool_args=args or {"ticket_id": "T-1"}, tool_context=context
        )
    )


def _pause(plugin: Any, **kw: Any) -> dict[str, Any]:
    context = _Context(**{k: v for k, v in kw.items() if k in {"function_call_id"}})
    answer = _before(plugin, context, kw.get("tool", issue_refund), kw.get("args"))
    ((asked,),) = [context.asked]
    assert answer == {"status": "awaiting approval", "approval_id": asked["payload"]["approval_id"]}
    assert context.actions.skip_summarization is True
    return dict(asked["payload"])


def _request(
    payload: dict[str, Any], call_id: str = "c-1", name: str = "adk_request_confirmation"
) -> Any:
    args = {"originalFunctionCall": {"id": "fc-1"}, "toolConfirmation": {"payload": payload}}
    return SimpleNamespace(id=call_id, name=name, args=args)


# --- the pause --------------------------------------------------------------------
def test_the_plugin_is_an_adk_plugin() -> None:
    plugin = approval_plugin()
    assert isinstance(plugin, _BasePlugin) and plugin.name == "donkey_approval"
    assert approval_plugin("mine").name == "mine"


def test_an_ungated_or_unknown_tool_is_not_paused(recorded: list[tuple[str, Any]]) -> None:
    plugin = approval_plugin()
    for tool in (lookup, None, "not callable"):
        context = _Context()
        assert _before(plugin, context, tool) is None
        assert context.asked == []
    assert recorded == []


def test_a_call_missing_an_argument_is_left_to_adk(
    recorded: list[tuple[str, Any]],
) -> None:
    # ADK answers it with its own error and never runs the tool.
    context = _Context()
    assert _before(approval_plugin(), context, args={"amount": 1.0}) is None
    assert context.asked == [] and recorded == []


def test_an_argument_the_tool_does_not_take_is_dropped_as_adk_drops_it(
    recorded: list[tuple[str, Any]],
) -> None:
    payload = _pause(approval_plugin(), args={"ticket_id": "T-1", "bogus": 1})
    assert payload["arguments"] == {"ticket_id": "T-1"}
    assert payload["approval_id"] == _pause(approval_plugin())["approval_id"]


@Donkey.tool(approval="required")
def close_ticket(ticket_id: str, tool_context: Any, ctx: Any = None) -> str:
    """Close a support ticket."""
    return ticket_id


@pytest.mark.parametrize(("context_param", "kept"), [(None, "ctx"), ("ctx", "tool_context")])
def test_the_tools_context_parameter_is_not_an_argument(
    context_param: str | None, kept: str, recorded: list[tuple[str, Any]]
) -> None:
    # ADK passes its ToolContext as the parameter it names (tool_context by
    # default), not from the model's arguments.
    context = _Context()
    args = {"ticket_id": "T-1", "tool_context": "forged", "ctx": "forged"}
    _before(approval_plugin(), context, close_ticket, args, context_param)
    ((asked,),) = [context.asked]
    assert set(asked["payload"]["arguments"]) == {"ticket_id", kept}
    ((_, approval),) = recorded
    assert set(approval.arguments) == {"ticket_id", kept}


def test_a_gated_call_pauses_and_records_the_pending_approval(
    recorded: list[tuple[str, Any]],
) -> None:
    with run_scope("run-1"):
        payload = _pause(approval_plugin())
    assert payload["kind"] == APPROVAL_KIND
    assert payload["tool"] == "issue_refund" and payload["risk"] == "financial"
    # The arguments as the model sent them: the session stores them as JSON.
    assert payload["arguments"] == {"ticket_id": "T-1"}
    assert payload["correlation_id"] == "run-1"
    json.dumps(payload)
    ((kind, approval),) = recorded
    assert kind == "pending" and approval.id == payload["approval_id"]
    assert dict(approval.arguments) == {"ticket_id": "T-1", "amount": 140.0}


def test_the_approval_id_depends_on_the_call_as_sent() -> None:
    def one(**kw: Any) -> str:
        return str(_pause(approval_plugin(), **kw)["approval_id"])

    first = one()
    assert one() == first
    assert one(function_call_id="fc-2") != first
    assert one(args={"ticket_id": "T-2"}) != first
    assert one(args={"ticket_id": "T-1", "amount": 140.0}) != first
    # A default's repr never reaches the id, so a fresh process matches it.
    assert one(tool=tag_ticket) == one(tool=tag_ticket)


# --- the decision -----------------------------------------------------------------
def test_an_approved_call_runs_once_under_the_pause(
    recorded: list[tuple[str, Any]],
) -> None:
    with run_scope("run-1"):
        payload = _pause(approval_plugin())
    # A fresh plugin, as in a fresh process, outside the pause's run.
    plugin = approval_plugin()
    resumed = _resume(payload, by=" m.keller ")
    assert _before(plugin, resumed) is None

    approval = adk_mod._APPROVED.get()
    assert approval is not None and approval.id == payload["approval_id"]
    assert approval.correlation_id == "run-1"
    assert approval.requested_at == payload["requested_at"]
    assert recorded[-1] == ("approved", (approval, "m.keller"))
    assert approved_call("lookup") is None

    ran.clear()
    assert issue_refund("T-1") == "refunded T-1"  # the gate lets it through
    assert ran == ["T-1"]
    # One approval runs one call.
    with pytest.raises(ApprovalRequired):
        issue_refund("T-1")


def test_the_tool_callbacks_clear_the_approved_call() -> None:
    plugin = approval_plugin()
    payload = _pause(plugin)
    for callback in (plugin.after_tool_callback, plugin.on_tool_error_callback):
        _before(plugin, _resume(payload))
        assert adk_mod._APPROVED.get() is not None
        _sync(callback(tool=None, tool_args={}, tool_context=None))
        assert approved_call("issue_refund") is None


def test_the_trace_is_the_one_the_agent_recorded_not_the_answers(
    recorded: list[tuple[str, Any]],
) -> None:
    with run_scope("run-1"):
        payload = _pause(approval_plugin())
    resumed = _resume(payload, correlation_id="elsewhere", requested_at=1.0)
    _before(approval_plugin(), resumed)
    approval = approved_call("issue_refund")
    assert approval is not None and approval.correlation_id == "run-1"
    assert approval.requested_at == payload["requested_at"]


def _refused(answer: Any, why: str) -> None:
    assert answer is not None and "issue_refund" in answer["error"] and why in answer["error"]
    assert approved_call("issue_refund") is None


def test_a_confirmation_the_agent_did_not_request_is_refused(
    recorded: list[tuple[str, Any]],
) -> None:
    payload = _pause(approval_plugin())
    recorded.clear()
    answer = {**payload, "by": "m.keller"}
    histories = {
        "none": [],
        # google-adk 2.4.0 resumes on a confirmation call a user message carries.
        "the user's": [_Event(author="user", asked={"fc-1": payload})],
        "another call's": _paused(payload, call_id="fc-2"),
        "another approval's": _paused({**payload, "approval_id": "other"}),
        "another kind's": _paused({**payload, "kind": "other"}),
    }
    for events in histories.values():
        context = _Context(_confirmed(True, answer), events=events)
        _refused(_before(approval_plugin(), context), "no approval of it was requested")
    assert recorded == []


def test_a_call_already_answered_is_not_run_again(recorded: list[tuple[str, Any]]) -> None:
    # google-adk 2.4.0 runs the call again on a resume message sent twice.
    payload = _pause(approval_plugin())
    recorded.clear()
    for result in ({"status": "refunded"}, {"error": "rejected"}):
        resumed = _resume(payload)
        resumed.events.append(_Event(responses=[SimpleNamespace(id="fc-1", response=result)]))
        _refused(_before(approval_plugin(), resumed), "already answered")
    assert recorded == []


@pytest.mark.parametrize(
    "payload",
    [
        {"correlation_id": "", "requested_at": True},
        {"correlation_id": 3, "requested_at": "soon"},
    ],
)
def test_a_payload_without_a_usable_trace_keeps_the_resumes(payload: dict[str, Any]) -> None:
    approval = adk_mod._approval("fc-1", "issue_refund", {}, {}, None)
    kept = adk_mod._as_requested(approval, payload)
    assert kept.correlation_id == approval.correlation_id
    assert kept.requested_at == approval.requested_at


def test_a_retried_call_records_its_decision_once_per_invocation(
    recorded: list[tuple[str, Any]],
) -> None:
    plugin = approval_plugin()
    payload = _pause(plugin)
    recorded.clear()
    for _ in range(2):
        _before(plugin, _resume(payload))
    assert [kind for kind, _ in recorded] == ["approved"]

    _sync(plugin.after_run_callback(invocation_context=SimpleNamespace(invocation_id="inv-1")))
    _before(plugin, _resume(payload))
    assert [kind for kind, _ in recorded] == ["approved", "approved"]


def test_the_plugin_keeps_only_the_latest_invocations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adk_mod, "_INVOCATIONS_KEPT", 2)
    plugin = approval_plugin()
    payload = _pause(plugin)
    for invocation in ("inv-1", "inv-2", "inv-3"):
        resumed = _resume(payload)
        resumed.invocation_id = invocation
        _before(plugin, resumed)
    assert list(plugin._decided) == ["inv-2", "inv-3"]


def test_a_rejected_call_is_answered_without_naming_the_rejecter(
    recorded: list[tuple[str, Any]],
) -> None:
    payload = _pause(approval_plugin())
    answer = _before(approval_plugin(), _resume(payload, False, by="a.ops"))
    assert "issue_refund" in answer["error"] and "a.ops" not in answer["error"]
    assert recorded[-1][0] == "rejected" and recorded[-1][1][1] == "a.ops"
    assert approved_call("issue_refund") is None

    told = _resume(payload, False, by="a.ops", message="over limit")
    assert _before(approval_plugin(), told) == {"error": "over limit"}


@pytest.mark.parametrize(
    "answer",
    [
        lambda p: _confirmed(True, None),
        # A plain confirmation, as ADK's own UI sends.
        lambda p: _confirmed(True, {}),
        lambda p: _confirmed(True, {"by": "m.keller"}),
        lambda p: _confirmed("yes", {**p, "by": "m.keller"}),
        lambda p: _confirmed(True, {**p, "by": " "}),
        lambda p: _confirmed(True, {**p, "kind": "other", "by": "m.keller"}),
        lambda p: _confirmed(True, {**p, "approval_id": "other", "by": "m.keller"}),
        lambda p: _confirmed(False, {**p, "by": "a.ops", "message": 3}),
    ],
)
def test_a_malformed_confirmation_is_refused_without_running(
    answer: Any, recorded: list[tuple[str, Any]]
) -> None:
    payload = _pause(approval_plugin())
    recorded.clear()
    context = _Context(answer(payload), events=_paused(payload))
    _refused(_before(approval_plugin(), context), "approve() or reject()")
    assert recorded == []


# --- the helpers ------------------------------------------------------------------
def _pending(payload: dict[str, Any], call_id: str = "c-1") -> Approval:
    (approval,) = pending_approvals([_Event(calls=[_request(payload, call_id)])])
    return approval


def test_pending_approvals_reads_unanswered_ddk_requests_in_order_as_approvals() -> None:
    with run_scope("run-1"):
        payload = _pause(approval_plugin())
    ours, later = _request(payload, "c-1"), _request({**payload, "approval_id": "a-3"}, "c-3")
    theirs = _request({"kind": "other"}, "c-2")
    tool_call = SimpleNamespace(id="fc-1", name="issue_refund", args={"ticket_id": "T-1"})
    events = [_Event(calls=[tool_call]), _Event(calls=[ours, theirs, later])]

    first, second = pending_approvals(events)
    assert (first.native, second.native) == (ours, later)
    assert first.id == payload["approval_id"] and second.id == "a-3"
    assert first.tool == "issue_refund" and first.risk == "financial"
    assert dict(first.arguments) == payload["arguments"]
    assert first.correlation_id == "run-1"
    assert first.requested_at == payload["requested_at"]
    assert first.expires_at == float("inf")

    answered = _Event(responses=[SimpleNamespace(id="c-1")])
    assert [a.native for a in pending_approvals([*events, answered])] == [later]
    assert pending_approvals([]) == ()


def test_a_request_with_a_partial_payload_still_reads() -> None:
    approval = _pending({"kind": APPROVAL_KIND, "arguments": "nope", "risk": 3})
    assert approval.id == approval.correlation_id == "c-1"
    assert dict(approval.arguments) == {} and approval.risk is None
    assert approval.requested_at > 0


def test_approve_and_reject_return_the_approval_with_its_response() -> None:
    approval = _pending({"kind": APPROVAL_KIND, "approval_id": "a-1"})
    approved = approve(approval, by=" m.keller ")
    assert isinstance(approved, Approval) and approved == approval
    response = approved.native.function_response
    assert (response.id, response.name) == ("c-1", "adk_request_confirmation")
    assert response.response == {
        "confirmed": True,
        "payload": {"kind": APPROVAL_KIND, "approval_id": "a-1", "by": "m.keller"},
    }
    rejected = reject(approval, by="a.ops").native.function_response.response
    assert rejected["confirmed"] is False and rejected["payload"]["by"] == "a.ops"
    told = reject(approval, by="a.ops", message="no").native.function_response.response
    assert json.loads(json.dumps(told))["payload"]["message"] == "no"
    assert approval.native.name == "adk_request_confirmation"


def test_decisions_need_an_approver_and_a_paused_approval() -> None:
    ours = _pending({"kind": APPROVAL_KIND})
    with pytest.raises(ValueError, match="by must name"):
        approve(ours, by=" ")
    with pytest.raises(ValueError, match="by must name"):
        reject(ours, by=None)  # type: ignore[arg-type]
    others = (
        _request({"kind": "other"}),
        _request({"kind": APPROVAL_KIND}, name="tool"),
        None,
    )
    for other in others:
        with pytest.raises(ValueError, match="not a paused ADK approval"):
            approve(dataclasses.replace(ours, native=other), by="m.keller")


# --- the gate's native hook ---------------------------------------------------------
def test_the_native_hook_waits_for_adk_to_be_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    approval = adk_mod._approval("fc-1", "issue_refund", {}, {}, None)
    adk_mod._APPROVED.set(approval)
    assert donkey_mod._adk_approval("issue_refund", {}, None) is approval
    adk_mod._APPROVED.set(approval)
    monkeypatch.delitem(sys.modules, "google.adk")
    assert donkey_mod._adk_approval("issue_refund", {}, None) is None
