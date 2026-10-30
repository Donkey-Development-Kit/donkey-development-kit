"""OpenAI Agents SDK correlation-id propagation (#223, BG §1.1).

The deep-adapter guarantee LangGraph proves in ``test_langgraph_correlation.py``,
for ``Runner.run``: a run id bound once with ``donkey.run(id=…)`` is visible
inside the agent's dynamic instructions and inside a ``function_tool`` with
nothing passed through the run context, and every model request the run makes
carries it on the wire. The SDK runs both inside the caller's context, and the
model sends through our shared transport (full injection), so nothing is exempt.

Skipped where ``agents`` is not installed (the ``openai-agents`` extra); runs in
any env with the extra present, including the ``adapter-contract(openai-agents)``
CI leg and the nightly matrix.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core.telemetry import current_correlation_id

_HEADER = "x-correlation-id"


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        correlation_header=_HEADER,
        max_retries=0,
    )


def _gateway(sent: list[httpx.Request]) -> httpx.MockTransport:
    """A gateway whose model calls ``lookup`` once, then answers."""

    def reply(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if len(sent) == 1:
            call = {"name": "lookup", "arguments": json.dumps({"ticket_id": "T-1"})}
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


async def test_run_id_reaches_instructions_tools_and_the_wire() -> None:
    agents = pytest.importorskip("agents")

    observed: list[tuple[str, str | None]] = []

    def instructions(_ctx: Any, _agent: Any) -> str:
        observed.append(("instructions", current_correlation_id()))
        return "Reply briefly."

    @agents.function_tool
    def lookup(ticket_id: str) -> str:
        """Look a ticket up."""
        observed.append(("tool", current_correlation_id()))
        return f"{ticket_id}: open"

    sent: list[httpx.Request] = []
    async with Donkey(_cfg()) as donkey:
        for client in http_clients(donkey):
            swap_transport(client, _gateway(sent))
        agent = agents.Agent(
            name="triage",
            instructions=instructions,
            model=donkey.openai_agents.model("gpt-4o"),
            tools=[lookup],
        )
        async with donkey.run(id="run-oa"):
            result = await agents.Runner.run(
                agent, "look up T-1", run_config=agents.RunConfig(tracing_disabled=True)
            )

    assert result.final_output == "done"
    # Instructions are resolved once per turn: two turns around the one tool call.
    assert observed == [("instructions", "run-oa"), ("tool", "run-oa"), ("instructions", "run-oa")]
    assert [r.headers[_HEADER] for r in sent] == ["run-oa", "run-oa"]
    assert current_correlation_id() is None  # nothing leaked past the block
