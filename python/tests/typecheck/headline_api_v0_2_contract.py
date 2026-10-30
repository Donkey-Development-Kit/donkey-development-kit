"""Static contract for the 0.2.0 headline API under a downstream ``mypy --strict`` (#1445).

Never executed: mypy checks it (``files`` in ``pyproject.toml``). Each
``assert_type`` pins a public annotation as a user sees it. Each negative case is
a ``# type: ignore[<code>]`` on a call that must fail type checking; strict mode's
``warn_unused_ignores`` turns the ignore into an error the moment the call stops
failing, so a loosened annotation cannot pass silently.

The ``ToolSet`` bindings are pinned at their current ``list[Any]``, and
``Approvals.resolve()`` at its current ``Any``: tightening them can surface new
downstream mypy errors, so it is a deliberate change of its own (#1445), and
this file changes with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from typing_extensions import assert_type

from donkey_kit import (
    A2AResult,
    A2ATask,
    AgentSpec,
    Approval,
    ApprovalError,
    ApprovalNotFound,
    ApprovalRejected,
    ApprovalRequired,
    Approvals,
    ApprovalTimedOut,
    Classifier,
    Donkey,
    DonkeyError,
    GatewayApprovalDenied,
    LastCall,
    OnRefusal,
    PIIDetected,
    PolicyViolation,
    RunLedger,
    RunRefusal,
    TokenBudgetExceeded,
    ToolSet,
    UserScope,
    classify,
    registered_agents,
)
from donkey_kit.core import PolicyRegistration, RefusalContext, RunUsage
from donkey_kit.experimental import DescriptorMismatch

# --- @donkey.agent keeps the decorated callable's signature (BG §2.9, #217) -------


async def check_agent(donkey: Donkey, task: A2ATask) -> None:
    @donkey.agent(name="support-triage", skills=["triage", "draft-reply"])
    async def handle(task: A2ATask) -> str:
        """Triage a support ticket."""
        return task.input

    @Donkey.agent(name="billing")
    def handle_sync(task: A2ATask, *, verbose: bool = False) -> A2AResult:
        """Answer a billing question."""
        return A2AResult(text=task.input)

    assert_type(await handle(task), str)
    assert_type(handle_sync(task, verbose=True), A2AResult)
    assert_type(registered_agents(), tuple[AgentSpec, ...])
    assert_type(donkey.serve(handle), None)

    await handle("ticket")  # type: ignore[arg-type]
    handle_sync(task, verbos=True)  # type: ignore[call-arg]
    donkey.agent("support-triage")  # type: ignore[call-arg]
    donkey.agent(skills=["triage"])  # type: ignore[call-arg]


# --- donkey.on(X): the four reaction verbs (BG §2.1, #208) ------------------------


def mask_and_retry(ctx: RefusalContext) -> None:
    assert_type(ctx.violation, PolicyViolation)
    assert_type(ctx.body, dict[str, Any] | None)
    ctx.resend(json={"model": "gpt-4o-mini", "messages": []})


async def mask_and_retry_async(ctx: RefusalContext) -> None:
    ctx.resend(json={"messages": []})


def needs_a_violation(violation: PolicyViolation) -> None:
    del violation


def check_on(donkey: Donkey, queue: asyncio.Queue[PolicyViolation]) -> None:
    assert_type(donkey.on(PIIDetected), OnRefusal)
    # Every verb registers and returns nothing to chain on.
    assert_type(donkey.on(PIIDetected).call(mask_and_retry, max_times=2), None)
    assert_type(donkey.on(PIIDetected).call(mask_and_retry_async), None)
    assert_type(donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait="45m"), None)
    donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait=timedelta(minutes=5), max_times=2)
    donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait=30.0)
    assert_type(donkey.on(PolicyViolation).fallback(model="internal-safe-model"), None)
    assert_type(donkey.on(PolicyViolation).escalate(to=queue), None)
    donkey.on(PolicyViolation).escalate(to=needs_a_violation)

    donkey.on(ValueError)  # type: ignore[arg-type]
    donkey.on(PIIDetected).call(needs_a_violation)  # type: ignore[arg-type]
    donkey.on(PIIDetected).call(mask_and_retry, max_times="1")  # type: ignore[arg-type]
    donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait=[45])  # type: ignore[arg-type]
    donkey.on(PolicyViolation).fallback()  # type: ignore[call-arg]
    donkey.on(PolicyViolation).escalate(to=object())  # type: ignore[arg-type]
    # No verb un-refuses (#209): there is no ignore() to call.
    donkey.on(PIIDetected).ignore()  # type: ignore[attr-defined]


# --- classify.register: a custom policy's refusal, typed (BG §2.2, #210) ----------


class SalaryLeakBlocked(PolicyViolation):
    remediation = "Salary figures may not leave HR. Ask the user to open the HR portal."


def check_classify(donkey: Donkey) -> None:
    assert_type(classify, Classifier)
    assert_type(donkey.classify, Classifier)

    registration = classify.register(
        match={"status": 403, "error_type": "acme_salary_block"},
        raises=SalaryLeakBlocked,
    )
    assert_type(registration, PolicyRegistration)
    assert_type(registration.raises, type[PolicyViolation])
    assert_type(registration.policy, str)
    assert_type(donkey.classify.registered(), tuple[PolicyRegistration, ...])
    assert_type(donkey.classify.unregister(SalaryLeakBlocked), tuple[PolicyRegistration, ...])

    classify.register(match={"status": 403}, raises=ValueError)  # type: ignore[arg-type]
    classify.register(match={"status": 403})  # type: ignore[call-arg]
    classify.register({"status": 403}, SalaryLeakBlocked)  # type: ignore[call-arg]
    classify.unregister(ValueError)  # type: ignore[arg-type]


# --- donkey.as_user(token=...): async and sync with (BG §2.4, #215, #1436) --------


async def check_as_user_async(donkey: Donkey, token: str) -> None:
    assert_type(donkey.as_user(token=token), UserScope)
    async with donkey.as_user(token=token):
        await donkey.openai().models.list()


def check_as_user_sync(donkey: Donkey, token: str) -> None:
    # The same object binds the user for blocking code (#1472).
    with donkey.as_user(token=token):
        donkey.openai(sync=True).models.list()

    donkey.as_user(token)  # type: ignore[call-arg]
    donkey.as_user()  # type: ignore[call-arg]
    donkey.as_user(token=None)  # type: ignore[arg-type]


# --- donkey.current_run: the run ledger (#613) ------------------------------------


async def check_current_run(donkey: Donkey) -> None:
    async with donkey.run(id="ticket-1"):
        ledger = donkey.current_run
        assert_type(ledger, RunLedger | None)
        if ledger is None:
            return
        assert_type(ledger.run_id, str)
        assert_type(ledger.available, bool)
        assert_type(ledger.calls, tuple[LastCall, ...])
        assert_type(ledger.refusals, tuple[RunRefusal, ...])
        assert_type(ledger.call_count, int)
        assert_type(ledger.usage, RunUsage)
        assert_type(ledger.usage.total_tokens, int)
        assert_type(any(r.is_a(PIIDetected) for r in ledger.refusals), bool)
        assert_type(ledger.refusals[0].kind, type[DonkeyError])
        assert_type(ledger.refusals[0].policy, str | None)

        ledger.run_id = "other"  # type: ignore[misc]
        donkey.current_run = ledger  # type: ignore[misc]
        ledger.refusals[0].is_a(ValueError)  # type: ignore[arg-type]


# --- ToolSet bindings: today's return types (docs/verified-apis.md §9) ------------


async def check_toolset_bindings(donkey: Donkey) -> None:
    tools = await donkey.tools.discover()
    assert_type(tools, ToolSet)
    assert_type(tools.filter(allow=["get_*"], deny=["get_ssn"]), ToolSet)

    assert_type(await tools.langgraph(), list[Any])
    assert_type(await tools.llamaindex(), list[Any])
    assert_type(await tools.adk(), list[Any])
    assert_type(await tools.strands(), list[Any])
    assert_type(await tools.openai_agents(), list[Any])
    assert_type(await tools.agent_framework(), list[Any])
    # These two hold an MCP connection open, so they are async context managers.
    async with tools.anthropic() as anthropic_tools:
        assert_type(anthropic_tools, list[Any])
    async with tools.crewai() as crewai_tools:
        assert_type(crewai_tools, list[Any])

    # Renamed to openai_agents() (#1468); the old name is gone.
    await tools.openai()  # type: ignore[attr-defined]
    tools.filter(allow="get_*")  # type: ignore[arg-type]


# --- @donkey.tool keeps the decorated signature, gated or not (BG §2.3, #1423) ----


async def check_tool(donkey: Donkey) -> None:
    # Read from an instance it declares the gated tool on that client; on the
    # class it names none (ADR 0018). Both forms type the same.
    @donkey.tool(approval="required", risk="financial", approval_timeout="15m")
    async def issue_refund(ticket_id: str, amount: float) -> str:
        """Refund a ticket."""
        return ticket_id

    @Donkey.tool(approval="required", approval_timeout=timedelta(minutes=5))
    def close_ticket(ticket_id: str, *, note: str = "") -> bool:
        """Close a ticket."""
        return bool(note)

    @donkey.tool
    async def lookup_crm(customer_id: str) -> dict[str, str]:
        """Look up a customer."""
        return {"id": customer_id}

    @Donkey.tool(server="sku-manager")
    def lookup_sku(sku: str) -> int:
        """Look up a SKU."""
        return len(sku)

    assert_type(await issue_refund("T-1", 140.0), str)
    assert_type(close_ticket("T-1", note="done"), bool)
    assert_type(await lookup_crm("C-1"), dict[str, str])
    assert_type(lookup_sku("S-1"), int)

    await issue_refund("T-1")  # type: ignore[call-arg]
    close_ticket(1)  # type: ignore[arg-type]
    close_ticket("T-1", note=1)  # type: ignore[arg-type]
    donkey.tool(approval="optional")  # type: ignore[call-overload]
    Donkey.tool(risk=1)  # type: ignore[call-overload]
    donkey.tool(approval_timeout=[15])  # type: ignore[call-overload]
    donkey.tool(lookup_sku, approval="required")  # type: ignore[call-overload]


# --- donkey.approvals: this client's registry (BG §2.3, ADR 0018) -----------------


def check_approvals(donkey: Donkey, err: ApprovalRequired, tools: ToolSet) -> None:
    assert_type(donkey.approvals, Approvals)
    assert_type(Approvals(), Approvals)
    assert_type(donkey.approvals.pending(), tuple[Approval, ...])

    approval = err.approval
    assert_type(approval, Approval)
    assert_type(err.tool, str)
    assert_type(err.arguments, Mapping[str, Any])
    assert_type(err.risk, str | None)
    assert_type(approval.id, str)
    assert_type(approval.arguments, Mapping[str, Any])
    assert_type(approval.correlation_id, str)
    assert_type(approval.expires_at, float)
    assert_type(approval.expired, bool)
    # The framework's own pause object (#1422): open by design.
    assert_type(approval.native, Any)

    assert_type(donkey.approvals.get(approval.id), Approval)
    # The tool's own return value, or an awaitable for an async tool.
    assert_type(donkey.approvals.resolve(approval.id, approved_by="m.keller"), Any)
    assert_type(donkey.approvals.reject(approval.id, rejected_by="m.keller"), Approval)
    assert_type(tools.require_approval(tools=["issue_refund"], risk="financial"), ToolSet)

    donkey.approvals.resolve(approval.id)  # type: ignore[call-arg]
    donkey.approvals.resolve(approval.id, "m.keller")  # type: ignore[call-arg]
    donkey.approvals.reject(approval.id, approved_by="m.keller")  # type: ignore[call-arg]
    donkey.approvals.get(1)  # type: ignore[arg-type]
    donkey.approvals = Approvals()  # type: ignore[misc]
    approval.id = "other"  # type: ignore[misc]
    tools.require_approval(["issue_refund"])  # type: ignore[call-arg]


# --- the approval errors: who refused, and what else they are (#1423) -------------


def takes_donkey_error(err: DonkeyError) -> None: ...
def takes_approval_error(err: ApprovalError) -> None: ...
def takes_policy_violation(err: PolicyViolation) -> None: ...
def takes_key_error(err: KeyError) -> None: ...
def takes_value_error(err: ValueError) -> None: ...


def check_approval_errors(
    rejected: ApprovalRejected,
    denied: GatewayApprovalDenied,
    not_found: ApprovalNotFound,
    timed_out: ApprovalTimedOut,
    required: ApprovalRequired,
    mismatch: DescriptorMismatch,
) -> None:
    # A refusal, by a human or by the gateway's step-up, is an ApprovalError.
    takes_approval_error(rejected)
    takes_approval_error(denied)
    takes_policy_violation(denied)
    assert_type(rejected.approval, Approval)
    assert_type(denied.policy, str)

    # An unknown id is still the KeyError it used to be.
    takes_donkey_error(not_found)
    takes_key_error(not_found)
    assert_type(not_found.approval_id, str | None)
    assert_type(not_found.remediation, str)

    takes_donkey_error(mismatch)
    takes_value_error(mismatch)
    assert_type(mismatch.remediation, str)

    assert_type(timed_out.approval, Approval)
    assert_type(required.approval, Approval)

    # Waiting, expiry and an unknown id are not refusals; a human's no is not a policy.
    takes_approval_error(not_found)  # type: ignore[arg-type]
    takes_approval_error(timed_out)  # type: ignore[arg-type]
    takes_approval_error(required)  # type: ignore[arg-type]
    takes_policy_violation(rejected)  # type: ignore[arg-type]
    takes_key_error(rejected)  # type: ignore[arg-type]
