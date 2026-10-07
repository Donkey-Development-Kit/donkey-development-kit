"""The request-count window, ``donkey.budget.requests`` (#974): filled from the
stock ``rate-limiting`` policy's ``x-ratelimit-*`` trio, and paced alongside the
token window. Headers come from the live captures in
``tests/fixtures/anypoint/request_rate_limit`` (docs/verified-apis.md §4). No
wall-clock sleeping: ``asyncio.sleep`` is recorded and the clock is injected."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from donkey_kit import Budget, BudgetReserveReached, RequestWindow
from donkey_kit.core import budget as budget_mod
from donkey_kit.simulator.fixtures import parse_headers

_FIXED_NOW = datetime(2026, 10, 7, 14, 0, 0, tzinfo=timezone.utc)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "request_rate_limit"


def _captured(name: str) -> httpx.Response:
    return httpx.Response(200, headers=parse_headers((_FIXTURES / name).read_text()))


def _observe_requests(
    b: Budget, *, limit: int, remaining: int, reset_ms: int | None = None
) -> None:
    headers = {"x-ratelimit-limit": str(limit), "x-ratelimit-remaining": str(remaining)}
    if reset_ms is not None:
        headers["x-ratelimit-reset"] = str(reset_ms)
    b.observe(httpx.Response(200, headers=headers), now=_FIXED_NOW)


def _observe_tokens(b: Budget, *, limit: int, remaining: int, reset_ms: int | None) -> None:
    headers = {"x-token-limit": str(limit), "x-token-remaining": str(remaining)}
    if reset_ms is not None:
        headers["x-token-reset"] = str(reset_ms)
    b.observe(httpx.Response(200, headers=headers), now=_FIXED_NOW)


@pytest.fixture
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        calls.append(delay)

    monkeypatch.setattr(budget_mod.asyncio, "sleep", _fake_sleep)
    return calls


# --- observe(): the window fills from the gateway's trio ---------------------


def test_request_window_starts_unobserved() -> None:
    w = Budget().requests
    assert isinstance(w, RequestWindow)
    assert (w.limit, w.remaining, w.reset_at, w.observed_at, w.fraction_used) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_live_200_fills_the_request_window_not_the_token_window() -> None:
    """AC: budget.requests.remaining reflects the gateway count on a 200."""
    b = Budget()
    b.observe(_captured("responses.success.headers.txt"), now=_FIXED_NOW)
    assert b.requests.limit == 3
    assert b.requests.remaining == 2
    assert b.requests.reset_at == _FIXED_NOW + timedelta(milliseconds=53316)
    assert b.requests.observed_at == _FIXED_NOW
    assert b.requests.fraction_used == pytest.approx(1 / 3)
    # The capture has no token-rate-limit policy, so the token window stays cold.
    assert b.observed_at is None


def test_upstream_suffixed_ratelimit_headers_are_not_read() -> None:
    """The capture also carries Azure's ``x-ratelimit-limit-requests: 250``; that
    is the provider's quota, not the gateway's window."""
    b = Budget()
    b.observe(
        httpx.Response(
            200,
            headers={"x-ratelimit-limit-requests": "250", "x-ratelimit-remaining-requests": "249"},
        ),
        now=_FIXED_NOW,
    )
    assert b.requests.observed_at is None


def test_exhausted_window_reports_fully_used() -> None:
    b = Budget()
    b.observe(_captured("responses.window-exhausted.headers.txt"), now=_FIXED_NOW)
    assert b.requests.remaining == 0
    assert b.requests.fraction_used == 1.0


def test_after_reset_capture_refills_the_window() -> None:
    b = Budget()
    b.observe(_captured("responses.window-exhausted.headers.txt"), now=_FIXED_NOW)
    b.observe(_captured("responses.after-reset.headers.txt"), now=_FIXED_NOW)
    assert b.requests.remaining == 2


def test_request_only_response_leaves_token_freshness_untouched() -> None:
    b = Budget()
    _observe_tokens(b, limit=1000, remaining=900, reset_ms=60000)
    later = _FIXED_NOW + timedelta(seconds=5)
    b.observe(
        httpx.Response(200, headers={"x-ratelimit-limit": "3", "x-ratelimit-remaining": "1"}),
        now=later,
    )
    assert b.observed_at == _FIXED_NOW
    assert b.requests.observed_at == later


def test_cache_hit_advances_neither_window() -> None:
    b = Budget()
    b.observe(
        httpx.Response(
            200,
            headers={
                "x-semantic-cache-status": "hit",
                "x-ratelimit-limit": "3",
                "x-ratelimit-remaining": "0",
            },
        ),
        now=_FIXED_NOW,
    )
    assert b.requests.observed_at is None


# --- pace(): either window can refuse ----------------------------------------


async def test_pace_refuses_the_call_past_the_request_limit_without_sending() -> None:
    """AC: pace(reserve=0.0) raises on the call that would exceed the limit, and
    the guarded block (the request) never runs."""
    b = Budget()
    b.observe(_captured("responses.window-exhausted.headers.txt"), now=_FIXED_NOW)
    ran = False
    with pytest.raises(BudgetReserveReached) as exc:
        async with b.pace(reserve=0.0, now=_FIXED_NOW):
            ran = True
    assert not ran
    assert exc.value.window == "requests"
    assert exc.value.fraction_used == 1.0
    assert exc.value.reset_at == b.requests.reset_at
    assert "request window" in str(exc.value)


async def test_pace_allows_while_requests_remain() -> None:
    b = Budget()
    b.observe(_captured("responses.success.headers.txt"), now=_FIXED_NOW)
    ran = False
    async with b.pace(reserve=0.0, now=_FIXED_NOW):
        ran = True
    assert ran


async def test_small_limit_makes_a_fractional_reserve_coarse() -> None:
    """On a 3-request window, 2 of 3 used is 66.7%: reserve=0.30 trips there,
    reserve=0.05 does not (it behaves like 0.0 until the window is spent)."""
    b = Budget()
    _observe_requests(b, limit=3, remaining=1, reset_ms=60000)
    async with b.pace(reserve=0.05, now=_FIXED_NOW):
        pass
    with pytest.raises(BudgetReserveReached):
        async with b.pace(reserve=0.40, now=_FIXED_NOW):
            pass


async def test_expired_request_window_lets_the_call_through() -> None:
    b = Budget()
    _observe_requests(b, limit=3, remaining=0, reset_ms=1000)
    async with b.pace(now=_FIXED_NOW + timedelta(seconds=2)):
        pass


async def test_token_refusal_still_names_the_token_window() -> None:
    b = Budget()
    _observe_tokens(b, limit=1000, remaining=0, reset_ms=60000)
    with pytest.raises(BudgetReserveReached) as exc:
        async with b.pace(now=_FIXED_NOW):
            pass
    assert exc.value.window == "tokens"
    assert "token window" in str(exc.value)


@pytest.mark.parametrize(
    ("token_reset_ms", "request_reset_ms", "expected"),
    [
        (60000, 10000, "tokens"),
        (10000, 60000, "requests"),
        (None, 60000, "tokens"),  # no known reset outranks any time
        (60000, None, "requests"),
    ],
)
async def test_both_windows_spent_names_the_one_releasing_last(
    token_reset_ms: int | None, request_reset_ms: int | None, expected: str
) -> None:
    b = Budget()
    _observe_tokens(b, limit=1000, remaining=0, reset_ms=token_reset_ms)
    _observe_requests(b, limit=3, remaining=0, reset_ms=request_reset_ms)
    with pytest.raises(BudgetReserveReached) as exc:
        async with b.pace(now=_FIXED_NOW):
            pass
    assert exc.value.window == expected


# --- wait_for_reset(): waits on the tripped window ---------------------------


async def test_wait_for_reset_sleeps_to_the_request_reset(recorded_sleeps: list[float]) -> None:
    b = Budget()
    _observe_requests(b, limit=3, remaining=0, reset_ms=30000)
    with pytest.raises(BudgetReserveReached):
        async with b.pace(now=_FIXED_NOW):
            pass
    await b.wait_for_reset(now=_FIXED_NOW)
    assert recorded_sleeps == [30.0]


async def test_explicit_window_overrides_the_last_refusal(recorded_sleeps: list[float]) -> None:
    b = Budget()
    _observe_tokens(b, limit=1000, remaining=500, reset_ms=50000)
    _observe_requests(b, limit=3, remaining=0, reset_ms=20000)
    await b.wait_for_reset(window="requests", now=_FIXED_NOW)
    await b.wait_for_reset(window="tokens", now=_FIXED_NOW)
    assert recorded_sleeps == [20.0, 50.0]


async def test_wait_for_reset_rejects_an_unknown_window() -> None:
    with pytest.raises(ValueError, match="window"):
        await Budget().wait_for_reset(window="dollars")


async def test_a_concurrent_pass_does_not_redirect_a_pending_wait(
    recorded_sleeps: list[float],
) -> None:
    """Task A is refused on the request window; task B then passes pace() (here,
    once the window is refreshed). A's wait_for_reset() must still wait on the
    request window, not fall back to the cold token window and return at once."""
    b = Budget()
    _observe_requests(b, limit=3, remaining=0, reset_ms=30000)
    with pytest.raises(BudgetReserveReached):  # task A
        async with b.pace(now=_FIXED_NOW):
            pass
    _observe_requests(b, limit=3, remaining=1, reset_ms=30000)
    async with b.pace(now=_FIXED_NOW):  # task B
        pass
    await b.wait_for_reset(now=_FIXED_NOW)  # task A recovers
    assert recorded_sleeps == [30.0]


async def test_documented_loop_recovers_from_a_request_refusal(
    recorded_sleeps: list[float],
) -> None:
    """The retry loop from the pace() docstring, with window=exc.window."""
    b = Budget()
    _observe_requests(b, limit=3, remaining=0, reset_ms=5000)
    clock = [_FIXED_NOW]
    attempts = 0
    while True:
        try:
            async with b.pace(now=clock[0]):
                attempts += 1
        except BudgetReserveReached as exc:
            assert exc.reset_at is not None
            await b.wait_for_reset(window=exc.window, now=clock[0])
            clock[0] = exc.reset_at
            continue
        break
    assert attempts == 1
    assert recorded_sleeps == [5.0]
