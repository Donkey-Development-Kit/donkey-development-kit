"""Budget: the first-class object parsed from the LLM proxy's budget headers —
the numeric ``x-token-*`` trio (BG §1.3, #185) and the prose
``x-llm-proxy-ratelimit`` fallback that is the only budget signal on a live 200
(#352). No network is touched — headers are attached to constructed
``httpx.Response`` objects, and the transport wiring is exercised via
``httpx.MockTransport``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from donkey_kit import Budget, Donkey
from donkey_kit.core._window import Window, record
from donkey_kit.core.budget import Budget as CoreBudget
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient
from donkey_kit.simulator.fixtures import parse_headers

_FIXED_NOW = datetime(2026, 9, 8, 14, 0, 0, tzinfo=timezone.utc)

_FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "donkey_kit"
    / "simulator"
    / "_fixtures"
    / "anypoint"
    / "llm_proxy"
)


def _resp(status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, headers=headers)


def _resp_from_fixture(name: str, status: int = 200) -> httpx.Response:
    """A response carrying the exact headers of a committed live capture."""
    return httpx.Response(status, headers=parse_headers((_FIXTURES / name).read_text()))


# --- the object in isolation ----------------------------------------------


def test_budget_starts_unobserved() -> None:
    b = Budget()
    assert b.limit is None
    assert b.remaining is None
    assert b.reset_at is None
    assert b.observed_at is None
    assert b.fraction_used is None  # unobserved, not zero — nothing seen yet


def test_reset_ms_converts_to_reset_at_to_the_second() -> None:
    """AC: x-token-reset is milliseconds *to* reset (a delta, docs/verified-apis.md §4), so
    reset_at = observed_at + that delta. Injected clock makes it exact."""
    b = Budget()
    b.observe(
        _resp(**{"x-token-limit": "1000", "x-token-remaining": "250", "x-token-reset": "60000"}),
        now=_FIXED_NOW,
    )
    assert b.limit == 1000
    assert b.remaining == 250
    assert b.observed_at == _FIXED_NOW
    assert b.reset_at == _FIXED_NOW + timedelta(seconds=60)


def test_fraction_used_math() -> None:
    b = Budget()
    b.observe(_resp(**{"x-token-limit": "1000", "x-token-remaining": "250"}), now=_FIXED_NOW)
    assert b.fraction_used == 0.75


def test_fraction_used_guards_zero_limit() -> None:
    b = Budget()
    b.observe(_resp(**{"x-token-limit": "0", "x-token-remaining": "0"}), now=_FIXED_NOW)
    assert b.fraction_used is None  # no division by zero


def test_fraction_used_clamped_to_unit_interval() -> None:
    b = Budget()
    # remaining above limit (a window that reset between calls) must not go negative
    b.observe(_resp(**{"x-token-limit": "100", "x-token-remaining": "150"}), now=_FIXED_NOW)
    assert b.fraction_used == 0.0


def test_missing_headers_leave_object_unobserved_and_do_not_raise() -> None:
    """AC: a response with no x-token-* headers is a defined no-op — observed_at
    stays None so nobody mistakes 'never seen' for 'seen, empty'."""
    b = Budget()
    b.observe(_resp(200), now=_FIXED_NOW)  # a happy-path response without budget headers
    assert b.limit is None
    assert b.remaining is None
    assert b.reset_at is None
    assert b.observed_at is None
    assert b.fraction_used is None


def test_partial_headers_update_only_what_is_present() -> None:
    b = Budget()
    b.observe(
        _resp(**{"x-token-limit": "1000", "x-token-remaining": "800", "x-token-reset": "30000"}),
        now=_FIXED_NOW,
    )
    later = _FIXED_NOW + timedelta(seconds=5)
    # A later response carrying only remaining: limit/reset_at persist, observed_at moves.
    b.observe(_resp(**{"x-token-remaining": "600"}), now=later)
    assert b.limit == 1000  # persisted
    assert b.remaining == 600  # updated
    assert b.reset_at == _FIXED_NOW + timedelta(seconds=30)  # persisted
    assert b.observed_at == later  # freshness moved


def test_non_numeric_headers_are_ignored_not_fatal() -> None:
    b = Budget()
    b.observe(_resp(**{"x-token-limit": "lots", "x-token-remaining": "500"}), now=_FIXED_NOW)
    assert b.limit is None  # garbage ignored
    assert b.remaining == 500  # the parseable one still landed
    assert b.observed_at == _FIXED_NOW


# --- prose x-llm-proxy-ratelimit fallback (#352) ---------------------------
#
# The numeric x-token-* trio arrives only on the 429. On a live 200 (and the 403
# refusal) the same three values arrive as one prose header. Without parsing it,
# Budget could only ever populate from a rate-limit rejection.

_PROSE = "Token rate limit: 10000 tokens remaining of 10000 limit. Reset in 56711ms."


def test_prose_ratelimit_populates_when_numeric_trio_absent() -> None:
    """AC: observe() fills limit/remaining/reset_at/observed_at from
    x-llm-proxy-ratelimit when x-token-* is absent — the live-200 case."""
    b = Budget()
    b.observe(_resp(200, **{"x-llm-proxy-ratelimit": _PROSE}), now=_FIXED_NOW)
    assert b.limit == 10000
    assert b.remaining == 10000
    assert b.observed_at == _FIXED_NOW
    assert b.reset_at == _FIXED_NOW + timedelta(milliseconds=56711)
    assert b.fraction_used == 0.0  # full window, nothing used yet


def test_numeric_trio_wins_when_both_present() -> None:
    """AC: the numeric x-token-* trio still wins when both shapes are present.
    The prose here reports a full window; the numeric trio reports it drained."""
    b = Budget()
    b.observe(
        _resp(
            200,
            **{
                "x-token-limit": "10000",
                "x-token-remaining": "250",
                "x-token-reset": "1000",
                "x-llm-proxy-ratelimit": _PROSE,  # says 10000 remaining — must not win
            },
        ),
        now=_FIXED_NOW,
    )
    assert b.limit == 10000
    assert b.remaining == 250  # numeric, not the prose's 10000
    assert b.reset_at == _FIXED_NOW + timedelta(milliseconds=1000)  # numeric, not 56711
    assert b.fraction_used == 0.975


def test_prose_fills_only_fields_the_numeric_trio_leaves_unset() -> None:
    """Field-by-field merge: a numeric field present wins; a numeric field absent
    is filled from the prose. (A mixed response is not seen live, but the merge
    must be well-defined.)"""
    b = Budget()
    b.observe(
        _resp(200, **{"x-token-remaining": "42", "x-llm-proxy-ratelimit": _PROSE}),
        now=_FIXED_NOW,
    )
    assert b.limit == 10000  # from prose (numeric absent)
    assert b.remaining == 42  # numeric wins
    assert b.reset_at == _FIXED_NOW + timedelta(milliseconds=56711)  # from prose


def test_unparseable_prose_is_a_noop_and_never_raises() -> None:
    """AC: an unparseable sentence is a no-op, per verification discipline — never a crash, never a
    half-populated budget."""
    b = Budget()
    b.observe(_resp(200, **{"x-llm-proxy-ratelimit": "rate limited, try later"}), now=_FIXED_NOW)
    assert b.limit is None
    assert b.remaining is None
    assert b.reset_at is None
    assert b.observed_at is None  # nothing observed


def test_partial_prose_is_a_noop() -> None:
    """AC: a partial sentence (missing the reset clause) is discarded whole — a
    half-parsed budget is worse than none, so observed_at stays None."""
    b = Budget()
    partial = "Token rate limit: 10000 tokens remaining of 10000 limit."  # no 'Reset in …ms'
    b.observe(_resp(200, **{"x-llm-proxy-ratelimit": partial}), now=_FIXED_NOW)
    assert b.limit is None
    assert b.remaining is None
    assert b.reset_at is None
    assert b.observed_at is None


# --- an out-of-range reset is no signal, never a crash (#1444) -------------

# A reset no ``datetime`` can hold.
_HUGE_RESET_MS = [
    "9" * 25,  # the issue's reproduction: past what a timedelta holds
    "100000000000000000",  # fits a timedelta; reset_at lands past datetime.max
    "1730000000000000",  # an epoch timestamp in microseconds, read as a ms delta
]
# A time until reset is never negative.
_NEGATIVE_RESET_MS = ["-" + "9" * 25, "-5000", "-1"]
_BAD_RESET_MS = _HUGE_RESET_MS + _NEGATIVE_RESET_MS
# Not an int at all: parse_int already reads each as no value.
_NOT_A_NUMBER = ["nan", "inf", "-inf", "1e400", "Infinity"]


@pytest.mark.parametrize("raw", _BAD_RESET_MS + _NOT_A_NUMBER)
@pytest.mark.parametrize(
    ("limit", "remaining", "reset"),
    [
        ("x-token-limit", "x-token-remaining", "x-token-reset"),
        ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset"),
    ],
    ids=["tokens", "requests"],
)
def test_an_out_of_range_numeric_reset_is_ignored(
    raw: str, limit: str, remaining: str, reset: str
) -> None:
    """The counts still apply; only the reset is dropped."""
    b = Budget()
    b.observe(_resp(**{limit: "1000", remaining: "250", reset: raw}), now=_FIXED_NOW)
    window = b if reset == "x-token-reset" else b.requests
    assert (window.limit, window.remaining) == (1000, 250)
    assert window.reset_at is None
    assert window.observed_at == _FIXED_NOW


@pytest.mark.parametrize("raw", _HUGE_RESET_MS)
def test_an_out_of_range_prose_reset_is_ignored(raw: str) -> None:
    """The prose regex takes unsigned digits, so only a huge value reaches the window."""
    prose = f"Token rate limit: 42 tokens remaining of 100 limit. Reset in {raw}ms."
    b = Budget()
    b.observe(_resp(**{"x-llm-proxy-ratelimit": prose}), now=_FIXED_NOW)
    assert (b.limit, b.remaining) == (100, 42)
    assert b.reset_at is None


@pytest.mark.parametrize("raw", _BAD_RESET_MS)
def test_a_response_whose_only_signal_is_a_bad_reset_observes_nothing(raw: str) -> None:
    b = Budget()
    b.observe(_resp(**{"x-token-reset": raw, "x-ratelimit-reset": raw}), now=_FIXED_NOW)
    assert b.observed_at is None
    assert b.requests.observed_at is None


def test_a_bad_reset_keeps_the_reset_already_observed() -> None:
    """A field that fails to parse leaves that field as it was, as an absent one does."""
    b = Budget()
    b.observe(_resp(**{"x-token-limit": "1000", "x-token-reset": "60000"}), now=_FIXED_NOW)
    later = _FIXED_NOW + timedelta(seconds=1)
    b.observe(_resp(**{"x-token-remaining": "10", "x-token-reset": "9" * 25}), now=later)
    assert b.remaining == 10
    assert b.observed_at == later
    assert b.reset_at == _FIXED_NOW + timedelta(milliseconds=60000)


@pytest.mark.parametrize(
    "reset_ms",
    [float("nan"), float("inf"), float("-inf"), -0.5, 10**400, -(10**400)],
    ids=["nan", "inf", "-inf", "negative", "huge", "-huge"],
)
def test_record_ignores_a_non_finite_or_out_of_range_reset(reset_ms: float) -> None:
    """``record()`` is the guard every window shares, so it holds for any float a
    parser hands it, including the ``inf`` a Go duration with 400 digits sums to."""
    window = Window()
    assert record(window, None, None, reset_ms, _FIXED_NOW) is False
    assert window.observed_at is None
    assert record(window, 5, None, reset_ms, _FIXED_NOW) is True
    assert (window.limit, window.reset_at) == (5, None)


@pytest.mark.parametrize(
    ("limit", "remaining", "used"),
    [(1, -(10**400), 1.0), (10**400, 0, 1.0), (10**400, 10**400, 0.0), (4, 10**400, 0.0)],
    ids=["huge-negative-remaining", "huge-limit-spent", "huge-limit-unspent", "huge-remaining"],
)
def test_fraction_used_clamps_before_it_divides(limit: int, remaining: int, used: float) -> None:
    window = Window()
    window.limit, window.remaining = limit, remaining
    assert window.fraction_used == used


def test_record_still_takes_a_zero_and_the_latest_representable_reset() -> None:
    window = Window()
    record(window, None, None, 0, _FIXED_NOW)
    assert window.reset_at == _FIXED_NOW
    edge = (datetime.max.replace(tzinfo=timezone.utc) - _FIXED_NOW) // timedelta(milliseconds=1)
    record(window, None, None, edge - 1000, _FIXED_NOW)
    assert window.reset_at is not None and window.reset_at.year == 9999


def test_observes_from_committed_pii_fixture() -> None:
    """AC: regression against the committed reject.pii-detected.headers.txt, which
    already carries the prose header on a 403 refusal (`1 … of 1 limit`)."""
    b = Budget()
    b.observe(_resp_from_fixture("reject.pii-detected.headers.txt", status=403), now=_FIXED_NOW)
    assert b.limit == 1
    assert b.remaining == 1
    assert b.reset_at == _FIXED_NOW + timedelta(milliseconds=60000)
    assert b.observed_at == _FIXED_NOW


def test_observes_from_committed_success_policy_fixture() -> None:
    """AC: regression against the committed live 200-with-policy-applied capture —
    the case that was previously an unobserved no-op."""
    b = Budget()
    b.observe(_resp_from_fixture("responses.success.policy-applied.headers.txt"), now=_FIXED_NOW)
    assert b.limit == 10000
    assert b.remaining == 10000
    assert b.reset_at == _FIXED_NOW + timedelta(milliseconds=56711)
    assert b.observed_at == _FIXED_NOW


def test_upstream_openai_ratelimit_headers_are_not_mistaken_for_budget() -> None:
    """The passed-through x-ratelimit-* set is OpenAI's own quota, not the gateway
    window. Absent the gateway's headers, the budget stays unobserved rather than
    picking up the upstream numbers (whose resets are Go durations like `0s`)."""
    b = Budget()
    b.observe(
        _resp(
            200,
            **{
                "x-ratelimit-limit-tokens": "4000000",
                "x-ratelimit-remaining-tokens": "3999963",
                "x-ratelimit-reset-tokens": "0s",
            },
        ),
        now=_FIXED_NOW,
    )
    assert b.observed_at is None


def test_top_level_export_is_the_core_object() -> None:
    assert Budget is CoreBudget


# --- transport wiring: _on_response feeds the budget -----------------------


async def test_async_client_updates_attached_budget_on_response() -> None:
    b = Budget()

    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(
            **{"x-token-limit": "1000", "x-token-remaining": "900", "x-token-reset": "1000"}
        )

    client = DonkeyAsyncClient(
        DonkeyConfig(), None, budget=b, transport=httpx.MockTransport(handler)
    )
    async with client:
        await client.get("https://x")
    assert b.limit == 1000
    assert b.remaining == 900
    assert b.observed_at is not None  # stamped by the real clock in the hook


async def test_async_client_without_budget_is_a_noop_seam() -> None:
    """No budget attached → the hook stays byte-identical to a hookless client."""
    client = DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(lambda r: _resp(200))
    )
    async with client:
        resp = await client.get("https://x")
    assert resp.status_code == 200  # nothing raised, nothing to observe


def test_sync_client_updates_attached_budget_on_response() -> None:
    b = Budget()

    def handler(request: httpx.Request) -> httpx.Response:
        return _resp(**{"x-token-limit": "500", "x-token-remaining": "100"})

    client = DonkeyClient(DonkeyConfig(), budget=b, transport=httpx.MockTransport(handler))
    with client:
        client.get("https://x")
    assert b.limit == 500
    assert b.remaining == 100
    assert b.fraction_used == 0.8


# --- Donkey-level: budget is per-instance ----------------------------------


def test_donkey_exposes_a_budget() -> None:
    donkey = Donkey(DonkeyConfig())
    assert isinstance(donkey.budget, Budget)
    assert donkey.budget.observed_at is None  # unobserved until the first call returns
    assert donkey._http._budget is donkey.budget  # the shared client feeds this object


def test_two_donkeys_never_share_budget_state() -> None:
    """AC: per-Donkey, not global. Two instances with different credentials must
    not share budget state."""
    f1 = Donkey(DonkeyConfig(llm_proxy_client_id="a"))
    f2 = Donkey(DonkeyConfig(llm_proxy_client_id="b"))
    assert f1.budget is not f2.budget
    f1.budget.observe(_resp(**{"x-token-limit": "1000", "x-token-remaining": "1"}), now=_FIXED_NOW)
    assert f2.budget.remaining is None  # untouched


async def test_donkey_budget_updates_through_the_shared_client() -> None:
    donkey = Donkey(DonkeyConfig())
    donkey._http.governed_transport.replace_inner(
        httpx.MockTransport(
            lambda r: _resp(**{"x-token-limit": "2000", "x-token-remaining": "1500"})
        )
    )
    async with donkey._http as client:
        await client.get("https://proxy/chat/completions")
    assert donkey.budget.limit == 2000
    assert donkey.budget.remaining == 1500
    assert donkey.budget.fraction_used == 0.25
