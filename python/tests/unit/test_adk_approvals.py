"""Approval-gated tools under a real ADK app (BG §2.3, #1235).

The approved round trip is in ``tests/conformance/test_approval_round_trip.py``;
these cover a rejection and a gated tool without the plugin. Run in the
``adk-stacks`` nox session, at google-adk's floor and its newest release.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("google.adk")

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from donkey_kit import ApprovalRequired, Donkey
from donkey_kit.integrations.adk import approval_plugin, pending_approvals, reject

ran: list[str] = []
#: The tool results the model was shown.
seen: list[Any] = []


@Donkey.tool(approval="required", risk="financial")
def issue_refund(ticket_id: str) -> dict[str, str]:
    """Refund a support ticket."""
    ran.append(ticket_id)
    return {"status": "refunded"}


class AsksForRefund(BaseLlm):
    """Asks for ``issue_refund`` once, then answers, keeping what the tool
    answered in :data:`seen`."""

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> Any:
        answers = [
            part.function_response.response
            for content in llm_request.contents
            for part in content.parts or ()
            if part.function_response is not None
        ]
        if answers:
            seen.extend(answers)
            part = types.Part(text="done")
        else:
            call = types.FunctionCall(id="fc-1", name="issue_refund", args={"ticket_id": "T-1"})
            part = types.Part(function_call=call)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


async def _send(runner: Runner, session_id: str, message: types.Content) -> list[Any]:
    run = runner.run_async(user_id="u", session_id=session_id, new_message=message)
    return [event async for event in run]


def _runner(*plugins: Any) -> tuple[Runner, InMemorySessionService]:
    agent = LlmAgent(name="refunds", model=AsksForRefund(model="scripted"), tools=[issue_refund])
    sessions = InMemorySessionService()
    app = App(name="refunds", root_agent=agent, plugins=list(plugins))
    return Runner(app=app, session_service=sessions), sessions


async def test_a_rejected_call_is_not_run_and_the_model_is_told_without_the_rejecter() -> None:
    ran.clear()
    seen.clear()
    runner, sessions = _runner(approval_plugin())
    session = await sessions.create_session(app_name="refunds", user_id="u")
    ask = types.Content(role="user", parts=[types.Part(text="refund T-1")])
    (request,) = pending_approvals(await _send(runner, session.id, ask))

    answer = types.Content(role="user", parts=[reject(request, by="a.ops")])
    resumed = await _send(runner, session.id, answer)

    assert ran == []
    assert resumed[-1].content.parts[0].text == "done"
    (told,) = seen
    assert "issue_refund" in told["error"] and "a.ops" not in str(told)


async def test_a_gated_tool_without_the_plugin_raises_approval_required() -> None:
    ran.clear()
    runner, sessions = _runner()
    session = await sessions.create_session(app_name="refunds", user_id="u")
    ask = types.Content(role="user", parts=[types.Part(text="refund T-1")])
    with pytest.raises(ApprovalRequired):
        await _send(runner, session.id, ask)
    assert ran == []
