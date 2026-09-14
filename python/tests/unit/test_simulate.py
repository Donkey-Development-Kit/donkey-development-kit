"""``donkey.simulate()`` — in-process refusal injection with no server (#190, BG §1.5).

The in-process sibling of the donkey mock server (BG §1.4): instead of a TCP
port, it swaps a fixture-returning transport onto the live Donkey client for the
next N calls, so the refusal branch of an agent runs with no network and no
server. Framework-free — driven straight through the shared ``httpx`` client,
exactly as ``donkey.openai()`` would, so these run under ``[dev]`` alone (no
``[local]`` extra, no ``importorskip``).

Acceptance bar (issue #190):
  1. works with no network/server by swapping ``_transport``;
  2. ``times=N`` is honoured exactly, call N+1 proceeds normally;
  3. nesting and exiting restore the previous transport even on exception;
  4. replays the same fixtures classify() and the simulator are tested against.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import (
    AuthError,
    ConfigError,
    ContentSafetyBlocked,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
    UpstreamModelError,
    UpstreamRequestError,
    classify,
)
from donkey_kit.simulator import fixtures as fx
from donkey_kit.simulator.app import SIMULATOR_HEADER

_URL = "http://sim.local/responses"


def _donkey(cfg: DonkeyConfig | None = None) -> Donkey:
    return Donkey(cfg or DonkeyConfig())


async def _post(donkey: Donkey) -> httpx.Response:
    """One async round-trip through the shared client (stands in for the OpenAI
    client / donkey.openai())."""
    return await donkey._http.post(_URL, json={"model": "gpt-5.1"})


def _sentinel(status: int = 299) -> httpx.MockTransport:
    """A distinguishable 'real' transport so a passed-through (non-injected) call
    is provably not the fixture."""
    return httpx.MockTransport(lambda request: httpx.Response(status))


# The documented exception -> fixture-shape round-trip set. This list is the
# spec; test_mapping_table_matches_documented_set pins the implementation to it.
_ROUND_TRIP = [
    (TokenBudgetExceeded, "token-rate-limit"),
    (PIIDetected, "pii-detected"),
    (PromptInjectionBlocked, "injection-protection"),
    (ContentSafetyBlocked, "content-safety"),
    (UpstreamRequestError, "model-not-found"),
    (UpstreamModelError, "upstream-5xx"),
    (AuthError, "client-id-missing"),
    (PolicyViolation, "content-moderation"),
]


@pytest.mark.parametrize("exc,shape", _ROUND_TRIP, ids=[e.__name__ for e, _ in _ROUND_TRIP])
async def test_injected_refusal_replays_fixture_and_classifies_back(
    exc: type, shape: str
) -> None:
    # AC (4): the injected body is byte-identical to the fixture classify() is
    # tested against, so it lights up as exactly the requested typed refusal.
    donkey = _donkey()
    async with donkey:
        with donkey.simulate(exc):
            resp = await _post(donkey)
        assert resp.content == fx.load(shape).body
        assert isinstance(classify(resp), exc)


async def test_injected_response_is_honesty_stamped() -> None:
    # BG §1.4 honesty rule holds for in-process injection too: a simulated
    # refusal is identifiable as simulated in a log/trace, never mistaken for a
    # real gateway response — while the body stays the real captured fixture.
    donkey = _donkey()
    async with donkey:
        with donkey.simulate(PIIDetected):
            resp = await _post(donkey)
        assert resp.headers[SIMULATOR_HEADER] == "true"
        assert resp.content == fx.load("pii-detected").body


async def test_mapping_table_matches_documented_set() -> None:
    from donkey_kit.simulator.inject import _EXC_TO_SHAPE

    assert _EXC_TO_SHAPE == dict(_ROUND_TRIP)


async def test_times_is_honoured_exactly_then_passes_through() -> None:
    # AC (2): the first ``times`` calls get the fixture; call times+1 proceeds
    # normally (to the wrapped transport).
    donkey = _donkey()
    donkey._http._swap_transport(_sentinel())
    async with donkey:
        with donkey.simulate(PIIDetected, times=2):
            r1 = await _post(donkey)
            r2 = await _post(donkey)
            r3 = await _post(donkey)  # times+1 → wrapped transport
        assert (r1.status_code, r2.status_code) == (403, 403)
        assert r3.status_code == 299
        # After the block the wrapped transport is restored: no fixture leaks out.
        assert (await _post(donkey)).status_code == 299


async def test_default_times_is_one() -> None:
    donkey = _donkey()
    donkey._http._swap_transport(_sentinel())
    async with donkey:
        with donkey.simulate(PIIDetected):
            first = await _post(donkey)
            second = await _post(donkey)
        assert first.status_code == 403
        assert second.status_code == 299


async def test_retryable_5xx_consumes_one_count_across_retries() -> None:
    # UpstreamModelError injects a 503, which the client's retry loop WOULD
    # retry. ``times`` counts logical calls, not transport sends: request-identity
    # dedup means the whole retry sequence of one logical call consumes exactly
    # one count, so a second logical call still passes through.
    donkey = _donkey(DonkeyConfig(max_retries=2))
    donkey._http._swap_transport(_sentinel())
    async with donkey:
        with donkey.simulate(UpstreamModelError, times=1):
            r1 = await _post(donkey)  # 503 through every retry — one logical call
            r2 = await _post(donkey)  # second logical call → wrapped transport
        assert r1.status_code == 503
        assert r2.status_code == 299


async def test_nesting_and_exception_restore_previous_transport() -> None:
    # AC (3): nested simulate() composes, and each block restores the transport
    # it captured — even when the body raises.
    donkey = _donkey()
    original = donkey._http._transport
    async with donkey:
        with donkey.simulate(PIIDetected):
            after_outer = donkey._http._transport
            assert after_outer is not original
            with pytest.raises(RuntimeError):
                with donkey.simulate(AuthError):
                    assert (await _post(donkey)).status_code == 401  # inner wins
                    raise RuntimeError("boom")
            # inner block restored the PII transport despite the exception
            assert donkey._http._transport is after_outer
            assert (await _post(donkey)).status_code == 403
        # outer block restored the original transport
        assert donkey._http._transport is original


async def test_sync_client_swapped_when_already_built() -> None:
    # Sync coverage: a blocking client built BEFORE the block is a target.
    donkey = _donkey()
    sync = donkey._sync_http_client()  # build first
    sync._swap_transport(_sentinel())
    async with donkey:
        with donkey.simulate(PIIDetected):
            assert sync.post(_URL, json={}).status_code == 403
        assert sync.post(_URL, json={}).status_code == 299  # restored


async def test_sync_client_built_inside_block_is_not_retro_swapped() -> None:
    # Documented limitation: a sync client created INSIDE the block was not a
    # target at enter time, so it is not injected into.
    donkey = _donkey()
    assert donkey._sync_http is None
    async with donkey:
        with donkey.simulate(PIIDetected):
            sync = donkey._sync_http_client()
            sync._swap_transport(_sentinel())  # avoid touching the network
            assert sync.post(_URL, json={}).status_code == 299  # NOT injected


async def test_unmapped_donkey_error_raises_value_error() -> None:
    # ConfigError is a DonkeyError that classify() never produces from a gateway
    # response (it is a client-side config failure), so no captured fixture maps
    # back to it; asking to inject it is a clear error, not a silent miss.
    donkey = _donkey()
    async with donkey:
        with pytest.raises(ValueError, match="ConfigError"):
            with donkey.simulate(ConfigError):
                pass


async def test_non_donkey_error_type_is_rejected() -> None:
    donkey = _donkey()
    async with donkey:
        with pytest.raises((TypeError, ValueError)):
            with donkey.simulate(ValueError):  # type: ignore[arg-type]
                pass
