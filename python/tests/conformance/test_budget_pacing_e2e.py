"""End-to-end budget pacing against the local gateway simulator (BG §1.3, #295).

``local_gateway``-marked, so deselected by default and run only via
``pytest -m local_gateway`` (with the ``[local]`` + ``[llm]`` extras installed).

The unit tests (``tests/unit/test_budget_pacing.py``) drive :class:`Budget.pace`
and :meth:`Budget.wait_for_reset` off hand-fed headers. This test closes the loop
over a *real* TCP round-trip: a governed ``openai`` client points at the simulator
running the ``budget`` scenario, and every header the budget object reads is one
the simulator actually emitted on the wire — the numeric ``x-token-*`` trio on the
``429`` boundary (#352/#353), recomputed from its live counter, never fabricated.

It proves the full recover-from-exhaustion arc a developer actually writes:

1. **Exhaust.** Call until the window is spent. The stock ``openai`` client raises
   its *own* ``APIStatusError`` (429) — the SDK never learns about
   :class:`~donkey_kit.core.errors.TokenBudgetExceeded` — yet the governed
   transport's response hook still observed the real ``x-token-*`` headers into
   ``donkey.budget`` before that error surfaced (#185). That in-band freshness,
   even for a caller using the raw client, is the point of the budget object.
2. **Pace trips.** With the window observed as fully used, :meth:`Budget.pace`
   refuses the guarded block *before* issuing a request (#186).
3. **Recover.** :meth:`Budget.wait_for_reset` sleeps until the window rolls over.
4. **Resume.** The next guarded call succeeds and the budget replenishes.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import BudgetReserveReached
from donkey_kit.donkey import Donkey

pytestmark = pytest.mark.local_gateway

# limit=200 with the happy-path fixture's own 68-token cost spends the window in
# three calls, so exhaustion is a handful of round-trips, not hundreds. A short
# window keeps wait_for_reset quick; the recovery loop below tolerates the exact
# rollover instant either way, so the test never races the wall clock.
_BUDGET_SCENARIO = "budget:limit=200,window=2s"
_HAPPY_MODEL = "gpt-5.1"
_DEADLINE_S = 90.0


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def budget_simulator_base_url() -> Iterator[str]:
    """Boot a simulator wired to the ``budget`` scenario on a real ephemeral port.

    Function-scoped (unlike the session-scoped ``simulator_base_url``) so every
    test gets a fresh, unspent window and its own single-use scenario instance
    (scenarios are stateful and single-use — see ``donkey_kit.simulator.scenarios``).
    """
    uvicorn = pytest.importorskip("uvicorn")
    pytest.importorskip("starlette")
    from donkey_kit.simulator import build_app
    from donkey_kit.simulator.app import SimulatorConfig
    from donkey_kit.simulator.scenarios import parse_scenarios

    config = SimulatorConfig(scenarios=parse_scenarios([_BUDGET_SCENARIO]))
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(build_app(config), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5.0)
        raise RuntimeError("budget simulator did not start within 10s")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)


async def test_pace_then_recover_against_a_real_budget_window(
    budget_simulator_base_url: str,
) -> None:
    openai = pytest.importorskip("openai")

    cfg = DonkeyConfig(
        llm_proxy_url=budget_simulator_base_url,
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )
    donkey = Donkey(cfg)
    started = time.monotonic()
    try:
        client = donkey.openai(sync=False)

        # 1. Exhaust the window. The raw client raises its own 429; the governed
        #    transport observed the real x-token-* headers into donkey.budget first.
        for _ in range(50):  # bounded: exhaustion is ~3 calls, the cap only guards a hang
            try:
                await client.responses.create(
                    model=_HAPPY_MODEL, input="ping", max_output_tokens=16
                )
            except openai.APIStatusError as exc:
                assert exc.status_code == 429
                break
        else:  # pragma: no cover - only if the counter never trips
            pytest.fail("budget window never exhausted after 50 calls")

        assert donkey.budget.remaining == 0
        assert donkey.budget.reset_at is not None
        assert donkey.budget.fraction_used == 1.0

        # 2. pace() refuses the guarded block before any request leaves.
        block_ran = False
        with pytest.raises(BudgetReserveReached):
            async with donkey.budget.pace(reserve=0.0):
                block_ran = True  # pragma: no cover - must never execute
        assert block_ran is False

        # 3 + 4. Recover, then resume through pace() — the arc its docstring
        #    prescribes. Looped so the exact rollover instant is never a race;
        #    bounded by the wall-clock ceiling.
        resumed = None
        while time.monotonic() - started < _DEADLINE_S:
            await donkey.budget.wait_for_reset()
            try:
                async with donkey.budget.pace(reserve=0.0):
                    resumed = await client.responses.create(
                        model=_HAPPY_MODEL, input="ping", max_output_tokens=16
                    )
                break
            except BudgetReserveReached:
                continue  # local clock has not quite reached reset_at; wait again
            except openai.APIStatusError as exc:
                assert exc.status_code == 429  # window not yet rolled; wait again
                await asyncio.sleep(0.1)
        assert resumed is not None, "budget never recovered within the deadline"
        assert resumed.object == "response"
        assert donkey.budget.remaining is not None and donkey.budget.remaining > 0

        assert time.monotonic() - started < _DEADLINE_S
    finally:
        await donkey.aclose()
