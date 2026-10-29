"""``@donkey.tool(approval="required")`` under the OpenAI Agents SDK (BG §2.3, #213).

Built with ``openai_agents.function_tool``, a gated tool sets the SDK's own
``needs_approval``, so ``Runner.run`` stops before the tool runs and returns the
call in ``result.interruptions``. ``pending_approvals`` records the pause,
``approve`` / ``reject`` record the decision and hand it to ``RunState``, and
``Runner.run(agent, state)`` resumes. The pause lives in the run state, so it
survives ``to_string()`` / ``from_string()``, and a refusal the resumed tool's
model call gets is raised typed instead of becoming a tool result. The resumed
tool runs as the user the paused call was recorded for (#1416).
"""

from __future__ import annotations

import copy
import dataclasses
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from donkey_kit import (
    ApprovalRequired,
    Approvals,
    AuthError,
    Donkey,
    DonkeyConfig,
    PIIDetected,
)
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import approvals as approvals_mod
from donkey_kit.core import telemetry
from donkey_kit.core.identity import current_user
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.core.toolspec import tool_spec

agents = pytest.importorskip("agents")

from donkey_kit.integrations import openai_agents as ddk_agents  # noqa: E402

_RUN = agents.RunConfig(tracing_disabled=True)


class _ResponsesModel(agents.Model):
    """A scripted Responses model: namespace calls never pass through Chat Completions."""

    def __init__(self, calls: list[tuple[str, str | None]]) -> None:
        self.calls = calls
        self.inputs: list[Any] = []

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        from openai.types.responses import (
            ResponseFunctionToolCall,
            ResponseOutputMessage,
            ResponseOutputText,
        )

        self.inputs.append(kwargs.get("input", args[1] if len(args) > 1 else None))
        if len(self.inputs) == 1:
            output = [
                ResponseFunctionToolCall(
                    type="function_call",
                    name=name,
                    namespace=namespace,
                    call_id=f"call-{index}",
                    arguments='{"ticket_id":"T-1"}',
                )
                for index, (name, namespace) in enumerate(self.calls)
            ]
        else:
            output = [
                ResponseOutputMessage(
                    id="message-1",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[ResponseOutputText(type="output_text", text="done", annotations=[])],
                )
            ]
        return agents.ModelResponse(output=output, usage=agents.Usage(), response_id=None)

    async def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise NotImplementedError
        yield


@pytest.mark.parametrize("namespace", ["billing", "issue_refund"])
@pytest.mark.parametrize("restore", [False, True], ids=["same-state", "restored-state"])
async def test_responses_approval_resumes_namespaced_and_deferred_tools(
    namespace: str, restore: bool
) -> None:
    ran: list[str] = []

    @Donkey.tool(approval="required", risk="financial")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        ran.append(ticket_id)
        return "refunded"

    deferred = namespace == "issue_refund"
    tool = ddk_agents.function_tool(issue_refund, defer_loading=deferred)
    if not deferred:
        (tool,) = agents.tool_namespace(name=namespace, description="Billing tools.", tools=[tool])
    model = _ResponsesModel([("issue_refund", namespace)])
    agent = agents.Agent(name="refunds", model=model, tools=[tool])
    result = await agents.Runner.run(agent, "refund", run_config=_RUN)
    assert ran == []
    (approval,) = ddk_agents.pending_approvals(result)
    assert approval.risk == "financial"
    state = result.to_state()
    if restore:
        state = await agents.RunState.from_string(agent, state.to_string())
        assert ddk_agents.pending_approvals(state)[0].id == approval.id
    ddk_agents.approve(state, approval.id, by="reviewer")
    done = await agents.Runner.run(agent, state, run_config=_RUN)
    assert done.final_output == "done"
    assert ran == ["T-1"]
    with pytest.raises(KeyError, match="already decided"):
        ddk_agents.approve(state, approval, by="another-reviewer")


@pytest.mark.parametrize("deferred", [False, True], ids=["namespace", "deferred"])
async def test_responses_same_name_tools_resolve_their_own_specs(deferred: bool) -> None:
    ran: list[str] = []

    @Donkey.tool(approval="required", risk="billing")
    def bill(ticket_id: str, amount: int = 100) -> str:
        """Bill a ticket."""
        ran.append("billing")
        return ticket_id

    @Donkey.tool(approval="required", risk="shipping")
    def ship(ticket_id: str, address: str = "warehouse") -> str:
        """Ship a ticket."""
        ran.append("shipping")
        return ticket_id

    billing = ddk_agents.function_tool(bill, name_override="process")
    shipping = ddk_agents.function_tool(ship, name_override="process", defer_loading=deferred)
    if deferred:
        tools = [billing, shipping]
        namespaces = [None, "process"]
    else:
        tools = [
            *agents.tool_namespace(name="billing", description="Billing.", tools=[billing]),
            *agents.tool_namespace(name="shipping", description="Shipping.", tools=[shipping]),
        ]
        namespaces = ["billing", "shipping"]
    model = _ResponsesModel([("process", namespace) for namespace in namespaces])
    agent = agents.Agent(name="orders", model=model, tools=tools)
    paused = await agents.Runner.run(agent, "process", run_config=_RUN)
    assert ran == []
    first, second = ddk_agents.pending_approvals(paused)
    assert (first.tool, first.risk, dict(first.arguments)) == (
        "bill",
        "billing",
        {"ticket_id": "T-1", "amount": 100},
    )
    assert (second.tool, second.risk, dict(second.arguments)) == (
        "ship",
        "shipping",
        {"ticket_id": "T-1", "address": "warehouse"},
    )
    assert first.id != second.id
    state = await agents.RunState.from_string(agent, paused.to_state().to_string())
    ddk_agents.reject(state, first, by="reviewer", message="Do not bill.")
    ddk_agents.approve(state, second, by="reviewer")
    done = await agents.Runner.run(agent, state, run_config=_RUN)
    assert done.final_output == "done"
    assert ran == ["shipping"]
    outputs = [item for item in model.inputs[-1] if item.get("type") == "function_call_output"]
    assert {item["call_id"]: item["output"] for item in outputs} == {
        "call-0": "Do not bill.",
        "call-1": "T-1",
    }


@pytest.mark.parametrize("namespace", ["billing", "issue_refund"])
async def test_responses_unapproved_namespaced_and_deferred_tools_stay_gated(
    namespace: str,
) -> None:
    ran: list[str] = []

    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        ran.append(ticket_id)
        return ticket_id

    tool = ddk_agents.function_tool(issue_refund, defer_loading=namespace == "issue_refund")
    if namespace == "billing":
        (tool,) = agents.tool_namespace(name=namespace, description="Billing.", tools=[tool])
    tool.needs_approval = False
    model = _ResponsesModel([("issue_refund", namespace)])
    agent = agents.Agent(name="refunds", model=model, tools=[tool])
    async with Donkey(_cfg()) as donkey:
        with pytest.raises(ApprovalRequired):
            async with donkey.run():
                await agents.Runner.run(agent, "refund", run_config=_RUN)
    assert ran == []
    assert len(model.inputs) == 1


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


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
        if span.name == "donkey.tool.approval"
        for event in span.events
    ]


def _gateway(sent: list[dict[str, Any]], arguments: dict[str, Any]) -> httpx.MockTransport:
    """A gateway whose model asks for ``issue_refund(**arguments)`` once, then
    answers ``done``."""

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if len(sent) == 1:
            call = {"name": "issue_refund", "arguments": json.dumps(arguments)}
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


def _tool_results(request: dict[str, Any]) -> list[str]:
    return [m["content"] for m in request["messages"] if m["role"] == "tool"]


class _Refunds:
    """A Donkey on a scripted gateway, with a gated refund tool on an agent."""

    def __init__(
        self,
        donkey: Donkey,
        arguments: dict[str, Any] | None = None,
        tool: Any = None,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.ran: list[tuple[str, float, str | None]] = []
        for client in http_clients(donkey):
            swap_transport(client, _gateway(self.sent, arguments or {"ticket_id": "T-1"}))
        ran = self.ran

        @donkey.tool(approval="required", risk="financial")
        async def issue_refund(ticket_id: str, amount: float = 140.0) -> str:
            """Refund a support ticket."""
            ran.append((ticket_id, amount, current_correlation_id()))
            return f"refunded {ticket_id}"

        self.refund = issue_refund
        self.tool = ddk_agents.function_tool(issue_refund)
        if tool is not None:  # build the agent's tool from the DDK one
            self.tool = tool(self.tool)
        self.agent = agents.Agent(
            name="refunds", model=donkey.openai_agents.model("gpt-4o"), tools=[self.tool]
        )

    async def pause(self) -> Any:
        return await agents.Runner.run(self.agent, "refund T-1", run_config=_RUN)

    async def resume(self, state: Any) -> Any:
        return await agents.Runner.run(self.agent, state, run_config=_RUN)


# --- the pause ---------------------------------------------------------------
async def test_a_gated_tool_pauses_the_run_with_the_sdks_own_approval(spans: Any) -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        async with donkey.run(id="run-9"):
            result = await refunds.pause()
            (approval,) = ddk_agents.pending_approvals(result)

    assert refunds.tool.needs_approval is True
    assert refunds.ran == []
    (item,) = result.interruptions
    assert item.name == "issue_refund"
    assert approval.tool == "issue_refund"
    # The approver sees the whole call, the tool's defaults included.
    assert dict(approval.arguments) == {"ticket_id": "T-1", "amount": 140.0}
    assert (approval.risk, approval.correlation_id) == ("financial", "run-9")
    # The run state holds the pause; the in-memory registry is not used.
    assert Approvals().pending() == ()
    ((name, attrs),) = _events(spans)
    assert name == "donkey.approval.pending"
    assert attrs["donkey.approval.id"] == approval.id
    assert attrs["donkey.correlation_id"] == "run-9"
    assert "donkey.approval.expires_at" not in attrs
    assert "T-1" not in repr(spans.get_finished_spans()[0].to_json())


async def test_approve_records_the_approver_and_resumes_the_tool_once(spans: Any) -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        async with donkey.run(id="run-9"):
            result = await refunds.pause()
            (approval,) = ddk_agents.pending_approvals(result)
        state = result.to_state()
        async with donkey.run(id=approval.correlation_id):
            decided = ddk_agents.approve(state, approval, by=" m.keller ")
            done = await refunds.resume(state)

    assert done.final_output == "done"
    assert refunds.ran == [("T-1", 140.0, "run-9")]
    assert _tool_results(refunds.sent[-1]) == ["refunded T-1"]
    assert decided.id == approval.id
    assert [(n, a["donkey.approval.id"]) for n, a in _events(spans)] == [
        ("donkey.approval.pending", approval.id),
        ("donkey.approval.approved", approval.id),
    ]
    approved = _events(spans)[1][1]
    assert approved["donkey.approval.approved_by"] == "m.keller"
    assert approved["donkey.correlation_id"] == "run-9"


async def test_the_pause_survives_a_serialised_run_state(spans: Any) -> None:
    """Only the serialised run state carries the pause: the approval registry
    is cleared, and the decision is taken on a state rebuilt from the string."""
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        result = await refunds.pause()
        (approval,) = ddk_agents.pending_approvals(result)
        stored = result.to_state().to_string()
        approvals_mod._reset_for_tests()

        state = await agents.RunState.from_string(refunds.agent, stored)
        ddk_agents.approve(state, approval.id, by="m.keller")
        done = await refunds.resume(state)

    assert done.final_output == "done"
    assert [r[:2] for r in refunds.ran] == [("T-1", 140.0)]
    pending, approved = _events(spans)
    # The id is derived from the call id, the tool and its arguments.
    assert pending[1]["donkey.approval.id"] == approved[1]["donkey.approval.id"]
    # Outside a run, the pause, the decision and the tool share the approval id.
    assert refunds.ran[0][2] == approval.correlation_id == approval.id


async def test_a_sync_gated_tool_pauses_and_resumes() -> None:
    ran: list[str] = []
    async with Donkey(_cfg()) as donkey:
        sent: list[dict[str, Any]] = []
        for client in http_clients(donkey):
            swap_transport(client, _gateway(sent, {"ticket_id": "T-2"}))

        @donkey.tool(approval="required")
        def issue_refund(ticket_id: str) -> str:
            """Refund a support ticket."""
            ran.append(ticket_id)
            return f"refunded {ticket_id}"

        agent = agents.Agent(
            name="refunds",
            model=donkey.openai_agents.model("gpt-4o"),
            tools=[ddk_agents.function_tool(issue_refund)],
        )
        result = await agents.Runner.run(agent, "refund", run_config=_RUN)
        (approval,) = ddk_agents.pending_approvals(result.to_state())
        state = result.to_state()
        ddk_agents.approve(state, approval, by="m.keller")
        await agents.Runner.run(agent, state, run_config=_RUN)

    assert ran == ["T-2"]
    assert _tool_results(sent[-1]) == ["refunded T-2"]


async def _paused_as_alice(donkey: Donkey, seen: list[str | None]) -> tuple[Any, Any, Any]:
    """A run paused on a gated tool inside ``as_user(alice)``: its agent, state
    and approval. The tool records the user token it runs as."""
    for client in http_clients(donkey):
        swap_transport(client, _gateway([], {"ticket_id": "T-1"}))

    @donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        user = current_user()
        seen.append(user.token if user else None)
        return f"refunded {ticket_id}"

    agent = agents.Agent(
        name="refunds",
        model=donkey.openai_agents.model("gpt-4o"),
        tools=[ddk_agents.function_tool(issue_refund)],
    )
    async with donkey.as_user(token="token-alice"):
        result = await agents.Runner.run(agent, "refund", run_config=_RUN)
        (approval,) = ddk_agents.pending_approvals(result)
    return agent, result.to_state(), approval


async def test_the_resumed_tool_runs_as_the_user_who_paused_the_run() -> None:
    seen: list[str | None] = []
    async with Donkey(_cfg()) as donkey:
        agent, state, approval = await _paused_as_alice(donkey, seen)
        # The approver's handler resumes the run, outside as_user().
        ddk_agents.approve(state, approval, by="m.keller")
        await agents.Runner.run(agent, state, run_config=_RUN)

    assert seen == ["token-alice"]


async def test_resuming_the_run_as_another_user_is_refused() -> None:
    seen: list[str | None] = []
    async with Donkey(_cfg()) as donkey:
        agent, state, approval = await _paused_as_alice(donkey, seen)
        ddk_agents.approve(state, approval, by="m.keller")
        async with donkey.as_user(token="token-bob"):
            with pytest.raises(AuthError, match="as_user"):
                async with donkey.run():
                    await agents.Runner.run(agent, state, run_config=_RUN)

    assert seen == []


# --- rejection -----------------------------------------------------------------
async def test_reject_records_the_rejecter_and_tells_the_model(spans: Any) -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        result = await refunds.pause()
        (approval,) = ddk_agents.pending_approvals(result)
        state = result.to_state()
        rejected = ddk_agents.reject(state, approval, by="a.ops")
        done = await refunds.resume(state)

    assert done.final_output == "done"
    assert refunds.ran == []
    assert rejected.id == approval.id
    (told,) = _tool_results(refunds.sent[-1])
    assert "rejected" in told and "issue_refund" in told
    assert "a.ops" not in told  # the approver's name never goes to the model
    name, attrs = _events(spans)[-1]
    assert (name, attrs["donkey.approval.rejected_by"]) == ("donkey.approval.rejected", "a.ops")


async def test_reject_sends_the_callers_message() -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        result = await refunds.pause()
        (approval,) = ddk_agents.pending_approvals(result)
        state = result.to_state()
        ddk_agents.reject(state, approval, by="a.ops", message="Refunds over 100 need a manager.")
        await refunds.resume(state)

    assert _tool_results(refunds.sent[-1]) == ["Refunds over 100 need a manager."]


# --- bad decisions ---------------------------------------------------------------
@pytest.mark.parametrize("decide", [ddk_agents.approve, ddk_agents.reject])
async def test_a_blank_decider_is_a_value_error(decide: Any) -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        result = await refunds.pause()
        (approval,) = ddk_agents.pending_approvals(result)
        with pytest.raises(ValueError, match="by must name who decided"):
            decide(result.to_state(), approval, by=" ")


async def test_an_unknown_approval_is_a_key_error() -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        result = await refunds.pause()
        with pytest.raises(KeyError, match="no paused call"):
            ddk_agents.approve(result.to_state(), "not-an-approval", by="m.keller")


# --- one decision per paused call ------------------------------------------------
async def test_a_second_approval_of_the_call_is_a_key_error(spans: Any) -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        result = await refunds.pause()
        (approval,) = ddk_agents.pending_approvals(result)
        state = result.to_state()
        ddk_agents.approve(state, approval, by="alice")
        # The SDK still lists the decided call in state.get_interruptions().
        assert len(state.get_interruptions()) == 1
        with pytest.raises(KeyError, match="already decided"):
            ddk_agents.approve(state, approval, by="bob")
        await refunds.resume(state)

    assert [r[:2] for r in refunds.ran] == [("T-1", 140.0)]
    decided = [a.get("donkey.approval.approved_by") for n, a in _events(spans)[1:]]
    assert decided == ["alice"]


async def test_a_rejection_after_the_approval_is_a_key_error(spans: Any) -> None:
    """approve(by=alice) then reject(by=bob) would leave two audit events that
    contradict each other, while the SDK runs the tool: the reject raises."""
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        result = await refunds.pause()
        (approval,) = ddk_agents.pending_approvals(result)
        state = result.to_state()
        ddk_agents.approve(state, approval, by="alice")
        with pytest.raises(KeyError, match="already decided"):
            ddk_agents.reject(state, approval.id, by="bob")
        await refunds.resume(state)

    assert [r[:2] for r in refunds.ran] == [("T-1", 140.0)]
    assert [n for n, _ in _events(spans)] == ["donkey.approval.pending", "donkey.approval.approved"]


async def test_a_decision_outside_the_run_keeps_the_pauses_correlation(spans: Any) -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        async with donkey.run(id="run-X"):
            result = await refunds.pause()
            (approval,) = ddk_agents.pending_approvals(result)
        decided = ddk_agents.approve(result.to_state(), approval, by="alice")

    assert decided.correlation_id == "run-X"
    assert decided.requested_at == approval.requested_at
    name, attrs = _events(spans)[-1]
    assert (name, attrs["donkey.correlation_id"]) == ("donkey.approval.approved", "run-X")


# --- a gated tool is still the SDK's FunctionTool ----------------------------------
def test_a_gated_tool_keeps_its_wrapped_function() -> None:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        return ticket_id

    tool = ddk_agents.function_tool(issue_refund)
    assert isinstance(tool, agents.FunctionTool)
    assert tool.__wrapped__ is issue_refund
    assert copy.copy(tool).__wrapped__ is issue_refund


@pytest.mark.parametrize(
    "rebuild",
    [
        copy.copy,
        lambda tool: dataclasses.replace(tool, description="Refund a ticket."),
        lambda tool: agents.tool_namespace(
            name="billing", description="Billing tools.", tools=[tool]
        )[0],
    ],
    ids=["copy", "replace", "tool_namespace"],
)
def test_a_rebuilt_gated_tool_keeps_the_gate(rebuild: Any) -> None:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        return ticket_id

    tool = ddk_agents.function_tool(issue_refund)
    rebuilt = rebuild(tool)
    assert rebuilt is not tool
    assert rebuilt.needs_approval is True
    # Bound to the rebuilt tool, not the original's invoker, and still the gate.
    assert rebuilt.on_invoke_tool is not tool.on_invoke_tool
    assert tool_spec(rebuilt.on_invoke_tool) is tool_spec(issue_refund)
    assert rebuilt.__wrapped__ is issue_refund


async def test_a_copied_gated_tool_pauses_and_runs_once_approved(spans: Any) -> None:
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey, tool=copy.copy)
        result = await refunds.pause()
        assert refunds.ran == []
        (approval,) = ddk_agents.pending_approvals(result)
        state = result.to_state()
        ddk_agents.approve(state, approval, by="alice")
        done = await refunds.resume(state)

    assert done.final_output == "done"
    assert [r[:2] for r in refunds.ran] == [("T-1", 140.0)]
    assert _tool_results(refunds.sent[-1]) == ["refunded T-1"]


async def test_a_copied_gated_tool_the_sdk_did_not_approve_raises() -> None:
    """The copy's invoker is the gate, so with the SDK's approval off the call
    still raises ``ApprovalRequired`` rather than running."""
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey, tool=copy.copy)
        refunds.tool.needs_approval = False
        with pytest.raises(ApprovalRequired):
            async with donkey.run(id="run-c"):
                await refunds.pause()

    assert refunds.ran == []


# --- where the native pause does not apply ------------------------------------
def test_outside_an_agents_tool_call_the_hook_declines() -> None:
    assert ddk_agents.approved_call("issue_refund", {}, None) is None


async def test_a_call_the_sdk_did_not_approve_still_raises_approval_required() -> None:
    """Switching the SDK's approval off does not open the gate: the tool raises
    ``ApprovalRequired`` out of the run, typed, instead of running."""
    async with Donkey(_cfg()) as donkey:
        refunds = _Refunds(donkey)
        refunds.tool.needs_approval = False
        with pytest.raises(ApprovalRequired) as info:
            async with donkey.run(id="run-x"):
                await refunds.pause()

    assert refunds.ran == []
    assert info.value.approval.tool == "issue_refund"
    assert len(refunds.sent) == 1  # the model was not told anything


async def test_pending_approvals_skips_interruptions_it_did_not_gate() -> None:
    async with Donkey(_cfg()) as donkey:
        sent: list[dict[str, Any]] = []
        for client in http_clients(donkey):
            swap_transport(client, _gateway(sent, {"ticket_id": "T-1"}))

        @agents.function_tool(needs_approval=True)
        def issue_refund(ticket_id: str) -> str:
            """Refund a support ticket."""
            return ticket_id

        agent = agents.Agent(
            name="refunds", model=donkey.openai_agents.model("gpt-4o"), tools=[issue_refund]
        )
        result = await agents.Runner.run(agent, "refund", run_config=_RUN)

    assert len(result.interruptions) == 1
    assert ddk_agents.pending_approvals(result) == ()


# --- function_tool -------------------------------------------------------------
def test_function_tool_takes_only_a_donkey_tool() -> None:
    def plain(ticket_id: str) -> str:
        """Not a donkey tool."""
        return ticket_id

    with pytest.raises(TypeError, match="takes a @donkey.tool"):
        ddk_agents.function_tool(plain)


def test_a_gated_tool_refuses_needs_approval() -> None:
    @Donkey.tool(approval="required")
    def issue_refund(ticket_id: str) -> str:
        """Refund a support ticket."""
        return ticket_id

    with pytest.raises(TypeError, match="drop needs_approval"):
        ddk_agents.function_tool(issue_refund, needs_approval=False)


async def test_an_ungated_tool_runs_and_its_errors_go_to_the_model() -> None:
    """A plain ``@donkey.tool`` runs at once, and an exception that is not a
    DDK error still becomes the SDK's own tool-result text."""
    from agents.tool import default_tool_error_function

    async with Donkey(_cfg()) as donkey:
        sent: list[dict[str, Any]] = []
        for client in http_clients(donkey):
            swap_transport(client, _gateway(sent, {"ticket_id": "T-1"}))

        @donkey.tool
        def issue_refund(ticket_id: str) -> str:
            """Refund a support ticket."""
            raise RuntimeError(f"ledger down for {ticket_id}")

        tool = ddk_agents.function_tool(issue_refund, name_override="issue_refund")
        agent = agents.Agent(
            name="refunds", model=donkey.openai_agents.model("gpt-4o"), tools=[tool]
        )
        result = await agents.Runner.run(agent, "refund", run_config=_RUN)

    assert tool.needs_approval is False
    assert result.final_output == "done"
    assert _tool_results(sent[-1]) == [
        default_tool_error_function(None, RuntimeError("ledger down for T-1"))
    ]


# --- a refusal and the pause compose -------------------------------------------
async def test_an_approved_tools_refusal_leaves_the_run_typed(spans: Any) -> None:
    """The approval does not lift a refusal, and the SDK does not turn it into a
    tool result: the resumed tool's model call is refused, and ``PIIDetected``
    leaves ``Runner.run`` under the decision's correlation id."""
    async with Donkey(_cfg()) as donkey:
        sent: list[dict[str, Any]] = []
        for client in http_clients(donkey):
            swap_transport(client, _gateway(sent, {"ticket_id": "T-1"}))
        llm = donkey.openai()

        @donkey.tool(approval="required")
        async def issue_refund(ticket_id: str) -> str:
            """Refund a ticket, after asking the model to word the reply."""
            await llm.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": ticket_id}]
            )
            return "refunded"

        agent = agents.Agent(
            name="refunds",
            model=donkey.openai_agents.model("gpt-4o"),
            tools=[ddk_agents.function_tool(issue_refund)],
        )
        async with donkey.run(id="run-r"):
            result = await agents.Runner.run(agent, "refund", run_config=_RUN)
            (approval,) = ddk_agents.pending_approvals(result)
        state = result.to_state()
        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as refused:
            async with donkey.run(id="run-r"):
                ddk_agents.approve(state, approval, by="m.keller")
                await agents.Runner.run(agent, state, run_config=_RUN)

    assert refused.value.correlation_id == "run-r"
    assert "john.doe" not in str(refused.value)
    assert [n for n, _ in _events(spans)] == ["donkey.approval.pending", "donkey.approval.approved"]


async def test_the_translator_types_a_wrapped_refusal_and_ignores_the_rest() -> None:
    from agents.exceptions import UserError

    assert ddk_agents.refusal_translator(UserError("plain")) is None
    try:
        raise UserError("Error running tool x") from ValueError("not a refusal")
    except UserError as wrapped:
        assert ddk_agents.refusal_translator(wrapped) is None
    try:
        raise UserError("Error running tool x") from ApprovalRequired(
            "needs approval", approval=_approval()
        )
    except UserError as wrapped:
        assert isinstance(ddk_agents.refusal_translator(wrapped), ApprovalRequired)


def _approval() -> Any:
    return approvals_mod.Approval(
        id="a",
        tool="issue_refund",
        arguments={},
        risk=None,
        correlation_id="c",
        requested_at=0.0,
        expires_at=float("inf"),
    )
