"""The ``custom_policy_typed`` scenario (BG §2.2, #210).

A refusal shape a customer registered with ``classify.register()`` travels the
same path as a built-in one, under each framework: the simulated response (the
registration's own synthesised example) reaches user code as the registered
type inside ``donkey.run()``, a ``donkey.on(...)`` handler for it runs, and the
span records ``donkey.policy.type`` as the registration's slug, verbatim.

``importorskip``-guarded per framework and for the OTel SDK. CrewAI is exempt
(``KNOWN_LIMITATIONS``): its provider owns the transport, so neither the typed
bridge nor a handler of ours ever sees its refusals.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

# Sibling data module (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS

from donkey_kit import Donkey, DonkeyConfig, PolicyViolation, classify
from donkey_kit.core import RefusalContext, telemetry

_SCENARIO = "custom_policy_typed"
Call = Callable[[Donkey], Awaitable[None]]


class SalaryLeakBlocked(PolicyViolation):
    remediation = "Salary figures may not leave HR. Ask the user to open the HR portal."
    policy = "acme_salary_block"


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

# Each registration form a customer is most likely to write.
MATCHES: dict[str, dict[str, Any]] = {
    "error_type": {"status": 403, "error_type": "acme_salary_block"},
    "header": {"status": 403, "headers": {"x-acme-policy": "salary-block"}},
    "body": {"status": 451, "body": {"policy.id": "salary-guard"}},
}


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


def test_scenario_is_registered() -> None:
    assert _SCENARIO in CONFORMANCE_SCENARIOS
    exempt = {a for a, limits in KNOWN_LIMITATIONS.items() if _SCENARIO in limits}
    assert exempt == {"crewai"}


@pytest.mark.parametrize("form", MATCHES)
@pytest.mark.parametrize("surface", SURFACES)
async def test_a_custom_refusal_is_typed_handled_and_named_on_the_span(
    surface: str, form: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(monkeypatch)
    classify.register(match=MATCHES[form], raises=SalaryLeakBlocked)
    seen: list[PolicyViolation] = []

    def record(ctx: RefusalContext) -> None:
        seen.append(ctx.violation)

    async with Donkey(_cfg()) as donkey:
        donkey.on(SalaryLeakBlocked).call(record)
        with donkey.simulate(SalaryLeakBlocked), pytest.raises(SalaryLeakBlocked) as exc:
            async with donkey.run():
                await SURFACES[surface](donkey)

    assert exc.value.policy == "acme_salary_block"
    assert str(exc.value).startswith("Request refused by custom policy acme_salary_block")
    assert [type(v) for v in seen] == [SalaryLeakBlocked]
    spans = [dict(s.attributes or {}) for s in exporter.get_finished_spans()]
    refused = [a for a in spans if a.get("donkey.policy.decision") == "refuse"]
    assert [a["donkey.policy.type"] for a in refused] == ["acme_salary_block"]
