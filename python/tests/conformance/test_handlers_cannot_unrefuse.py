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

The surfaces are every adapter factory that sends through the SDK's transport
(``transport_surfaces.py``, #1441). ``importorskip``-guarded per framework.
CrewAI is exempt (``KNOWN_LIMITATIONS``): its provider owns the transport, so
no handler of ours ever sees its refusals.
"""

from __future__ import annotations

import httpx
import pytest

# Sibling data modules (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS
from transport_surfaces import (
    FACTORIES,
    MODEL_IN_BODY,
    SURFACES,
    framework_transport_adapters,
    ok,
    transport_factories,
)

from donkey_kit import Donkey, DonkeyConfig, PIIDetected
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import RefusalContext

_SCENARIO = "refusal_handler_cannot_unrefuse"


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


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


VERBS = ["wait_for_reset", "fallback", "call.resend", "call.returns", "call.raises", "escalate"]


def _resends(surface: str, verb: str) -> bool:
    """Whether ``verb`` issues a new governed request on ``surface``; the rest
    only ever propagate. ``fallback`` needs the model in the JSON body."""
    return verb == "call.resend" or (verb == "fallback" and surface in MODEL_IN_BODY)


def _wire(donkey: Donkey) -> list[httpx.Request]:
    """Route ``donkey``'s clients to a gateway that allows everything."""
    sent: list[httpx.Request] = []

    def allow(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return ok(request)

    for client in http_clients(donkey):
        swap_transport(client, httpx.MockTransport(allow))
    return sent


def test_scenario_is_registered() -> None:
    assert _SCENARIO in CONFORMANCE_SCENARIOS
    exempt = {a for a, limits in KNOWN_LIMITATIONS.items() if _SCENARIO in limits}
    assert exempt == framework_transport_adapters() == {"crewai"}


def test_every_transport_factory_runs_the_scenario() -> None:
    # The README says every adapter but CrewAI runs handlers (#1441).
    assert set(FACTORIES) == set(SURFACES) - {"openai"}
    assert set(FACTORIES.values()) == transport_factories()


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
    resends = _resends(surface, verb)
    async with Donkey(_cfg()) as donkey:
        _register(donkey, verb)
        sent = _wire(donkey)
        # Only the first call is refused; a new request would be allowed.
        with donkey.simulate(PIIDetected, times=1):
            if resends:
                async with donkey.run():
                    await SURFACES[surface](donkey)
            else:
                # The bridge types the framework's error as the scope exits.
                with pytest.raises(PIIDetected):
                    async with donkey.run():
                        await SURFACES[surface](donkey)
        assert len(sent) == (1 if resends else 0)
        if resends and verb == "fallback":
            assert b'"internal-safe-model"' in sent[0].content
        elif resends:
            assert b"[MASKED]" in sent[0].content
