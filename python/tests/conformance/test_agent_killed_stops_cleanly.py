"""The ``agent_killed_stops_cleanly`` scenario (BG §2.6, #220).

When the gateway's Agent Kill Switch refuses a call, the agent stops, under
whatever framework sits between the call and the caller:

* the kill reaches user code typed, as :class:`AgentKilled`, inside ``donkey.run()``;
* it is sent once, with retries on: neither the transport nor the framework's
  provider SDK re-sends it;
* no ``donkey.on(...)`` handler runs for it, not even one on ``PolicyViolation``;
* a sibling task's pace loop, asleep in ``wait_for_reset()``, wakes and stops too;
* spans are flushed before the call unwinds.

The gateway replies with the committed live capture (docs/verified-apis.md §4,
#694). ``importorskip``-guarded per framework. CrewAI is exempt
(``KNOWN_LIMITATIONS``): its provider owns the transport, so its kill never
reaches ours.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

# Sibling data modules (tests/conformance/ is not a package).
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS
from test_handlers_cannot_unrefuse import SURFACES

from donkey_kit import AgentKilled, BudgetReserveReached, Donkey, DonkeyConfig, PolicyViolation
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import spanflush
from donkey_kit.core.budget import Budget
from donkey_kit.simulator import fixtures

_SCENARIO = "agent_killed_stops_cleanly"


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=3,
    )


def _wire(donkey: Donkey) -> list[httpx.Request]:
    """Route ``donkey``'s clients to a gateway that has killed the agent."""
    fx = fixtures.load("agent-killed")
    headers = {**fixtures.replay_headers(fx), "content-type": fx.content_type or ""}
    sent: list[httpx.Request] = []

    def killed(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(fx.status, headers=headers, content=fx.body)

    for client in http_clients(donkey):
        swap_transport(client, httpx.MockTransport(killed))
    return sent


def _spend(budget: Budget) -> None:
    """Exhaust the token window, due to reset in an hour."""
    budget.observe(
        httpx.Response(
            429,
            headers={"x-token-limit": "100", "x-token-remaining": "0", "x-token-reset": "3600000"},
        )
    )


async def _pace_loop(budget: Budget) -> None:
    """A sibling worker running the documented pacing loop."""
    while True:
        try:
            async with budget.pace():
                raise AssertionError("the spent window refuses before the work runs")
        except BudgetReserveReached as exc:
            await budget.wait_for_reset(window=exc.window)


def test_scenario_is_registered() -> None:
    assert _SCENARIO in CONFORMANCE_SCENARIOS
    exempt = {a for a, limits in KNOWN_LIMITATIONS.items() if _SCENARIO in limits}
    assert exempt == {"crewai"}


@pytest.mark.parametrize("surface", SURFACES)
async def test_a_kill_stops_the_agent_and_its_pace_loops(
    surface: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushes: list[None] = []
    monkeypatch.setattr(spanflush, "flush", lambda: flushes.append(None))
    handled: list[object] = []
    async with Donkey(_cfg()) as donkey:
        donkey.on(PolicyViolation).call(handled.append)
        sent = _wire(donkey)
        _spend(donkey.budget)
        sibling = asyncio.ensure_future(_pace_loop(donkey.budget))
        while not donkey.budget._sleeps:  # until the sibling is asleep
            await asyncio.sleep(0)
        with pytest.raises(AgentKilled):
            async with donkey.run():
                await SURFACES[surface](donkey)
        with pytest.raises(AgentKilled):
            await asyncio.wait_for(sibling, timeout=5)  # not the hour its reset is due in
    assert len(sent) == 1
    assert handled == []
    assert flushes == [None]
