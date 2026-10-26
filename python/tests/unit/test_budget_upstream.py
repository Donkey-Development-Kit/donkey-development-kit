"""The upstream provider's quota, ``donkey.budget.upstream`` (#1025): the
suffixed ``x-ratelimit-*-requests`` / ``-tokens`` passthrough, read as its own
labelled source with a Go-duration reset, and never mixed into the gateway's
windows. Headers come from the live captures in ``tests/fixtures/anypoint``
(``routes/`` for OpenAI and Bedrock, #894; ``request_rate_limit/`` for Azure
OpenAI behind the gateway's request window, #974; docs/verified-apis.md §4,
"Upstream provider window")."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from donkey_kit import Budget, UpstreamQuota, UpstreamWindow
from donkey_kit.core._budget_headers import parse_go_duration
from donkey_kit.simulator.fixtures import parse_headers

_FIXED_NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=timezone.utc)

_ANYPOINT = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"


def _captured(path: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers=parse_headers((_ANYPOINT / path).read_text()))


# --- parse_go_duration --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "ms"),
    [
        # Every reset value the live captures carry.
        ("12ms", 12.0),
        ("24ms", 24.0),
        ("120ms", 120.0),
        ("1ms", 1.0),
        ("2ms", 2.0),
        ("6ms", 6.0),
        ("0s", 0.0),
        # The rest of Go's grammar: compound, fractional, sub-millisecond.
        ("1m30s", 90_000.0),
        ("6m0s", 360_000.0),
        ("1.5h", 5_400_000.0),
        ("1h2m3.5s", 3_723_500.0),
        (".5s", 500.0),
        ("500us", 0.5),
        ("500µs", 0.5),
        ("250ns", 0.00025),
        (" 12ms ", 12.0),
    ],
)
def test_go_durations_parse_to_milliseconds(raw: str, ms: float) -> None:
    assert parse_go_duration(raw) == pytest.approx(ms)


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "0",  # Azure OpenAI's bare reset: no unit, so no signal
        "250",
        "-1s",  # a time until reset is never negative
        "+1s",
        "1x",
        "ms",
        "1 s",
        "1s garbage",
        "2026-09-24T06:19:44Z",  # Anthropic's native header shape
    ],
)
def test_anything_else_is_no_signal(raw: str | None) -> None:
    assert parse_go_duration(raw) is None


# --- observe(): the upstream source fills on its own ---------------------------


def test_upstream_quota_starts_unobserved() -> None:
    b = Budget()
    assert isinstance(b.upstream, UpstreamQuota)
    for w in (b.upstream.requests, b.upstream.tokens):
        assert isinstance(w, UpstreamWindow)
        assert w.limit is None
        assert w.remaining is None
        assert w.reset_at is None
        assert w.observed_at is None
        assert w.fraction_used is None
        assert w.provider is None


def test_openai_capture_fills_the_upstream_windows_only() -> None:
    """OpenAI's passthrough: 5000/4999 requests, reset ``12ms``; tokens
    2000000/1999990, reset ``0s``."""
    b = Budget()
    b.observe(_captured("routes/openai.chat_completions.success.headers.txt"), now=_FIXED_NOW)

    req = b.upstream.requests
    assert (req.limit, req.remaining) == (5000, 4999)
    assert req.reset_at == _FIXED_NOW + timedelta(milliseconds=12)
    assert req.observed_at == _FIXED_NOW
    assert req.fraction_used == pytest.approx(1 / 5000)
    assert req.provider == "openai"

    tok = b.upstream.tokens
    assert (tok.limit, tok.remaining) == (2_000_000, 1_999_990)
    assert tok.reset_at == _FIXED_NOW
    assert tok.provider == "openai"

    # The gateway's own windows saw nothing: the route has no rate-limit policy.
    assert b.observed_at is None
    assert b.requests.observed_at is None


def test_azure_bare_zero_reset_is_not_read() -> None:
    """Azure OpenAI sends ``x-ratelimit-reset-requests: 0`` with
    ``remaining`` below ``limit``: a unitless value, so ``reset_at`` stays
    ``None`` while the counts are read."""
    b = Budget()
    b.observe(_captured("routes/azureopenai.chat_completions.success.headers.txt"), now=_FIXED_NOW)
    req = b.upstream.requests
    assert (req.limit, req.remaining) == (250, 249)
    assert req.reset_at is None
    assert req.observed_at == _FIXED_NOW
    assert req.provider == "azureopenai"
    assert (b.upstream.tokens.limit, b.upstream.tokens.remaining) == (250_000, 249_987)
    assert b.upstream.tokens.reset_at is None


def test_bedrock_capture_leaves_the_upstream_quota_unobserved() -> None:
    b = Budget()
    b.observe(
        _captured("routes/bedrockanthropic.chat_completions.success.headers.txt"), now=_FIXED_NOW
    )
    assert b.upstream.requests.observed_at is None
    assert b.upstream.tokens.observed_at is None


# --- the two sources never overwrite each other ---------------------------------


def test_one_response_carrying_both_fills_each_window_from_its_own_headers() -> None:
    """The request-rate-limit 200 carries the gateway's ``3/2/29075`` trio and
    Azure's ``250/249/0`` passthrough side by side."""
    b = Budget()
    b.observe(_captured("request_rate_limit/responses.after-reset.headers.txt"), now=_FIXED_NOW)
    assert (b.requests.limit, b.requests.remaining) == (3, 2)
    assert b.requests.reset_at == _FIXED_NOW + timedelta(milliseconds=29075)
    assert (b.upstream.requests.limit, b.upstream.requests.remaining) == (250, 249)
    assert b.upstream.requests.reset_at is None


def test_a_gateway_only_response_leaves_the_upstream_window_alone() -> None:
    """The gateway's own 429 never reaches the upstream, so it has no
    passthrough: it moves ``budget.requests`` to 0 and leaves ``upstream`` as
    the earlier 200 set it."""
    b = Budget()
    b.observe(_captured("request_rate_limit/responses.after-reset.headers.txt"), now=_FIXED_NOW)
    later = _FIXED_NOW + timedelta(seconds=1)
    b.observe(
        _captured("request_rate_limit/reject.request-rate-limit.repeat.headers.txt", 429),
        now=later,
    )
    assert b.requests.remaining == 0
    assert b.requests.observed_at == later
    assert b.upstream.requests.remaining == 249
    assert b.upstream.requests.observed_at == _FIXED_NOW


def test_an_upstream_only_response_leaves_the_gateway_windows_alone() -> None:
    b = Budget()
    b.observe(
        httpx.Response(
            200,
            headers={
                "x-ratelimit-limit": "3",
                "x-ratelimit-remaining": "1",
                "x-token-limit": "1000",
                "x-token-remaining": "400",
            },
        ),
        now=_FIXED_NOW,
    )
    b.observe(
        _captured("routes/openai.chat_completions.success.headers.txt"),
        now=_FIXED_NOW + timedelta(seconds=1),
    )
    assert (b.requests.limit, b.requests.remaining) == (3, 1)
    assert (b.limit, b.remaining) == (1000, 400)
    assert b.requests.observed_at == _FIXED_NOW
    assert b.observed_at == _FIXED_NOW


# --- replacement and ordering ----------------------------------------------------


def test_an_observation_from_another_upstream_replaces_the_whole_window() -> None:
    """A routed proxy can switch upstreams between calls: Azure's unread reset
    must not inherit OpenAI's."""
    b = Budget()
    b.observe(_captured("routes/openai.chat_completions.success.headers.txt"), now=_FIXED_NOW)
    later = _FIXED_NOW + timedelta(seconds=1)
    b.observe(_captured("routes/azureopenai.chat_completions.success.headers.txt"), now=later)
    req = b.upstream.requests
    assert (req.limit, req.remaining, req.reset_at) == (250, 249, None)
    assert req.provider == "azureopenai"
    assert req.observed_at == later


def test_a_stale_upstream_observation_is_dropped() -> None:
    """#311: the freshest observation wins, not the last to arrive."""
    b = Budget()
    b.observe(_captured("routes/openai.chat_completions.success.headers.txt"), now=_FIXED_NOW)
    b.observe(
        _captured("routes/azureopenai.chat_completions.success.headers.txt"),
        now=_FIXED_NOW - timedelta(seconds=1),
    )
    assert b.upstream.requests.provider == "openai"
    assert b.upstream.requests.limit == 5000


def test_a_cache_hit_does_not_observe_the_upstream() -> None:
    b = Budget()
    b.observe(
        httpx.Response(
            200,
            headers={
                "x-semantic-cache-status": "hit",
                "x-ratelimit-limit-requests": "5000",
                "x-ratelimit-remaining-requests": "4999",
            },
        ),
        now=_FIXED_NOW,
    )
    assert b.upstream.requests.observed_at is None


# --- pace() does not guard on the upstream ---------------------------------------


async def test_pace_lets_a_call_through_an_exhausted_upstream_window() -> None:
    """The upstream quota is the provider key's, shared by every consumer of the
    proxy and dependent on the route, so pace() stays on the gateway's windows."""
    b = Budget()
    b.observe(
        httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-requests": "5000",
                "x-ratelimit-remaining-requests": "0",
                "x-ratelimit-reset-requests": "1m0s",
            },
        ),
        now=_FIXED_NOW,
    )
    assert b.upstream.requests.fraction_used == 1.0
    ran = False
    async with b.pace(reserve=0.0, now=_FIXED_NOW):
        ran = True
    assert ran
