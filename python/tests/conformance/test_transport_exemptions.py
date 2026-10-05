"""Transport-owned conformance exemptions are ASSERTED, never skipped (#362, #504,
the conformance kit).

``donkey.last_call`` is populated by the shared transport's ``_on_response``, so an
adapter that does not route through our httpx client structurally cannot observe
it — exactly like the ``correlation_id_propagated`` exemption. This test pins
these things so the exemption stays honest:

1. Every ``KNOWN_LIMITATIONS`` scenario key names a real scenario (no typo silently
   exempting nothing) — the internal-matrix analogue of the customer plugin's
   collection-time validation.
2. The set of adapters exempted from ``gateway_identity_observed`` is EXACTLY the
   set whose default factory's ``capabilities().observes_last_call`` is ``False``
   (#726). The exemption table
   and the code fact it documents cannot drift apart: add a non-observing adapter
   without recording the exemption (or vice-versa) and this fails.
3. The adapters that record no exemption really are handed the shared client:
   MS Agent Framework and ADK ``model()`` get an OpenAI client that sends
   through it; LlamaIndex and ADK ``gemini()`` get its view (#740).

Reading ``capabilities()`` off each adapter class imports only the adapter
modules, which import their framework lazily inside methods — so this needs no
framework extra installed (it lives in ``tests/conformance``, not the base-only
``tests/unit`` job, but stays import-light regardless).
"""

from __future__ import annotations

import importlib

import pytest

# Sibling data module: tests/conformance/ is not a package, so pytest's prepend
# import mode puts this directory on sys.path and ``suite`` resolves to it.
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import build_http_client
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter
from donkey_kit.llm import client as llm_client

_GATEWAY_SCENARIO = "gateway_identity_observed"
_CORRELATION_SCENARIO = "correlation_id_propagated"
_JWT_SCENARIO = "jwt_token_refreshed"
# The connection_kwargs() key each formerly exempted adapter hands its
# pre-built OpenAI client under (#740).
_OPENAI_CLIENT_KWARG = {"agent_framework": "async_client", "adk": "client"}


def _adapter_class(attr: str) -> type[Adapter]:
    spec = ADAPTERS[attr]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls


def _exempted(scenario: str) -> set[str]:
    return {adapter for adapter, limits in KNOWN_LIMITATIONS.items() if scenario in limits}


def test_scenario_is_registered() -> None:
    assert {_GATEWAY_SCENARIO, _CORRELATION_SCENARIO, _JWT_SCENARIO} <= set(
        CONFORMANCE_SCENARIOS
    )


def test_conformance_scenarios_are_exactly_the_executable_set() -> None:
    # Locks the #749 retire decision: every surviving name has a real test body
    # (see the module docstring's mapping), and a scenario describing a
    # registry/tools/governance/publication surface that still raises
    # _verify.blocked(...) never sneaks back in without one.
    assert set(CONFORMANCE_SCENARIOS) == {
        "simple_completion",
        "streaming_completion",
        "policy_violation_terminal",
        "attribution_headers_present",
        "correlation_id_propagated",
        "gateway_identity_observed",
        "jwt_token_refreshed",
        "typed_refusal_bridged",
    }
    assert len(CONFORMANCE_SCENARIOS) == len(set(CONFORMANCE_SCENARIOS)), (
        "CONFORMANCE_SCENARIOS has a duplicate entry"
    )


def test_every_known_limitation_names_a_real_scenario() -> None:
    scenarios = set(CONFORMANCE_SCENARIOS)
    for adapter, limits in KNOWN_LIMITATIONS.items():
        for scenario, reason in limits.items():
            assert scenario in scenarios, (
                f"KNOWN_LIMITATIONS[{adapter!r}] names unknown scenario {scenario!r}"
            )
            assert isinstance(reason, str) and reason.strip(), (
                f"KNOWN_LIMITATIONS[{adapter!r}][{scenario!r}] must be a non-empty reason"
            )


@pytest.mark.parametrize("scenario", [_CORRELATION_SCENARIO, _GATEWAY_SCENARIO, _JWT_SCENARIO])
def test_only_crewai_is_exempt(scenario: str) -> None:
    # Every other adapter sends through the shared client (#740, #828). CrewAI's
    # native OpenAI provider builds its own clients from one client_params dict
    # for both sync and async, so ours cannot be injected — asserted, not skipped.
    assert _exempted(scenario) == {"crewai"}
    assert "client_params" in KNOWN_LIMITATIONS["crewai"][scenario]


def test_exemption_matches_observes_last_call_flag() -> None:
    # The adapters that record the last_call exemption must be EXACTLY the ones
    # whose class says it cannot observe — the table documents the code fact.
    non_observing = {
        attr for attr in ADAPTERS if not _adapter_class(attr).capabilities().observes_last_call
    }
    assert _exempted(_GATEWAY_SCENARIO) == non_observing, (
        "gateway_identity_observed exemptions and observes_last_call=False adapters "
        f"disagree: exempted={sorted(_exempted(_GATEWAY_SCENARIO))}, "
        f"non_observing={sorted(non_observing)}"
    )


async def test_formerly_exempt_adapters_are_handed_the_shared_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fact that retires their exemptions (#740): each connection_kwargs()
    # carries our shared client's view, or an OpenAI client sending through it
    # (adk.gemini() since #691). Pinned to openai<3, where that client is the
    # view itself; on openai>=3 it is the httpx2 bridge over the same client,
    # covered by test_adapter_openai3_bridge.py (#728).
    monkeypatch.setattr(llm_client, "_openai_on_httpx2", lambda: False)
    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
    )
    client = build_http_client(cfg, None)
    try:
        for attr, key in _OPENAI_CLIENT_KWARG.items():
            kw = _adapter_class(attr)(cfg, client).connection_kwargs()
            assert kw[key]._client is client.view(), attr
        li = _adapter_class("llamaindex")(cfg, client).connection_kwargs()
        assert li["async_http_client"] is client.view()
        adk = _adapter_class("adk")(cfg, client)
        gem = adk.gemini_connection_kwargs()  # type: ignore[attr-defined]
        assert gem["client_kwargs"]["http_options"]["httpx_async_client"] is client.view()
    finally:
        await client.aclose()
