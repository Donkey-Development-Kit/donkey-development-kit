"""A resumed approval runs as its requester, not as whoever resolves it (#1416).

``donkey.approvals.resolve()`` usually runs in the approver's request handler, so
without the requester's binding the tool's model calls would go out as the
service, or as the approver. The gate captures the ``donkey.as_user(...)``
binding and the attribution (cost tags, business group) where the call is
requested and re-binds them for the resumed call. A resolving context bound to a
different user is refused with ``AuthError`` and the approval stays pending.

The same rule holds on the native path every framework adapter shares: the pause
recorded with ``record_pending()`` keeps its requester in this process, and the
gate runs the approved call as that requester. A fake hook drives it here, so the
rule is pinned with no framework installed; each adapter's own approval tests
pin it through the framework.

Driven with ``httpx.MockTransport``, so no network is touched.
"""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Mapping
from typing import Any

import httpx
import pytest

from donkey_kit import ApprovalRequired, Approvals, Donkey, DonkeyConfig
from donkey_kit.core import approvals as approvals_mod
from donkey_kit.core.approvals import Approval, gate, record_pending
from donkey_kit.core.correlation import current_business_group, current_cost_tags
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import AuthError
from donkey_kit.core.identity import (
    Requester,
    capture_requester,
    check_requester,
    current_user,
    requester_scope,
)
from donkey_kit.core.transport.headers import PROXY_API_KEY_SENTINEL

_PROXY = "https://gw.example.internal/openai-sdk/"
_ALICE = "token-alice"
_BOB = "token-bob"
_SENTINEL = f"Bearer {PROXY_API_KEY_SENTINEL}"


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url=_PROXY,
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={})


def _wire(donkey: Donkey) -> _Recorder:
    proxy = _Recorder()
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(proxy))
    return proxy


async def _model_call(donkey: Donkey) -> None:
    # The OpenAI SDK pre-sets its api_key slot as the Authorization bearer.
    await donkey._http.post(
        f"{_PROXY}chat/completions", json={"model": "m"}, headers={"Authorization": _SENTINEL}
    )


Seen = list[tuple[str | None, CostTags | None, str | None]]


def _who() -> tuple[str | None, CostTags | None, str | None]:
    """The user token and attribution the calling code runs under."""
    user = current_user()
    return (user.token if user else None, current_cost_tags(), current_business_group())


def _refund_tool(seen: Seen) -> Any:
    @Donkey.tool(approval="required", risk="financial")
    def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        seen.append(_who())
        return "refunded"

    return issue_refund


async def _requested(call: Awaitable[Any]) -> Approval:
    with pytest.raises(ApprovalRequired) as info:
        await call
    return info.value.approval


def _requested_sync(tool: Any, *args: Any) -> Approval:
    with pytest.raises(ApprovalRequired) as info:
        tool(*args)
    return info.value.approval


# --- the identity seam --------------------------------------------------------
async def test_capture_records_the_user_and_attribution_bound_now() -> None:
    assert capture_requester() == Requester(None, None, None)
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            with donkey.attribution(team="payments", business_group="risk-eu"):
                requester = capture_requester()
    assert requester.user is not None and requester.user.token == _ALICE
    assert requester.cost == CostTags(team="payments")
    assert requester.business_group == "risk-eu"
    # The token never shows in the value's repr.
    assert _ALICE not in repr(requester)


async def test_the_scope_binds_exactly_the_requester_and_restores_on_exit() -> None:
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            with donkey.attribution(team="payments", business_group="risk-eu"):
                alice = capture_requester()
        with donkey.attribution(team="ops", project="p1", business_group="risk-us"):
            with requester_scope(alice, what="a call"):
                # Replaced, not merged: the requester set no project.
                assert _who() == (_ALICE, CostTags(team="payments"), "risk-eu")
            assert _who() == (None, CostTags(team="ops", project="p1"), "risk-us")
            with pytest.raises(RuntimeError), requester_scope(alice, what="a call"):
                raise RuntimeError("the call failed")
            assert _who() == (None, CostTags(team="ops", project="p1"), "risk-us")


async def test_no_requester_binds_nothing() -> None:
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_BOB):
            with requester_scope(None, what="a call"):
                assert _who() == (_BOB, None, None)


async def test_the_check_compares_tokens_not_scopes() -> None:
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            alice = capture_requester()
        # Unbound passes: the scope re-binds the requester.
        check_requester(alice, what="a call")
        # Another block with the same token is the same user.
        async with donkey.as_user(token=_ALICE):
            check_requester(alice, what="a call")
        async with donkey.as_user(token=_BOB):
            with pytest.raises(AuthError) as other:
                check_requester(alice, what="approval 1f for tool 'issue_refund'")
            with pytest.raises(AuthError) as service:
                check_requester(Requester(None, None, None), what="a call")
    assert "approval 1f for tool 'issue_refund'" in str(other.value)
    assert "another user's token" in str(other.value)
    assert "as the service" in str(service.value)
    for err in (other.value, service.value):
        assert "as_user()" in err.remediation
        for token in (_ALICE, _BOB):
            assert token not in str(err) and token not in err.remediation


async def test_matching_the_current_user_is_strict_where_the_check_lets_unbound_pass() -> None:
    service = Requester(None, None, None)
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            alice = capture_requester()
            assert alice.matches_current_user()
            assert not service.matches_current_user()
        # Unbound matches only a requester that had no user (#1418).
        assert service.matches_current_user()
        assert not alice.matches_current_user()
        async with donkey.as_user(token=_ALICE):
            assert alice.matches_current_user()
        async with donkey.as_user(token=_BOB):
            assert not alice.matches_current_user()


# --- resolve(): the raise-and-resume path -------------------------------------
async def test_resolve_sends_the_tools_model_calls_as_the_requester() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey)

        @donkey.tool(approval="required")
        async def summarise(ticket_id: str) -> str:
            """Summarise a ticket with the model."""
            await _model_call(donkey)
            return "summarised"

        async with donkey.as_user(token=_ALICE):
            approval = await _requested(summarise("T-1"))
        # The decision arrives in the approver's handler, outside as_user().
        resumed = donkey.approvals.resolve(approval.id, approved_by="m.keller")
        assert await resumed == "summarised"
        await _model_call(donkey)
    resumed_call, later = proxy.requests
    assert resumed_call.headers["Authorization"] == f"Bearer {_ALICE}"
    # The binding is the resumed call's only: the resolver's own calls stay its own.
    assert later.headers["Authorization"] == _SENTINEL


async def test_resolve_runs_the_tool_with_the_requesters_attribution() -> None:
    seen: Seen = []
    issue_refund = _refund_tool(seen)
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            with donkey.run(team="payments", business_group="risk-eu"):
                approval = _requested_sync(issue_refund, "T-1")
        with donkey.attribution(team="ops", business_group="risk-us"):
            assert donkey.approvals.resolve(approval.id, approved_by="m.keller") == "refunded"
            assert _who() == (None, CostTags(team="ops"), "risk-us")
    assert seen == [(_ALICE, CostTags(team="payments"), "risk-eu")]


async def test_a_service_request_drops_the_resolvers_attribution() -> None:
    seen: Seen = []
    issue_refund = _refund_tool(seen)
    approval = _requested_sync(issue_refund, "T-1")
    async with Donkey(_cfg()) as donkey:
        with donkey.attribution(team="ops", business_group="risk-us"):
            donkey.approvals.resolve(approval.id, approved_by="m.keller")
    assert seen == [(None, None, None)]


async def test_resolve_under_another_user_is_refused_and_stays_pending() -> None:
    seen: Seen = []
    issue_refund = _refund_tool(seen)
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            approval = _requested_sync(issue_refund, "T-1")
        async with donkey.as_user(token=_BOB):
            with pytest.raises(AuthError) as refused:
                donkey.approvals.resolve(approval.id, approved_by="m.keller")
        assert seen == []
        assert approval.id in str(refused.value)
        # Nothing was consumed: the approval can still be resolved properly.
        assert donkey.approvals.get(approval.id) == approval
        donkey.approvals.resolve(approval.id, approved_by="m.keller")
    assert seen == [(_ALICE, None, None)]


async def test_resolve_under_the_requesters_own_token_runs() -> None:
    seen: Seen = []
    issue_refund = _refund_tool(seen)
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            approval = _requested_sync(issue_refund, "T-1")
        async with donkey.as_user(token=_ALICE):
            donkey.approvals.resolve(approval.id, approved_by="m.keller")
    assert seen == [(_ALICE, None, None)]


async def test_a_service_request_resolved_inside_as_user_is_refused() -> None:
    seen: Seen = []
    issue_refund = _refund_tool(seen)
    approval = _requested_sync(issue_refund, "T-1")
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_BOB):
            with pytest.raises(AuthError, match="as the service"):
                donkey.approvals.resolve(approval.id, approved_by="m.keller")
    assert seen == []
    assert Approvals().get(approval.id) == approval


async def test_an_async_tool_awaited_under_another_user_is_refused() -> None:
    seen: Seen = []

    @Donkey.tool(approval="required")
    async def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        seen.append(_who())
        return "refunded"

    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            approval = await _requested(issue_refund("T-1"))
        resumed = donkey.approvals.resolve(approval.id, approved_by="m.keller")
        async with donkey.as_user(token=_BOB):
            with pytest.raises(AuthError):
                await resumed
    assert seen == []


async def test_reject_needs_no_identity_check() -> None:
    issue_refund = _refund_tool([])
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            approval = _requested_sync(issue_refund, "T-1")
        async with donkey.as_user(token=_BOB):
            assert donkey.approvals.reject(approval.id, rejected_by="m.keller") == approval


# --- the native path the framework adapters share ------------------------------
class _Paused(Exception):
    """What the fake framework raises to pause a call."""


class _FakeFramework:
    """A native hook that pauses a call once, then approves it on resume, the way
    the LangGraph, OpenAI Agents, ADK and Strands hooks do."""

    def __init__(self) -> None:
        self.approval: Approval | None = None

    def __call__(self, tool: str, arguments: Mapping[str, Any], risk: str | None) -> Approval:
        if self.approval is not None:
            return self.approval
        self.approval = Approval(
            id="native-1",
            tool=tool,
            arguments=arguments,
            risk=risk,
            correlation_id="native-1",
            requested_at=time.time(),
            expires_at=math.inf,
        )
        record_pending(self.approval)
        raise _Paused


def _native_tool(seen: Seen, framework: _FakeFramework) -> Any:
    def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        seen.append(_who())
        return "refunded"

    return gate(issue_refund, risk=None, timeout=60.0, natives=[framework])


async def test_a_native_resume_runs_as_the_requester_that_paused_it() -> None:
    seen: Seen = []
    tool = _native_tool(seen, _FakeFramework())
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            with donkey.attribution(business_group="risk-eu"), pytest.raises(_Paused):
                tool("T-1")
        # The framework resumes in the approver's context.
        with donkey.attribution(business_group="risk-us"):
            assert tool("T-1") == "refunded"
    assert seen == [(_ALICE, None, "risk-eu")]


async def test_a_native_resume_under_another_user_is_refused() -> None:
    seen: Seen = []
    tool = _native_tool(seen, _FakeFramework())
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            with pytest.raises(_Paused):
                tool("T-1")
        async with donkey.as_user(token=_BOB):
            with pytest.raises(AuthError, match="native-1"):
                tool("T-1")
    assert seen == []


async def test_a_pause_this_process_did_not_record_runs_as_the_resumer() -> None:
    """After a restart DDK holds no requester for a framework's pause, so the
    resuming code's own binding stands."""
    seen: Seen = []
    tool = _native_tool(seen, _FakeFramework())
    async with Donkey(_cfg()) as donkey:
        async with donkey.as_user(token=_ALICE):
            with pytest.raises(_Paused):
                tool("T-1")
        approvals_mod._reset_for_tests()
        async with donkey.as_user(token=_BOB):
            tool("T-1")
    assert seen == [(_BOB, None, None)]


def test_only_the_latest_native_pauses_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals_mod, "_NATIVE_KEPT", 2)
    for n in range(3):
        record_pending(Approval(f"a{n}", "issue_refund", {}, None, f"a{n}", time.time(), math.inf))
    assert list(approvals_mod._NATIVE_REQUESTERS) == ["a1", "a2"]
    # Recording a pause again refreshes it.
    record_pending(Approval("a1", "issue_refund", {}, None, "a1", time.time(), math.inf))
    assert list(approvals_mod._NATIVE_REQUESTERS) == ["a2", "a1"]


def test_a_raised_approval_is_not_kept_as_a_native_pause() -> None:
    _requested_sync(_refund_tool([]), "T-1")
    assert approvals_mod._NATIVE_REQUESTERS == {}
