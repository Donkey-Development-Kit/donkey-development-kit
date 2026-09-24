"""Semantic-cache steering and surfacing (docs/verified-apis.md §2, BG §1.1, #587).

Base-only surface (Surface 1): httpx + fixtures, no framework import. The feature
spans several ``core/`` modules but is one capability, so its tests live together:

  * ``CacheControls`` validates every set control at construction (verification discipline);
  * ``donkey.cache(...)`` binds the controls to a contextvar for a block, dual
    sync/async, and the transport injects the VERIFIED lowercase ``x-cache-*``
    request headers on the one shared injection seam (async AND sync);
  * ``last_call`` surfaces the gateway's outcome — ``cache_status`` / ``cache_score``
    / ``cache_hit`` — parsed from the LIVE-captured response headers for all four
    states (fixture-driven, Surface 3);
  * a cache ``hit`` never advances the budget (verbatim replay, no fresh spend, #588);
  * the outcome lands on the OTel span (``donkey.cache.*``).

Verification note: the five ``x-cache-*`` names are ``verified=True`` in
``_verify`` (confirmed live, #588), so injecting them emits NO
``UnverifiedValueWarning`` — asserted below.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import httpx
import pytest

from donkey_kit import CacheControls, Donkey
from donkey_kit.core import lastcall, telemetry
from donkey_kit.core._verify import (
    CACHE_NO_STORE_HEADER,
    CACHE_PRINCIPAL_ID_HEADER,
    CACHE_SKIP_HEADER,
    CACHE_THRESHOLD_HEADER,
    CACHE_TTL_HEADER,
    UnverifiedValueWarning,
)
from donkey_kit.core.budget import Budget
from donkey_kit.core.cachecontrol import (
    CacheScope,
    cache_scope,
    current_cache_controls,
)
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.lastcall import (
    LastCall,
    LastCallStatus,
    is_cache_hit,
    semantic_cache,
)
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient
from donkey_kit.simulator.fixtures import parse_headers

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "semantic_cache"


def _resp_from_fixture(name: str, status: int = 200) -> httpx.Response:
    """A response replaying a LIVE-captured semantic-cache header dump (Surface 3)."""
    return httpx.Response(status, headers=parse_headers((_FIXTURES / name).read_text()))


# ---------------------------------------------------------------------------
# CacheControls — construction validates every set control (verification discipline)
# ---------------------------------------------------------------------------


def test_empty_controls_is_empty() -> None:
    assert CacheControls().is_empty
    assert not CacheControls(skip=True).is_empty
    assert not CacheControls(ttl=60).is_empty


@pytest.mark.parametrize("field", ["skip", "no_store"])
def test_boolean_controls_reject_non_bool(field: str) -> None:
    # A number where a bool is meant is a config error, not silently truthy-cast.
    with pytest.raises(ConfigError):
        CacheControls(**{field: 1})  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [0, -5, 1.5, True, False, "60"])
def test_ttl_must_be_a_positive_int(bad: object) -> None:
    # bool subclasses int, so True/False must be rejected too (not ttl=1).
    with pytest.raises(ConfigError):
        CacheControls(ttl=bad)  # type: ignore[arg-type]


def test_ttl_accepts_a_positive_int() -> None:
    assert CacheControls(ttl=60).ttl == 60


@pytest.mark.parametrize("bad", [-0.1, 1.1, True, "0.5"])
def test_threshold_must_be_a_number_in_unit_interval(bad: object) -> None:
    with pytest.raises(ConfigError):
        CacheControls(threshold=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("good", [0.0, 0.5, 1.0])
def test_threshold_accepts_unit_interval(good: float) -> None:
    assert CacheControls(threshold=good).threshold == good


def test_principal_id_rejects_empty() -> None:
    with pytest.raises(ConfigError):
        CacheControls(principal_id="")


def test_principal_id_rejects_overlong() -> None:
    with pytest.raises(ConfigError):
        CacheControls(principal_id="x" * 257)


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b", "a\x7fb"])
def test_principal_id_rejects_control_characters(bad: str) -> None:
    # A newline in a value emitted as a request header is header injection.
    with pytest.raises(ConfigError):
        CacheControls(principal_id=bad)


# ---------------------------------------------------------------------------
# CacheControls.headers() — the wire emission (verification discipline: presence-only bools)
# ---------------------------------------------------------------------------


def test_headers_emit_only_set_controls() -> None:
    controls = CacheControls(skip=True, ttl=60, threshold=0.5, principal_id="u-1")
    emitted = dict(controls.headers())
    assert emitted == {
        CACHE_SKIP_HEADER.get(): "true",
        CACHE_TTL_HEADER.get(): "60",
        CACHE_THRESHOLD_HEADER.get(): "0.5",
        CACHE_PRINCIPAL_ID_HEADER.get(): "u-1",
    }


def test_boolean_headers_are_presence_only() -> None:
    # The live capture only ever sent `true`; a False/None omits the header rather
    # than inventing an untested `x-cache-skip: false` (verification discipline).
    assert dict(CacheControls(skip=False, no_store=False).headers()) == {}
    assert dict(CacheControls(no_store=True).headers()) == {CACHE_NO_STORE_HEADER.get(): "true"}


def test_empty_controls_emit_nothing() -> None:
    assert dict(CacheControls().headers()) == {}


# ---------------------------------------------------------------------------
# CacheScope — the contextvar binding behind donkey.cache(...)
# ---------------------------------------------------------------------------


def test_current_cache_controls_is_none_outside_a_scope() -> None:
    assert current_cache_controls() is None


def test_scope_binds_and_restores_sync() -> None:
    controls = CacheControls(skip=True)
    assert current_cache_controls() is None
    with cache_scope(controls) as bound:
        assert bound is controls
        assert current_cache_controls() is controls
    assert current_cache_controls() is None


def test_empty_scope_is_inert() -> None:
    # A no-argument donkey.cache() binds nothing and leaves an outer scope in place.
    outer = CacheControls(ttl=30)
    with cache_scope(outer):
        with cache_scope(CacheControls()) as inner:
            assert inner is None
            assert current_cache_controls() is outer
        assert current_cache_controls() is outer
    assert current_cache_controls() is None


def test_nested_scopes_shadow_and_restore() -> None:
    outer = CacheControls(ttl=30)
    inner = CacheControls(skip=True)
    with cache_scope(outer):
        assert current_cache_controls() is outer
        with cache_scope(inner):
            assert current_cache_controls() is inner
        assert current_cache_controls() is outer
    assert current_cache_controls() is None


async def test_scope_binds_and_restores_async() -> None:
    controls = CacheControls(no_store=True)
    async with cache_scope(controls) as bound:
        assert bound is controls
        assert current_cache_controls() is controls
    assert current_cache_controls() is None


def test_cache_scope_returns_a_cache_scope() -> None:
    assert isinstance(cache_scope(CacheControls(skip=True)), CacheScope)


# ---------------------------------------------------------------------------
# Transport injection — the one shared seam, async AND sync (cross-surface lockstep)
# ---------------------------------------------------------------------------


def _capture_async(seen: dict[str, str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200)

    return handler


async def test_cache_headers_injected_async() -> None:
    seen: dict[str, str] = {}
    client = DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(_capture_async(seen))
    )
    async with client:
        with warnings.catch_warnings():
            # verified=True names must not warn (verification discipline: nothing left to discover).
            warnings.simplefilter("error", UnverifiedValueWarning)
            with cache_scope(CacheControls(skip=True, ttl=60, principal_id="u-1")):
                await client.get("https://x/thing")
    assert seen[CACHE_SKIP_HEADER.get()] == "true"
    assert seen[CACHE_TTL_HEADER.get()] == "60"
    assert seen[CACHE_PRINCIPAL_ID_HEADER.get()] == "u-1"


def test_cache_headers_injected_sync() -> None:
    seen: dict[str, str] = {}
    with DonkeyClient(DonkeyConfig(), transport=httpx.MockTransport(_capture_async(seen))) as c:
        with cache_scope(CacheControls(threshold=0.5)):
            c.get("https://x/thing")
    assert seen[CACHE_THRESHOLD_HEADER.get()] == "0.5"


async def test_no_cache_headers_without_a_scope() -> None:
    seen: dict[str, str] = {}
    client = DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(_capture_async(seen))
    )
    async with client:
        await client.get("https://x/thing")
    assert CACHE_SKIP_HEADER.get() not in seen
    assert CACHE_TTL_HEADER.get() not in seen


# ---------------------------------------------------------------------------
# last_call surfacing — parsed from the LIVE captures for all four states
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,status,score_present",
    [
        ("responses.hit.headers.txt", "hit", True),
        ("responses.miss.headers.txt", "miss", False),
        ("responses.bypass.headers.txt", "bypass", False),
        ("responses.no-store.headers.txt", "no-store", False),
    ],
)
def test_semantic_cache_parses_every_live_state(
    fixture: str, status: str, score_present: bool
) -> None:
    """docs/verified-apis.md §2: status ∈ {hit,miss,bypass,no-store}; score hit-only."""
    response = _resp_from_fixture(fixture)
    cache_status, cache_score = semantic_cache(response)
    assert cache_status == status
    if score_present:
        assert isinstance(cache_score, float)
    else:
        assert cache_score is None


def test_is_cache_hit_only_true_on_hit() -> None:
    assert is_cache_hit(_resp_from_fixture("responses.hit.headers.txt")) is True
    assert is_cache_hit(_resp_from_fixture("responses.miss.headers.txt")) is False
    assert is_cache_hit(_resp_from_fixture("responses.bypass.headers.txt")) is False


def test_absent_headers_leave_cache_fields_none() -> None:
    # A proxy with no caching policy sends neither header; never raise (verification discipline).
    status, score = semantic_cache(httpx.Response(200))
    assert status is None and score is None
    assert is_cache_hit(httpx.Response(200)) is False


def test_last_call_surfaces_status_score_and_hit_flag() -> None:
    record = LastCall.from_response(_resp_from_fixture("responses.hit.headers.txt"))
    assert record.status is LastCallStatus.OBSERVED
    assert record.cache_status == "hit"
    assert isinstance(record.cache_score, float)
    assert record.cache_hit is True


def test_last_call_cache_hit_false_on_miss() -> None:
    record = LastCall.from_response(_resp_from_fixture("responses.miss.headers.txt"))
    assert record.cache_status == "miss"
    assert record.cache_score is None
    assert record.cache_hit is False


def test_donkey_last_call_populated_via_transport() -> None:
    """The transport's _on_response lands the cache outcome on the context record."""
    token = lastcall._last_call.set(None)
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return _resp_from_fixture("responses.hit.headers.txt")

        with DonkeyClient(DonkeyConfig(), transport=httpx.MockTransport(handler)) as c:
            # A model call (JSON body with a `model`) is what feeds last_call —
            # a bare GET is not "the last call" a developer means (#362).
            c.post("https://x/chat/completions", json={"model": "gpt-5-mini"})
        record = lastcall.current_last_call()
        assert record is not None and record.cache_hit is True
    finally:
        lastcall._last_call.reset(token)


# ---------------------------------------------------------------------------
# Budget — a hit is a verbatim replay: no fresh spend to observe (#588)
# ---------------------------------------------------------------------------


def test_budget_ignores_a_cache_hit_even_with_budget_headers() -> None:
    # A hit carries no budget headers live; short-circuiting on the STATUS keeps
    # the intent explicit and robust to a future hit echoing a stale window (#587).
    b = Budget()
    hit = httpx.Response(
        200,
        headers={
            "x-semantic-cache-status": "hit",
            "x-token-limit": "1000",
            "x-token-remaining": "10",
        },
    )
    b.observe(hit)
    assert b.limit is None
    assert b.remaining is None
    assert b.observed_at is None


def test_budget_observes_a_miss_normally() -> None:
    b = Budget()
    miss = httpx.Response(
        200,
        headers={
            "x-semantic-cache-status": "miss",
            "x-token-limit": "1000",
            "x-token-remaining": "250",
        },
    )
    b.observe(miss)
    assert b.limit == 1000
    assert b.remaining == 250


# ---------------------------------------------------------------------------
# OTel span — the cache outcome lands on the span attributes
# ---------------------------------------------------------------------------


def test_cache_span_keys_are_pinned_and_allowlisted() -> None:
    assert telemetry.DONKEY_CACHE_STATUS == "donkey.cache.status"
    assert telemetry.DONKEY_CACHE_SCORE == "donkey.cache.score"
    assert telemetry.DONKEY_CACHE_STATUS in telemetry._ALLOWED_SPAN_ATTRIBUTES
    assert telemetry.DONKEY_CACHE_SCORE in telemetry._ALLOWED_SPAN_ATTRIBUTES


def test_build_genai_attributes_emits_cache_outcome() -> None:
    attrs = telemetry.build_genai_attributes(cache_status="hit", cache_score=0.9518)
    assert attrs[telemetry.DONKEY_CACHE_STATUS] == "hit"
    assert attrs[telemetry.DONKEY_CACHE_SCORE] == 0.9518


def test_build_genai_attributes_omits_absent_cache_outcome() -> None:
    attrs = telemetry.build_genai_attributes(cache_status=None, cache_score=None)
    assert telemetry.DONKEY_CACHE_STATUS not in attrs
    assert telemetry.DONKEY_CACHE_SCORE not in attrs


# ---------------------------------------------------------------------------
# donkey.cache(...) — the public facade
# ---------------------------------------------------------------------------


def test_donkey_cache_returns_a_binding_scope() -> None:
    donkey = Donkey(DonkeyConfig(llm_proxy_url="https://proxy"))
    with donkey.cache(skip=True):
        controls = current_cache_controls()
        assert controls is not None and controls.skip is True
    assert current_cache_controls() is None


def test_donkey_cache_validates_at_the_call_site() -> None:
    donkey = Donkey(DonkeyConfig(llm_proxy_url="https://proxy"))
    with pytest.raises(ConfigError):
        donkey.cache(ttl=-1)
