"""Budget pacing: ``pace(reserve=)`` and ``wait_for_reset()`` — the recovery
half of piece 2 (BG §1.3, #186). No wall-clock sleeping: ``asyncio.sleep``
is monkeypatched to record its delay, and the clock is injected via ``now=`` the
same way :meth:`Budget.observe` accepts it, so every assertion is deterministic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from donkey_kit import Budget, BudgetReserveReached
from donkey_kit.core import budget as budget_mod

_FIXED_NOW = datetime(2026, 9, 8, 14, 0, 0, tzinfo=timezone.utc)


def _resp(**headers: str) -> httpx.Response:
    return httpx.Response(200, headers=headers)


def _observe(b: Budget, *, limit: int, remaining: int, reset_ms: int | None = None) -> None:
    headers = {"x-token-limit": str(limit), "x-token-remaining": str(remaining)}
    if reset_ms is not None:
        headers["x-token-reset"] = str(reset_ms)
    b.observe(_resp(**headers), now=_FIXED_NOW)


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace ``asyncio.sleep`` inside budget.py with a recorder so tests never
    actually block, and we can assert exactly how long ``wait_for_reset`` slept."""
    calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        calls.append(delay)

    monkeypatch.setattr(budget_mod.asyncio, "sleep", _fake_sleep)
    return calls


# --- BudgetReserveReached exists and is exported ----------------------------


def test_reserve_reached_is_exported_and_a_donkey_error() -> None:
    from donkey_kit import DonkeyError
    from donkey_kit.core.errors import BudgetReserveReached as CoreReserveReached

    assert BudgetReserveReached is CoreReserveReached
    assert issubclass(BudgetReserveReached, DonkeyError)


def test_reserve_reached_is_not_a_policy_violation() -> None:
    """It is a client-side pre-emptive signal, not a gateway refusal — catching it
    as a PolicyViolation would misclassify it (and it is meant to be recovered from)."""
    from donkey_kit.core.errors import PolicyViolation

    assert not issubclass(BudgetReserveReached, PolicyViolation)


# --- pace(): the pre-emptive guard ------------------------------------------


async def test_pace_allows_when_unobserved() -> None:
    """AC: with an unobserved budget, pace() lets the first request through rather
    than blocking/raising forever."""
    b = Budget()
    ran = False
    async with b.pace(reserve=0.10):
        ran = True
    assert ran


async def test_pace_raises_before_the_body_when_reserve_crossed() -> None:
    """AC: pace() raises BEFORE the guarded request, not after a 429 lands."""
    b = Budget()
    _observe(b, limit=1000, remaining=50, reset_ms=60000)  # 95% used
    ran = False
    with pytest.raises(BudgetReserveReached) as exc:
        async with b.pace(reserve=0.10, now=_FIXED_NOW):  # threshold 90%
            ran = True
    assert ran is False  # the body never executed → no request was issued
    assert exc.value.fraction_used == pytest.approx(0.95)
    assert exc.value.reserve == pytest.approx(0.10)
    assert exc.value.reset_at == _FIXED_NOW + timedelta(seconds=60)


async def test_pace_allows_below_reserve() -> None:
    b = Budget()
    _observe(b, limit=1000, remaining=200)  # 80% used, below the 90% threshold
    ran = False
    async with b.pace(reserve=0.10):
        ran = True
    assert ran


async def test_pace_raises_at_the_exact_boundary() -> None:
    """fraction_used == 1 - reserve is 'reserve reached' (>=, not >)."""
    b = Budget()
    _observe(b, limit=1000, remaining=100)  # exactly 90% used
    with pytest.raises(BudgetReserveReached):
        async with b.pace(reserve=0.10):
            pass


@pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0])
async def test_pace_rejects_reserve_outside_unit_interval(bad: float) -> None:
    b = Budget()
    with pytest.raises(ValueError):
        async with b.pace(reserve=bad):
            pass


async def test_pace_default_reserve_only_fires_at_full_exhaustion() -> None:
    """Default reserve=0.0 → threshold is 100%; a not-quite-full window passes."""
    b = Budget()
    _observe(b, limit=1000, remaining=1)  # 99.9% used, not yet 100%
    async with b.pace():  # reserve defaults to 0.0
        pass  # does not raise
    _observe(b, limit=1000, remaining=0)  # fully exhausted
    with pytest.raises(BudgetReserveReached):
        async with b.pace():
            pass


async def test_pace_defaults_to_wall_clock_after_reset() -> None:
    """Omitting now= uses the real UTC clock, as production callers do."""
    b = Budget()
    observed_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    b.observe(
        _resp(
            **{
                "x-token-limit": "1000",
                "x-token-remaining": "0",
                "x-token-reset": "1000",
            }
        ),
        now=observed_at,
    )

    ran = False
    async with b.pace():
        ran = True
    assert ran


async def test_pace_defaults_to_wall_clock_before_reset() -> None:
    """The real UTC clock keeps the reserve guard armed before reset_at."""
    b = Budget()
    b.observe(
        _resp(
            **{
                "x-token-limit": "1000",
                "x-token-remaining": "0",
                "x-token-reset": "60000",
            }
        )
    )

    with pytest.raises(BudgetReserveReached):
        async with b.pace():
            raise AssertionError("body must not run before the window resets")


# --- wait_for_reset(): the recovery sleep -----------------------------------


async def test_wait_for_reset_sleeps_until_reset_at(recorded_sleeps: list[float]) -> None:
    """AC: sleeps until reset_at and returns; the delay is reset_at - now."""
    b = Budget()
    _observe(b, limit=1000, remaining=0, reset_ms=60000)  # reset_at = now + 60s
    await b.wait_for_reset(now=_FIXED_NOW)
    assert recorded_sleeps == [pytest.approx(60.0)]


async def test_wait_for_reset_unobserved_returns_immediately(recorded_sleeps: list[float]) -> None:
    """AC: unobserved budget → nothing to wait for; returns without sleeping."""
    b = Budget()
    await b.wait_for_reset(now=_FIXED_NOW)
    assert recorded_sleeps == []


async def test_wait_for_reset_never_spins_when_already_past(
    recorded_sleeps: list[float],
) -> None:
    """AC: never spins. A reset_at already in the past returns at once — a single
    (or zero) sleep, never a loop."""
    b = Budget()
    _observe(b, limit=1000, remaining=0, reset_ms=1000)  # reset_at = now + 1s
    later = _FIXED_NOW + timedelta(seconds=10)  # already well past reset_at
    await b.wait_for_reset(now=later)
    assert recorded_sleeps == []  # no negative/zero sleep issued


# --- the full pace → reserve-reached → wait → resume cycle ------------------
# Stand-in for AC #4 (end-to-end against the #187 simulator, deferred): exercises
# the whole recovery loop deterministically, without a live gateway or real time.


async def test_pace_then_wait_then_resume_cycle(recorded_sleeps: list[float]) -> None:
    b = Budget()

    # 1) unobserved → first batch runs
    ran_first = False
    async with b.pace(reserve=0.05):
        ran_first = True
    assert ran_first

    # 2) window nearly exhausted → the next batch is refused before it runs
    _observe(b, limit=20000, remaining=500, reset_ms=60000)  # 97.5% used
    with pytest.raises(BudgetReserveReached):
        async with b.pace(reserve=0.05, now=_FIXED_NOW):  # threshold 95%
            raise AssertionError("body must not run once the reserve is reached")

    # 3) wait for the window, then a fresh window lets work resume
    await b.wait_for_reset(now=_FIXED_NOW)
    assert recorded_sleeps == [pytest.approx(60.0)]
    _observe(b, limit=20000, remaining=20000, reset_ms=60000)  # reset happened
    ran_after = False
    async with b.pace(reserve=0.05, now=_FIXED_NOW + timedelta(seconds=60)):
        ran_after = True
    assert ran_after


async def test_pace_allows_probe_after_wait_without_manual_observe(
    recorded_sleeps: list[float],
) -> None:
    """Regression #452: the documented wait-and-retry loop must make progress
    without injecting the fresh observation that only the guarded request can produce."""
    b = Budget()
    _observe(b, limit=20000, remaining=500, reset_ms=60000)  # 97.5% used

    with pytest.raises(BudgetReserveReached):
        async with b.pace(reserve=0.05, now=_FIXED_NOW):
            raise AssertionError("body must not run before the window resets")

    await b.wait_for_reset(now=_FIXED_NOW)
    assert recorded_sleeps == [pytest.approx(60.0)]

    ran_probe = False
    async with b.pace(reserve=0.05, now=_FIXED_NOW + timedelta(seconds=60)):
        ran_probe = True
    assert ran_probe


async def test_signal_free_response_leaves_expired_window_open() -> None:
    """BG §1.3: once reset_at has elapsed, a response with no budget signal
    leaves the stale pass-through open rather than restoring the old refusal."""
    b = Budget()
    _observe(b, limit=20000, remaining=500, reset_ms=1000)  # 97.5% used
    later = _FIXED_NOW + timedelta(seconds=2)

    b.observe(_resp(), now=later)

    ran = False
    async with b.pace(reserve=0.05, now=later):
        ran = True
    assert ran


async def test_future_reset_at_rearms_expired_window() -> None:
    """BG §1.3: a recognised signal with a future reset_at makes the reserve
    guard active again after the previous observation expired."""
    b = Budget()
    _observe(b, limit=20000, remaining=500, reset_ms=1000)  # 97.5% used
    later = _FIXED_NOW + timedelta(seconds=2)

    async with b.pace(reserve=0.05, now=later):
        pass

    b.observe(
        _resp(
            **{
                "x-token-limit": "20000",
                "x-token-remaining": "500",
                "x-token-reset": "60000",
            }
        ),
        now=later,
    )

    with pytest.raises(BudgetReserveReached):
        async with b.pace(reserve=0.05, now=later):
            pass
