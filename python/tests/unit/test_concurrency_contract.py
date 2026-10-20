"""The concurrency contract for the two pieces of mutable per-``Donkey`` state,
the ``Budget`` and the run correlation ID, under the parallel fan-out agent
frameworks produce (#311, BG §1.3/§1.7).

``Budget``: the freshest observation wins, ordered by when each response's
headers arrived, never by when its hooks ran; ``pace()`` decides on one
snapshot. Correlation: one ID for a fan-out inside ``donkey.run()``, one per
call outside it. No network: every response comes from ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit._testing import swap_transport
from donkey_kit.core.budget import Budget
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import BudgetReserveReached
from donkey_kit.core.transport import DonkeyAsyncClient

_T0 = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
_FANOUT = 12
_CHAT = {
    "id": "c",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
}


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


def _tokens(remaining: int, limit: int = 1000, reset_ms: int = 60_000) -> httpx.Response:
    return httpx.Response(
        200,
        headers={
            "x-token-limit": str(limit),
            "x-token-remaining": str(remaining),
            "x-token-reset": str(reset_ms),
        },
    )


def _requests(remaining: int, limit: int = 10) -> httpx.Response:
    return httpx.Response(
        200,
        headers={
            "x-ratelimit-limit": str(limit),
            "x-ratelimit-remaining": str(remaining),
            "x-ratelimit-reset": "60000",
        },
    )


# --- Budget ordering: the freshest observation wins --------------------------


def test_a_stale_observation_arriving_late_does_not_move_the_budget_backwards() -> None:
    b = Budget()
    b.observe(_tokens(200), now=_T0 + timedelta(seconds=2))
    b.observe(_tokens(900), now=_T0 + timedelta(seconds=1))  # older, lands later
    assert b.remaining == 200
    assert b.fraction_used == 0.8
    assert b.observed_at == _T0 + timedelta(seconds=2)


def test_a_newer_observation_applies_including_a_window_that_reset() -> None:
    b = Budget()
    b.observe(_tokens(100), now=_T0)
    b.observe(_tokens(1000), now=_T0 + timedelta(minutes=2))  # the window reset
    assert b.remaining == 1000
    assert b.reset_at == _T0 + timedelta(minutes=3)


def test_each_window_is_ordered_on_its_own() -> None:
    """A response carrying only the request trio is the first observation of
    that window, whatever the token window has already seen."""
    b = Budget()
    b.observe(_tokens(500), now=_T0 + timedelta(seconds=5))
    b.observe(_requests(3), now=_T0 + timedelta(seconds=1))
    assert b.requests.remaining == 3
    assert b.remaining == 500


def test_the_transport_stamp_orders_observations_not_the_call_to_observe() -> None:
    """Through a governed transport each response carries when its headers
    arrived; ``observe()`` called later, in any order, honours that stamp."""
    fresh, stale = _tokens(100), _tokens(700)
    stale.extensions["donkey_received_at"] = _T0
    fresh.extensions["donkey_received_at"] = _T0 + timedelta(milliseconds=5)
    b = Budget()
    b.observe(fresh)
    b.observe(stale)
    assert b.remaining == 100


class _SlowHookClient(DonkeyAsyncClient):
    """A subclass whose response hook is slower the fresher the response is, so
    hooks finish in the reverse of arrival order: the worst case for a budget
    that trusted the order ``observe()`` runs in."""

    seen: list[float]
    order: list[int]

    async def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        # Answers land 3 ms apart; a hook 9 ms slower per step fresher runs
        # them in exactly the reverse order.
        index = int(response.headers["x-call-index"])
        await asyncio.sleep(0.009 * (_FANOUT - index))
        await super()._on_response(request, response)
        self.order.append(index)
        assert self._budget is not None
        used = self._budget.fraction_used
        assert used is not None
        self.seen.append(used)


async def test_a_fan_out_whose_hooks_finish_out_of_order_keeps_the_freshest_budget() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        index, calls = calls, calls + 1
        # Staggered so the gateway answers in call order, each spending more.
        await asyncio.sleep(0.003 * index)
        response = _tokens(1000 - 50 * (index + 1))
        response.headers["x-call-index"] = str(index)
        return response

    budget = Budget()
    client = _SlowHookClient(
        DonkeyConfig(), None, budget=budget, transport=httpx.MockTransport(handler)
    )
    client.seen, client.order = [], []
    async with client:
        await asyncio.gather(*(client.get("https://proxy/x") for _ in range(_FANOUT)))

    assert budget.remaining == 1000 - 50 * _FANOUT  # the last answer, not the last hook
    assert client.seen == sorted(client.seen)  # fraction_used never moved backwards
    assert client.order == sorted(client.order, reverse=True)  # hooks really ran reversed


def test_threads_observing_at_once_leave_one_consistent_freshest_state() -> None:
    """The sync client feeds one Budget from its callers' threads. Whatever the
    interleaving, the state is the freshest response's, never a mix of two."""
    stamps = [_T0 + timedelta(milliseconds=i) for i in range(200)]
    responses = []
    for i, stamp in enumerate(stamps):
        response = _tokens(remaining=i, limit=1000 + i)  # limit and remaining pair up
        response.extensions["donkey_received_at"] = stamp
        responses.append(response)
    random.Random(311).shuffle(responses)
    b = Budget()
    torn: list[tuple[int, int]] = []
    stop = threading.Event()

    def read() -> None:
        while not stop.is_set():
            try:
                asyncio.run(_enter(b, reserve=1.0))
            except BudgetReserveReached as exc:
                limit, remaining = b.limit, b.remaining
                if exc.fraction_used is None or limit is None or remaining is None:
                    continue
                if limit - remaining != 1000:
                    torn.append((limit, remaining))

    with ThreadPoolExecutor(max_workers=8) as pool:
        reader = pool.submit(read)
        list(pool.map(b.observe, responses))
        stop.set()
        reader.result()

    assert (b.limit, b.remaining) == (1199, 199)
    assert b.observed_at == stamps[-1]
    assert torn == []


async def _enter(b: Budget, *, reserve: float) -> None:
    async with b.pace(reserve=reserve):
        pass


# --- pace(): one snapshot, an admission check rather than a reservation -------


async def test_tasks_entering_pace_together_all_get_the_same_refusal() -> None:
    b = Budget()
    now = datetime.now(timezone.utc)
    b.observe(_tokens(50), now=now)  # 95% used, resets in a minute
    results = await asyncio.gather(
        *(_enter(b, reserve=0.10) for _ in range(_FANOUT)), return_exceptions=True
    )
    assert all(isinstance(r, BudgetReserveReached) for r in results)
    refusals = [r for r in results if isinstance(r, BudgetReserveReached)]
    assert {(r.window, r.fraction_used, r.reset_at) for r in refusals} == {
        ("tokens", 0.95, now + timedelta(minutes=1))
    }


async def test_tasks_entering_pace_together_before_any_response_all_pass() -> None:
    """The stated limit: pace() cannot see what in-flight calls will spend, so a
    fan-out that enters together is admitted together. ``reserve`` must cover it."""
    b = Budget()
    b.observe(_tokens(200), now=datetime.now(timezone.utc))  # 80% used
    entered = 0

    async def call() -> None:
        nonlocal entered
        async with b.pace(reserve=0.10):
            entered += 1
            await asyncio.sleep(0)

    await asyncio.gather(*(call() for _ in range(_FANOUT)))
    assert entered == _FANOUT


# --- correlation: one ID per run, one per call outside a run ----------------


def _correlation_ids(donkey: Donkey) -> list[str]:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["X-Correlation-Id"])
        await asyncio.sleep(0)
        return httpx.Response(200, json=_CHAT, headers=dict(_tokens(500).headers))

    swap_transport(donkey._http, httpx.MockTransport(handler))
    return seen


async def test_a_fan_out_inside_a_run_sends_exactly_one_correlation_id() -> None:
    """The sketch in #311: a raw client fan-out under ``donkey.run()``. The
    state lives on ``Donkey``, so every adapter built on it gets the same."""
    pytest.importorskip("openai")
    donkey = Donkey(_cfg())
    seen = _correlation_ids(donkey)
    client = donkey.llm.client()
    try:
        async with donkey.run(id="run-311"):
            await asyncio.gather(
                *(
                    client.chat.completions.create(
                        model="m", messages=[{"role": "user", "content": str(i)}]
                    )
                    for i in range(_FANOUT)
                )
            )
    finally:
        await donkey.aclose()
    assert seen == ["run-311"] * _FANOUT
    assert donkey.budget.fraction_used == 0.5


async def test_a_fan_out_outside_a_run_sends_one_id_per_call() -> None:
    """Without a scope nothing is grouped, whichever task happened to call first."""
    donkey = Donkey(_cfg())
    seen = _correlation_ids(donkey)
    try:
        await asyncio.gather(*(donkey._http.get("https://proxy/x") for _ in range(_FANOUT)))
    finally:
        await donkey.aclose()
    assert len(seen) == _FANOUT
    assert len(set(seen)) == _FANOUT


async def test_a_child_scope_never_leaks_to_its_parent_or_siblings() -> None:
    donkey = Donkey(_cfg())
    seen = _correlation_ids(donkey)

    async def child(i: int) -> None:
        if i == 0:
            async with donkey.run(id="child"):
                await donkey._http.get("https://proxy/x")
        else:
            await donkey._http.get("https://proxy/x")

    try:
        async with donkey.run(id="parent"):
            await asyncio.gather(*(child(i) for i in range(_FANOUT)))
            await donkey._http.get("https://proxy/x")
    finally:
        await donkey.aclose()
    assert sorted(seen) == sorted(["child"] + ["parent"] * _FANOUT)


async def test_a_task_spawned_in_a_run_keeps_its_id_after_the_block_exits() -> None:
    donkey = Donkey(_cfg())
    seen = _correlation_ids(donkey)
    release = asyncio.Event()

    async def late() -> None:
        await release.wait()
        await donkey._http.get("https://proxy/x")

    try:
        async with donkey.run(id="run-late"):
            task = asyncio.create_task(late())
        release.set()
        await task
    finally:
        await donkey.aclose()
    assert seen == ["run-late"]
