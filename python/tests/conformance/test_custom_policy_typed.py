"""The ``custom_policy_typed`` scenario (BG §2.2, #210).

A refusal shape a customer registered with ``classify.register()`` travels the
same path as a built-in one, under each framework: the simulated response (the
registration's own synthesised example) reaches user code as the registered
type inside ``donkey.run()``, a ``donkey.on(...)`` handler for it runs, and the
span records ``donkey.policy.type`` as the registration's slug, verbatim.

The surfaces are every adapter factory that sends through the SDK's transport
(``transport_surfaces.py``, #1441). ``importorskip``-guarded per framework and
for the OTel SDK. CrewAI is exempt (``KNOWN_LIMITATIONS``): its provider owns
the transport, so neither the typed bridge nor a handler of ours ever sees its
refusals.
"""

from __future__ import annotations

from typing import Any

import pytest

# Sibling data modules (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS
from transport_surfaces import (
    FACTORIES,
    SURFACES,
    framework_transport_adapters,
    transport_factories,
)

from donkey_kit import Donkey, DonkeyConfig, PolicyViolation, classify
from donkey_kit.core import RefusalContext, telemetry

_SCENARIO = "custom_policy_typed"


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
    assert exempt == framework_transport_adapters() == {"crewai"}


def test_every_transport_factory_runs_the_scenario() -> None:
    # The README says every adapter but CrewAI types a registered shape (#1441).
    assert set(FACTORIES) == set(SURFACES) - {"openai"}
    assert set(FACTORIES.values()) == transport_factories()


async def _refuse(surface: str, form: str) -> tuple[SalaryLeakBlocked, list[PolicyViolation]]:
    """Refuse one call on ``surface`` with the ``form`` registration; return the
    error user code caught and the violations the handler saw."""
    classify.register(match=MATCHES[form], raises=SalaryLeakBlocked)
    seen: list[PolicyViolation] = []

    def record(ctx: RefusalContext) -> None:
        seen.append(ctx.violation)

    async with Donkey(_cfg()) as donkey:
        donkey.on(SalaryLeakBlocked).call(record)
        with donkey.simulate(SalaryLeakBlocked), pytest.raises(SalaryLeakBlocked) as exc:
            async with donkey.run():
                await SURFACES[surface](donkey)
    return exc.value, seen


@pytest.mark.parametrize("form", MATCHES)
@pytest.mark.parametrize("surface", SURFACES)
async def test_a_custom_refusal_is_typed_and_handled(surface: str, form: str) -> None:
    # No OTel SDK needed, so this runs in every framework's CI leg.
    error, seen = await _refuse(surface, form)
    assert error.policy == "acme_salary_block"
    assert str(error).startswith("Request refused by custom policy acme_salary_block")
    assert [type(v) for v in seen] == [SalaryLeakBlocked]


@pytest.mark.parametrize("form", MATCHES)
@pytest.mark.parametrize("surface", SURFACES)
async def test_a_custom_refusal_is_named_on_the_span(
    surface: str, form: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(monkeypatch)
    await _refuse(surface, form)
    spans = [dict(s.attributes or {}) for s in exporter.get_finished_spans()]
    refused = [a for a in spans if a.get("donkey.policy.decision") == "refuse"]
    assert [a["donkey.policy.type"] for a in refused] == ["acme_salary_block"]
