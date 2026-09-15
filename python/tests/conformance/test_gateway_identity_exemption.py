"""The gateway-identity conformance exemption is ASSERTED, never skipped (#362, §8.1).

``donkey.last_call`` is populated by the shared transport's ``_on_response``, so an
adapter that does not route through our httpx client structurally cannot observe
it — exactly like the ``correlation_id_propagated`` exemption. This test pins two
things so the exemption stays honest:

1. Every ``KNOWN_LIMITATIONS`` scenario key names a real scenario (no typo silently
   exempting nothing) — the internal-matrix analogue of the customer plugin's
   collection-time validation.
2. The set of adapters exempted from ``gateway_identity_observed`` is EXACTLY the
   set whose :attr:`Adapter.observes_last_call` is ``False``. The exemption table
   and the code fact it documents cannot drift apart: add a non-observing adapter
   without recording the exemption (or vice-versa) and this fails.

Reading ``observes_last_call`` off each adapter class imports only the adapter
modules, which import their framework lazily inside methods — so this needs no
framework extra installed (it lives in ``tests/conformance``, not the base-only
``tests/unit`` job, but stays import-light regardless).
"""

from __future__ import annotations

import importlib

# Sibling data module: tests/conformance/ is not a package, so pytest's prepend
# import mode puts this directory on sys.path and ``suite`` resolves to it.
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS

from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter

_SCENARIO = "gateway_identity_observed"


def _adapter_class(attr: str) -> type[Adapter]:
    spec = ADAPTERS[attr]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls


def test_scenario_is_registered() -> None:
    assert _SCENARIO in CONFORMANCE_SCENARIOS


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


def test_exemption_matches_observes_last_call_flag() -> None:
    # The adapters that record the last_call exemption must be EXACTLY the ones
    # whose class says it cannot observe — the table documents the code fact.
    exempted = {
        adapter
        for adapter, limits in KNOWN_LIMITATIONS.items()
        if _SCENARIO in limits
    }
    non_observing = {
        attr for attr in ADAPTERS if not _adapter_class(attr).observes_last_call
    }
    assert exempted == non_observing, (
        "gateway_identity_observed exemptions and observes_last_call=False adapters "
        f"disagree: exempted={sorted(exempted)}, non_observing={sorted(non_observing)}"
    )
    # And it must be a non-empty set — a conformance surface with zero recorded
    # exemptions here would mean every adapter observes, which is not true.
    assert non_observing == {"adk", "crewai", "llamaindex", "agent_framework"}
