"""The ``approval_round_trip`` scenario (BG §2.3, #211, #212, #213).

An approval-gated tool that calls the model pauses, is approved, and resumes,
under each framework's own pause primitive: the raw client raises
``ApprovalRequired`` and resumes on ``donkey.approvals.resolve(...)``; LangGraph
pauses with ``interrupt()`` and resumes on ``Command(resume=...)``; the OpenAI
Agents SDK stops ``Runner.run`` on ``needs_approval`` and resumes on
``RunState.approve``. The paused tool sends nothing to the gateway, the approver
lands on the approval's span event, and the resumed tool's model call is still
judged by the gateway: an armed refusal reaches user code typed, under the
correlation id the decision event recorded. The approval never overrides it.

``importorskip``-guarded per framework and for the OTel SDK. CrewAI is exempt
(``KNOWN_LIMITATIONS``): its provider owns the transport, so the resumed call
carries neither the approval's correlation id nor a typed refusal.

Strands is supported at ``connection_kwargs()`` only (BG §1.8), so it is not on
the roster and needs no exemption. Its native pause still gets the same round
trip (#1234): :func:`test_a_strands_agent_pauses_with_its_own_interrupt` drives
an agent whose ``ApprovalHook`` pauses the call with Strands' interrupt, using a
scripted Strands model so only the tool's own model call reaches the gateway.
Strands hands a tool's exception to the model as an error tool result, outside
any typed-refusal bridge, so the refusal is asserted where the tool catches it.
Google ADK is supported at ``connection_kwargs()`` only too (#1235):
:func:`test_an_adk_agent_pauses_with_its_own_tool_confirmation` pauses the call
with ADK's tool confirmation through ``approval_plugin()`` and resumes it with
a fresh plugin and runner on the same session, as a new process would, with
the refusal asserted the same way.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

# Sibling data module (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS

from donkey_kit import ApprovalRequired, Donkey, DonkeyConfig, DonkeyError, PIIDetected
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import telemetry
from donkey_kit.core.refusals import translate

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


async def _openai_agents(donkey: Donkey) -> Callable[[], Awaitable[None]]:
    agents = pytest.importorskip("agents")
    from donkey_kit.integrations.openai_agents import approve, function_tool, pending_approvals

    # The model asks for the tool once; the resumed tool's own call is refused.
    for http in http_clients(donkey):
        swap_transport(http, _scripted_tool_call([]))
    client = donkey.openai()

    @donkey.tool(approval="required", risk="financial")
    async def issue_refund(ticket_id: str) -> None:
        """Refund a ticket, after asking the model to word the reply."""
        await client.chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": ticket_id}]
        )

    agent = agents.Agent(
        name="refunds",
        model=donkey.openai_agents.model("gpt-4o"),
        tools=[function_tool(issue_refund)],
    )
    run_config = agents.RunConfig(tracing_disabled=True)
    async with donkey.run(id=_PAUSE_RUN):
        paused = await agents.Runner.run(agent, "refund T-1", run_config=run_config)
        (approval,) = pending_approvals(paused)
    state = paused.to_state()

    async def resume() -> None:
        # The run state holds the pause; resume under the approval's run id to
        # join the decision and the resumed call to it.
        async with donkey.run(id=approval.correlation_id):
            approve(state, approval, by="m.keller")
            await agents.Runner.run(agent, state, run_config=run_config)

    return resume


# The conformance-tested roster (BG §1.8): the raw client, LangGraph and the
# OpenAI Agents SDK. CrewAI's asserted exemption is in suite.py.
SURFACES: dict[str, Pause] = {
    "openai": _openai,
    "langgraph": _langgraph,
    "openai_agents": _openai_agents,
}

#: The model calls a surface makes before the pause, to have the model choose
#: the tool. The Agents SDK runs the agent's model turn first; the others call
#: the tool directly.
_TOOL_CHOICE_CALLS = {"openai": 0, "langgraph": 0, "openai_agents": 1}


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
    assert exempt == {"crewai"}


@pytest.mark.parametrize("surface", SURFACES)
async def test_an_approved_tool_resumes_and_is_still_judged_by_the_gateway(
    surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(monkeypatch)

    async with Donkey(_cfg()) as donkey:
        resume = await SURFACES[surface](donkey)

        # The paused tool sent nothing: besides the pending approval, the only
        # spans are the model calls that chose the tool.
        names = [s.name for s in exporter.get_finished_spans()]
        assert names.count(_APPROVAL_SPAN) == 1
        assert len(names) - 1 == _TOOL_CHOICE_CALLS[surface]
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


async def test_a_strands_agent_pauses_with_its_own_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round trip under a Strands agent with an ``ApprovalHook`` (#1234)."""
    pytest.importorskip("openai")
    strands = pytest.importorskip("strands")
    from strands.models.model import Model

    from donkey_kit.integrations.strands import ApprovalHook, approve, pending_approvals

    class AsksForRefund(Model):
        """Asks for ``issue_refund`` once. Never calls a gateway."""

        turns = 0

        def update_config(self, **model_config: Any) -> None:
            pass

        def get_config(self) -> Any:
            return {}

        async def structured_output(self, *args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError
            yield

        async def stream(self, *args: Any, **kwargs: Any) -> Any:
            self.turns += 1
            assert self.turns == 1, "a tool refusal must stop before the next model turn"
            yield {"messageStart": {"role": "assistant"}}
            start = {"toolUse": {"toolUseId": "tu-1", "name": "issue_refund"}}
            yield {"contentBlockStart": {"start": start}}
            delta = {"toolUse": {"input": json.dumps({"ticket_id": "T-1"})}}
            yield {"contentBlockDelta": {"delta": delta}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}

    exporter = _exporter(monkeypatch)

    async with Donkey(_cfg()) as donkey:
        client = donkey.openai()

        @donkey.tool(approval="required", risk="financial")
        async def issue_refund(ticket_id: str) -> None:
            """Refund a ticket, after asking the model to word the reply."""
            await client.chat.completions.create(
                model="gpt-4o", messages=[{"role": "user", "content": ticket_id}]
            )

        agent = strands.Agent(
            model=AsksForRefund(),
            tools=[strands.tool(issue_refund)],
            hooks=[ApprovalHook()],
            callback_handler=None,
        )
        async with donkey.run(id=_PAUSE_RUN):
            paused = await agent.invoke_async("refund T-1")
        assert paused.stop_reason == "interrupt"
        (interrupt,) = pending_approvals(paused)

        # The pause sent nothing: the only span is the pending approval.
        assert {s.name for s in exporter.get_finished_spans()} == {_APPROVAL_SPAN}
        ((pending, pending_attrs),) = _approval_events(exporter)
        assert pending == "donkey.approval.pending"
        assert pending_attrs["donkey.approval.risk"] == "financial"
        assert pending_attrs["donkey.correlation_id"] == _PAUSE_RUN

        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as caught:
            async with donkey.run():
                await agent.invoke_async([approve(interrupt, by="m.keller")])

    decisions = [(n, a) for n, a in _approval_events(exporter) if n != pending]
    ((approved, approved_attrs),) = decisions
    assert approved == "donkey.approval.approved"
    assert approved_attrs["donkey.approval.approved_by"] == "m.keller"
    assert approved_attrs["donkey.approval.id"] == pending_attrs["donkey.approval.id"]
    # The resumed call went to the gateway under the decision's correlation id,
    # and the gateway refused it: the approval did not lift it.
    assert caught.value.correlation_id == approved_attrs["donkey.correlation_id"] == _PAUSE_RUN


async def test_an_adk_agent_pauses_with_its_own_tool_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round trip under an ADK app with the approval plugin (#1235), resumed
    by a fresh plugin and runner on the same session, as a new process would."""
    pytest.importorskip("openai")
    pytest.importorskip("google.adk")
    from google.adk.agents import LlmAgent
    from google.adk.apps import App
    from google.adk.models import BaseLlm, LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from donkey_kit.integrations.adk import approval_plugin, approve, pending_approvals

    class AsksForRefund(BaseLlm):
        """Asks for ``issue_refund`` once, then answers. Never calls a gateway."""

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> Any:
            answered = any(
                part.function_response is not None
                for content in llm_request.contents
                for part in content.parts or ()
            )
            if answered:
                part = types.Part(text="done")
            else:
                call = types.FunctionCall(id="fc-1", name="issue_refund", args={"ticket_id": "T-1"})
                part = types.Part(function_call=call)
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    exporter = _exporter(monkeypatch)
    refusals: list[DonkeyError | None] = []
    sessions = InMemorySessionService()

    async with Donkey(_cfg()) as donkey:
        client = donkey.openai()

        @donkey.tool(approval="required", risk="financial")
        async def issue_refund(ticket_id: str) -> dict[str, str]:
            """Refund a ticket, after asking the model to word the reply."""
            try:
                await client.chat.completions.create(
                    model="gpt-4o", messages=[{"role": "user", "content": ticket_id}]
                )
            except Exception as exc:  # noqa: BLE001 - asserted below, typed
                refusals.append(translate(exc))
                return {"status": "refused"}
            return {"status": "refunded"}

        def runner() -> Any:
            agent = LlmAgent(
                name="refunds", model=AsksForRefund(model="scripted"), tools=[issue_refund]
            )
            app = App(name="refunds", root_agent=agent, plugins=[approval_plugin()])
            return Runner(app=app, session_service=sessions)

        session = await sessions.create_session(app_name="refunds", user_id="u")

        async def send(message: types.Content) -> list[Any]:
            run = runner().run_async(user_id="u", session_id=session.id, new_message=message)
            return [event async for event in run]

        async with donkey.run(id=_PAUSE_RUN):
            paused = await send(types.Content(role="user", parts=[types.Part(text="refund T-1")]))
        (request,) = pending_approvals(paused)

        # The pause sent nothing: the only span is the pending approval.
        assert {s.name for s in exporter.get_finished_spans()} == {_APPROVAL_SPAN}
        ((pending, pending_attrs),) = _approval_events(exporter)
        assert pending == "donkey.approval.pending"
        assert pending_attrs["donkey.approval.risk"] == "financial"
        assert pending_attrs["donkey.correlation_id"] == _PAUSE_RUN

        with donkey.simulate(PIIDetected):
            # A fresh run: the resumed call still runs under the pause's.
            async with donkey.run():
                resumed = await send(
                    types.Content(role="user", parts=[approve(request, by="m.keller")])
                )

    assert resumed[-1].content.parts[0].text == "done"
    # The session holds the user's answer too, so nothing is left pending.
    stored = await sessions.get_session(app_name="refunds", user_id="u", session_id=session.id)
    assert stored is not None
    assert not pending_approvals(stored.events)
    decisions = [(n, a) for n, a in _approval_events(exporter) if n != pending]
    ((approved, approved_attrs),) = decisions
    assert approved == "donkey.approval.approved"
    assert approved_attrs["donkey.approval.approved_by"] == "m.keller"
    assert approved_attrs["donkey.approval.id"] == pending_attrs["donkey.approval.id"]
    # The resumed call went to the gateway under the pause's correlation id, and
    # the gateway refused it: the approval did not lift it.
    (refused,) = refusals
    assert isinstance(refused, PIIDetected)
    assert refused.correlation_id == approved_attrs["donkey.correlation_id"] == _PAUSE_RUN
