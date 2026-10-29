"""The ``refusal_handler_cannot_unrefuse`` scenario (BG §2.1, #208, #209).

A handler registered with ``donkey.on(...)`` runs at the transport's
``_on_refusal`` hook, under whatever framework sits between the call and the
caller. Whatever it does, a refused call never reaches user code as a success:
either the refusal propagates (typed, inside ``donkey.run()``), or a **new**
governed request went out and its own result is returned.

Each verb runs on each surface twice:

* the gateway refuses every call: user code gets :class:`PIIDetected`;
* the gateway refuses only the first: user code succeeds only when the handler
  issued a new request, and the wire saw exactly that request.

``importorskip``-guarded per framework. CrewAI is exempt (``KNOWN_LIMITATIONS``):
its provider owns the transport, so no handler of ours ever sees its refusals.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

# Sibling data module (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS

from donkey_kit import Donkey, DonkeyConfig, PIIDetected
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import RefusalContext

_SCENARIO = "refusal_handler_cannot_unrefuse"
Call = Callable[[Donkey], Awaitable[None]]


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


async def _openai(donkey: Donkey) -> None:
    pytest.importorskip("openai")
    client = donkey.openai()
    await client.chat.completions.create(
        model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
    )


async def _langgraph(donkey: Donkey) -> None:
    pytest.importorskip("langchain_openai")
    graph_mod = pytest.importorskip("langgraph.graph")
    model = donkey.langgraph.chat_model("gpt-4o")

    async def call_model(state: dict[str, Any]) -> dict[str, Any]:
        await model.ainvoke(state["messages"])
        return {}

    builder = graph_mod.StateGraph(dict)
    builder.add_node("call_model", call_model)
    builder.set_entry_point("call_model")
    builder.add_edge("call_model", graph_mod.END)
    await builder.compile().ainvoke({"messages": [("user", "hi")]})


async def _openai_agents(donkey: Donkey) -> None:
    agents = pytest.importorskip("agents")
    agent = agents.Agent(name="conformance", model=donkey.openai_agents.model("gpt-4o"))
    # Tracing off: the Agents SDK would otherwise export spans to OpenAI.
    await agents.Runner.run(agent, "hi", run_config=agents.RunConfig(tracing_disabled=True))


# The conformance-tested roster (BG §1.8): the raw client, LangGraph and the
# OpenAI Agents SDK (#223).
SURFACES: dict[str, Call] = {
    "openai": _openai,
    "langgraph": _langgraph,
    "openai_agents": _openai_agents,
}


def _mask(ctx: RefusalContext) -> None:
    ctx.resend(json={**(ctx.body or {}), "messages": [{"role": "user", "content": "[MASKED]"}]})


def _raise(ctx: RefusalContext) -> None:
    raise RuntimeError("the masking service is down")


def _register(donkey: Donkey, verb: str) -> None:
    on = donkey.on(PIIDetected)
    if verb == "wait_for_reset":
        on.wait_for_reset()  # a PII refusal carries no reset time
    elif verb == "fallback":
        on.fallback(model="internal-safe-model")
    elif verb == "call.resend":
        on.call(_mask)
    elif verb == "call.returns":
        on.call(lambda ctx: None)
    elif verb == "call.raises":
        on.call(_raise)
    else:
        on.escalate(to=lambda violation: None)


# The verbs that issue a new governed request; the rest only ever propagate.
_RESENDING = {"fallback", "call.resend"}
VERBS = ["wait_for_reset", "fallback", "call.resend", "call.returns", "call.raises", "escalate"]


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def _wire(donkey: Donkey) -> list[httpx.Request]:
    """Route ``donkey``'s clients to a gateway that allows everything."""
    sent: list[httpx.Request] = []

    def allow(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _ok(request)

    for client in http_clients(donkey):
        swap_transport(client, httpx.MockTransport(allow))
    return sent


def test_scenario_is_registered() -> None:
    assert _SCENARIO in CONFORMANCE_SCENARIOS
    exempt = {a for a, limits in KNOWN_LIMITATIONS.items() if _SCENARIO in limits}
    assert exempt == {"crewai"}


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("surface", SURFACES)
async def test_a_handler_never_turns_a_refusal_into_a_success(surface: str, verb: str) -> None:
    async with Donkey(_cfg()) as donkey:
        _register(donkey, verb)
        sent = _wire(donkey)
        # Every call is refused, the handler's own new request included.
        with donkey.simulate(PIIDetected, times=10), pytest.raises(PIIDetected):
            async with donkey.run():
                await SURFACES[surface](donkey)
        assert sent == []


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("surface", SURFACES)
async def test_a_success_comes_only_from_a_new_governed_request(surface: str, verb: str) -> None:
    async with Donkey(_cfg()) as donkey:
        _register(donkey, verb)
        sent = _wire(donkey)
        # Only the first call is refused; a new request would be allowed.
        with donkey.simulate(PIIDetected, times=1):
            if verb in _RESENDING:
                async with donkey.run():
                    await SURFACES[surface](donkey)
            else:
                # The bridge types the framework's error as the scope exits.
                with pytest.raises(PIIDetected):
                    async with donkey.run():
                        await SURFACES[surface](donkey)
        assert len(sent) == (1 if verb in _RESENDING else 0)
        if verb == "fallback":
            assert b'"internal-safe-model"' in sent[0].content
        elif verb == "call.resend":
            assert b"[MASKED]" in sent[0].content
