"""``@donkey.tool(approval="required")``: pause a tool for a human (BG §2.3, #211).

A gated call records a pending approval and raises ``ApprovalRequired``;
``donkey.approvals.resolve()`` records the approver and runs the call under the
approval's correlation id; an unresolved approval times out with a typed error.
The approval is advisory and never overrides a gateway refusal.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from donkey_kit import (
    Approval,
    ApprovalError,
    ApprovalNotFound,
    ApprovalRejected,
    ApprovalRequired,
    Approvals,
    ApprovalTimedOut,
    ConfigError,
    Donkey,
    DonkeyConfig,
    DonkeyError,
    GatewayApprovalDenied,
    PIIDetected,
    PolicyViolation,
    registered_tools,
)
from donkey_kit.core import approval_types, telemetry
from donkey_kit.core import approvals as approvals_mod
from donkey_kit.core.correlation import current_approvals_client
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.core.toolspec import tool_spec


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    for module in (approvals_mod, approval_types):
        monkeypatch.setattr(module, "time", SimpleNamespace(time=fake.time))
    return fake


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


@pytest.fixture
def donkey() -> Iterator[Donkey]:
    with Donkey(_cfg()) as client:
        yield client


def _requested(call: Any) -> ApprovalRequired:
    with pytest.raises(ApprovalRequired) as info:
        call()
    return info.value


# --- the decorator ----------------------------------------------------------
def test_tool_with_empty_parentheses_is_still_the_identity() -> None:
    def lookup_crm(customer_id: str) -> str:
        """Look a customer up."""
        return customer_id

    assert Donkey.tool()(lookup_crm) is lookup_crm
    (spec,) = registered_tools()
    assert (spec.approval, spec.risk) == (None, None)


def test_risk_without_approval_is_recorded_and_changes_nothing() -> None:
    def lookup_crm(customer_id: str) -> str:
        """Look a customer up."""
        return customer_id

    assert Donkey.tool(risk="pii")(lookup_crm) is lookup_crm
    assert lookup_crm("c-1") == "c-1"
    (spec,) = registered_tools()
    assert spec.risk == "pii"


def test_a_gated_tool_keeps_its_name_docstring_signature_and_asyncness() -> None:
    @Donkey.tool(approval="required", risk="financial")
    async def issue_refund(ticket_id: str, amount: float) -> str:
        """Refund a ticket."""
        return ticket_id

    assert issue_refund.__name__ == "issue_refund"
    assert issue_refund.__doc__ == "Refund a ticket."
    assert list(inspect.signature(issue_refund).parameters) == ["ticket_id", "amount"]
    assert inspect.iscoroutinefunction(issue_refund)
    spec = tool_spec(issue_refund)
    assert spec is not None
    assert (spec.approval, spec.risk, spec.is_async) == ("required", "financial", True)
    # The registry hands out the gate, so a consumer calling it is gated too.
    assert spec.func is issue_refund


@pytest.mark.parametrize(
    "kwargs",
    [
        {"approval": "maybe"},
        {"approval": "required", "risk": " "},
        {"approval": "required", "approval_timeout": "soon"},
        {"approval": "required", "approval_timeout": 0},
    ],
)
def test_bad_decorator_arguments_fail_at_decoration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        Donkey.tool(**kwargs)


def test_a_gated_tool_without_a_docstring_is_still_rejected() -> None:
    with pytest.raises(ValueError, match="requires a docstring"):

        @Donkey.tool(approval="required")
        def issue_refund() -> None:
            return None

    assert registered_tools() == ()


# --- ApprovalRequired -------------------------------------------------------
def test_a_gated_call_does_not_run_and_carries_the_call() -> None:
    ran: list[str] = []

    @Donkey.tool(approval="required", risk="financial")
    def issue_refund(ticket_id: str, amount: float, currency: str = "EUR") -> str:
        """Refund a ticket."""
        ran.append(ticket_id)
        return "refunded"

    with Donkey(_cfg()) as donkey, donkey.run(id="run-7"):
        err = _requested(lambda: issue_refund("T-1", amount=140.0))
    assert ran == []
    assert err.tool == "issue_refund"
    assert dict(err.arguments) == {"ticket_id": "T-1", "amount": 140.0, "currency": "EUR"}
    assert err.risk == "financial"
    assert err.correlation_id == err.approval.correlation_id == "run-7"
    assert isinstance(err, DonkeyError)
    assert not isinstance(err, PolicyViolation)
    assert err.remediation.strip()
    # The arguments are request content: they never reach the message.
    assert "T-1" not in str(err)
    assert donkey.approvals.pending() == (err.approval,)


def test_outside_a_run_the_approval_gets_its_own_correlation_id(donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    first = _requested(lambda: issue_refund("T-1"))
    second = _requested(lambda: issue_refund("T-2"))
    assert first.correlation_id
    assert first.correlation_id != second.correlation_id
    assert first.approval.id != second.approval.id


def test_a_call_that_could_never_run_fails_now_and_requests_nothing(donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    with pytest.raises(TypeError):
        issue_refund("T-1", "extra")
    assert donkey.approvals.pending() == ()


async def test_an_async_gated_call_raises_when_awaited(donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    async def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        return ticket_id

    with pytest.raises(ApprovalRequired):
        await issue_refund("T-1")


def test_the_typed_refusal_bridge_lets_approval_required_through() -> None:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    with Donkey(_cfg()) as donkey, pytest.raises(ApprovalRequired), donkey.run():
        issue_refund("T-1")


# --- resolve / reject -------------------------------------------------------
def test_resolve_runs_the_call_under_the_approvals_correlation_id() -> None:
    seen: list[tuple[str, float, str | None]] = []

    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str, amount: float) -> str:
        """Refund a ticket."""
        seen.append((ticket_id, amount, current_correlation_id()))
        return "refunded"

    with Donkey(_cfg()) as donkey:
        with donkey.run(id="run-7"):
            err = _requested(lambda: issue_refund("T-1", 140.0))
        # The decision arrives later, outside the run that asked.
        assert donkey.approvals.resolve(err.approval.id, approved_by="m.keller") == "refunded"
        assert seen == [("T-1", 140.0, "run-7")]
        assert donkey.approvals.pending() == ()
        with pytest.raises(KeyError, match="no pending approval"):
            donkey.approvals.resolve(err.approval.id, approved_by="m.keller")


async def test_resolve_of_an_async_tool_is_awaited(donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    async def issue_refund(ticket_id: str) -> str:
        """Refund a ticket."""
        return f"refunded {ticket_id} in {current_correlation_id()}"

    try:
        await issue_refund("T-1")
    except ApprovalRequired as err:
        approval = err.approval
    result = await donkey.approvals.resolve(approval.id, approved_by="m.keller")
    assert result == f"refunded T-1 in {approval.correlation_id}"


def test_reject_drops_the_call_without_running_it(donkey: Donkey) -> None:
    ran: list[str] = []

    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""
        ran.append(ticket_id)

    err = _requested(lambda: issue_refund("T-1"))
    assert donkey.approvals.reject(err.approval.id, rejected_by="m.keller") == err.approval
    assert ran == []
    with pytest.raises(KeyError):
        donkey.approvals.get(err.approval.id)


@pytest.mark.parametrize("who", ["", "  "])
def test_a_decision_must_name_who_made_it(who: str, donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    err = _requested(lambda: issue_refund("T-1"))
    with pytest.raises(ValueError, match="approved_by"):
        donkey.approvals.resolve(err.approval.id, approved_by=who)
    with pytest.raises(ValueError, match="rejected_by"):
        donkey.approvals.reject(err.approval.id, rejected_by=who)
    # A rejected decision consumes nothing.
    assert donkey.approvals.get(err.approval.id) == err.approval


def test_the_approver_lands_in_the_log(caplog: pytest.LogCaptureFixture, donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    err = _requested(lambda: issue_refund("T-1"))
    with caplog.at_level(logging.INFO, logger="donkey_kit.core.approvals"):
        donkey.approvals.resolve(err.approval.id, approved_by="m.keller")
    (record,) = caplog.records
    assert "m.keller" in record.getMessage()
    assert err.approval.id in record.getMessage()
    assert "T-1" not in record.getMessage()


def test_an_approval_never_overrides_a_gateway_refusal() -> None:
    pytest.importorskip("openai")

    with Donkey(_cfg()) as donkey:

        @donkey.tool(approval="required")
        def summarise(ticket_id: str) -> None:
            """Summarise a ticket with the model."""
            llm = donkey.openai(sync=True)
            llm.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": ticket_id}]
            )

        err = _requested(lambda: summarise("T-1"))
        # donkey.run() is the typed-refusal bridge; the approval does not lift it.
        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as refused, donkey.run():
            donkey.approvals.resolve(err.approval.id, approved_by="m.keller")
    # The resumed call went to the gateway under the approval's run id.
    assert refused.value.correlation_id == err.approval.correlation_id


# --- timeout ----------------------------------------------------------------
def test_an_unresolved_approval_times_out_with_a_typed_error(clock: _Clock, donkey: Donkey) -> None:
    ran: list[str] = []

    @donkey.tool(approval="required", approval_timeout="30s")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""
        ran.append(ticket_id)

    err = _requested(lambda: issue_refund("T-1"))
    assert err.approval.expires_at == err.approval.requested_at + 30
    clock.now += 30
    assert err.approval.expired
    with pytest.raises(ApprovalTimedOut) as info:
        donkey.approvals.resolve(err.approval.id, approved_by="m.keller")
    assert info.value.approval == err.approval
    assert info.value.correlation_id == err.approval.correlation_id
    assert ran == []
    with pytest.raises(KeyError):
        donkey.approvals.get(err.approval.id)


def test_pending_drops_expired_approvals(clock: _Clock, donkey: Donkey) -> None:
    @donkey.tool(approval="required", approval_timeout=timedelta(minutes=1))
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    old = _requested(lambda: issue_refund("T-1")).approval
    clock.now += 45
    new = _requested(lambda: issue_refund("T-2")).approval
    assert donkey.approvals.pending() == (old, new)
    clock.now += 30
    assert donkey.approvals.pending() == (new,)


def test_the_default_timeout_is_fifteen_minutes(donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    approval = _requested(lambda: issue_refund("T-1")).approval
    assert approval.expires_at - approval.requested_at == pytest.approx(15 * 60)


# --- spans ------------------------------------------------------------------
def _events(exporter: Any) -> list[tuple[str, dict[str, Any]]]:
    return [
        (event.name, dict(event.attributes or {}))
        for span in exporter.get_finished_spans()
        for event in span.events
    ]


def test_a_pending_approval_is_a_span_event_without_the_arguments(
    spans: Any, donkey: Donkey
) -> None:
    @donkey.tool(approval="required", risk="financial")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    err = _requested(lambda: issue_refund("T-secret"))
    ((name, attrs),) = _events(spans)
    assert name == "donkey.approval.pending"
    assert attrs["gen_ai.tool.name"] == "issue_refund"
    assert attrs["donkey.approval.id"] == err.approval.id
    assert attrs["donkey.approval.risk"] == "financial"
    assert attrs["donkey.correlation_id"] == err.approval.correlation_id
    assert attrs["donkey.approval.expires_at"] == err.approval.expires_at
    assert "T-secret" not in repr(spans.get_finished_spans()[0].to_json())


def test_decisions_and_timeouts_are_span_events(spans: Any, clock: _Clock, donkey: Donkey) -> None:
    @donkey.tool(approval="required", approval_timeout=10)
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    approved = _requested(lambda: issue_refund("T-1")).approval
    rejected = _requested(lambda: issue_refund("T-2")).approval
    expired = _requested(lambda: issue_refund("T-3")).approval
    donkey.approvals.resolve(approved.id, approved_by="m.keller")
    donkey.approvals.reject(rejected.id, rejected_by="a.ops")
    clock.now += 10
    assert donkey.approvals.pending() == ()
    decisions = [(n, a) for n, a in _events(spans) if n != "donkey.approval.pending"]
    assert decisions == [
        (
            "donkey.approval.approved",
            {
                "gen_ai.tool.name": "issue_refund",
                "donkey.approval.id": approved.id,
                "donkey.correlation_id": approved.correlation_id,
                "donkey.approval.approved_by": "m.keller",
            },
        ),
        (
            "donkey.approval.rejected",
            {
                "gen_ai.tool.name": "issue_refund",
                "donkey.approval.id": rejected.id,
                "donkey.correlation_id": rejected.correlation_id,
                "donkey.approval.rejected_by": "a.ops",
            },
        ),
        (
            "donkey.approval.timed_out",
            {
                "gen_ai.tool.name": "issue_refund",
                "donkey.approval.id": expired.id,
                "donkey.correlation_id": expired.correlation_id,
            },
        ),
    ]


# --- which client records it (ADR 0018, #1423) -----------------------------
def test_each_donkey_owns_its_own_registry() -> None:
    with Donkey(_cfg()) as a, Donkey(_cfg()) as b:
        assert isinstance(a.approvals, Approvals)
        assert a.approvals is a.approvals
        assert a.approvals is not b.approvals


def test_a_call_in_a_clients_run_records_on_that_client() -> None:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    with Donkey(_cfg()) as a, Donkey(_cfg()) as b:
        with b.run():
            err = _requested(lambda: issue_refund("T-1"))
        assert b.approvals.pending() == (err.approval,)
        assert a.approvals.pending() == ()
        with pytest.raises(ApprovalNotFound) as info:
            a.approvals.resolve(err.approval.id, approved_by="m.keller")
        assert info.value.approval_id == err.approval.id
        assert b.approvals.get(err.approval.id) == err.approval


async def test_a_call_in_a_clients_as_user_block_records_on_that_client() -> None:
    @Donkey.tool(approval="required")
    async def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    with Donkey(_cfg()) as a, Donkey(_cfg()) as b:
        async with b.as_user(token="user-token"):
            with pytest.raises(ApprovalRequired) as info:
                await issue_refund("T-1")
        assert b.approvals.pending() == (info.value.approval,)
        assert a.approvals.pending() == ()


def test_the_running_scope_wins_over_the_declaring_client() -> None:
    with Donkey(_cfg()) as a, Donkey(_cfg()) as b:

        @a.tool(approval="required")
        def issue_refund(ticket_id: str) -> None:
            """Refund a ticket."""

        with b.run():
            err = _requested(lambda: issue_refund("T-1"))
        assert b.approvals.pending() == (err.approval,)
        assert a.approvals.pending() == ()
        # Outside every scope it falls back to the client it was declared on.
        later = _requested(lambda: issue_refund("T-2"))
        assert a.approvals.pending() == (later.approval,)


def test_a_bare_instance_decorator_declares_on_that_client(donkey: Donkey) -> None:
    @donkey.tool
    def lookup(ticket_id: str) -> str:
        """Look a ticket up."""
        return ticket_id

    assert lookup("T-1") == "T-1"
    # Declaring on an instance leaves nothing bound afterwards.
    assert current_approvals_client() is None


def test_a_class_declared_tool_outside_every_scope_raises_config_error() -> None:
    ran: list[str] = []

    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""
        ran.append(ticket_id)

    with pytest.raises(ConfigError, match="no Donkey owns this call") as info:
        issue_refund("T-1")
    assert "donkey.run()" in info.value.remediation
    assert ran == []


def test_an_ungated_class_declared_tool_still_runs_anywhere() -> None:
    @Donkey.tool
    def lookup(ticket_id: str) -> str:
        """Look a ticket up."""
        return ticket_id

    assert lookup("T-1") == "T-1"


def test_a_used_approval_is_not_found_with_a_readable_message(donkey: Donkey) -> None:
    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> None:
        """Refund a ticket."""

    err = _requested(lambda: issue_refund("T-1"))
    donkey.approvals.reject(err.approval.id, rejected_by="m.keller")
    with pytest.raises(ApprovalNotFound) as info:
        donkey.approvals.reject(err.approval.id, rejected_by="m.keller")
    assert isinstance(info.value, KeyError)
    assert isinstance(info.value, DonkeyError)
    # Not KeyError's quoted repr.
    assert str(info.value).startswith("no pending approval")
    assert info.value.remediation.strip()


def test_the_approval_errors_share_one_base() -> None:
    assert issubclass(ApprovalRejected, ApprovalError)
    assert issubclass(GatewayApprovalDenied, ApprovalError)
    assert issubclass(GatewayApprovalDenied, PolicyViolation)
    assert not issubclass(ApprovalRejected, PolicyViolation)
    assert not issubclass(ApprovalRequired, ApprovalError)
    assert not issubclass(ApprovalTimedOut, ApprovalError)


def test_approval_is_a_frozen_value_object() -> None:
    approval = Approval(
        id="a-1",
        tool="t",
        arguments={},
        risk=None,
        correlation_id="run-1",
        requested_at=0.0,
        expires_at=1.0,
    )
    with pytest.raises(AttributeError):
        approval.tool = "other"  # type: ignore[misc]


def test_native_defaults_to_none_and_is_left_out_of_equality_and_the_repr() -> None:
    approval = Approval(
        id="a-1",
        tool="t",
        arguments={},
        risk=None,
        correlation_id="run-1",
        requested_at=0.0,
        expires_at=1.0,
    )
    assert approval.native is None
    paused = dataclasses.replace(approval, native=SimpleNamespace(secret="resume-token"))
    assert paused == approval
    assert "native" not in repr(paused) and "resume-token" not in repr(paused)
    with pytest.raises(AttributeError):
        paused.native = None  # type: ignore[misc]
