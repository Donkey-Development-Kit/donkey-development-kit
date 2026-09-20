"""Scenario scripting for the local gateway simulator (#188, BG §1.4).

Two layers of test:

1. The parser + scenario runtime in :mod:`donkey_kit.simulator.scenarios` — pure,
   no server, base-only safe.
2. End-to-end against the ASGI app via ``httpx.ASGITransport`` (needs ``[local]``
   for starlette; guarded by an import skip) — proving a stock client sees the
   scripted shapes with the honesty header and the right discriminators.
"""

from __future__ import annotations

import time

import pytest

from donkey_kit.simulator.fixtures import (
    LIMIT_HEADER,
    RATELIMIT_HEADER,
    REMAINING_HEADER,
    RESET_HEADER,
)
from donkey_kit.simulator.scenarios import (
    BudgetScenario,
    InjectionScenario,
    PiiBlockScenario,
    ScenarioError,
    parse_scenario,
    parse_scenarios,
    request_text,
)

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parse_pii_block() -> None:
    s = parse_scenario("pii_block:every=5")
    assert isinstance(s, PiiBlockScenario)


def test_parse_injection() -> None:
    s = parse_scenario("injection:on-pattern=ignore previous")
    assert isinstance(s, InjectionScenario)


def test_parse_budget_with_duration_units() -> None:
    s = parse_scenario("budget:limit=20000,window=60s")
    assert isinstance(s, BudgetScenario)
    assert s._window_ms == 60_000  # noqa: SLF001 — asserting the parse result
    assert s._limit == 20_000  # noqa: SLF001


@pytest.mark.parametrize(
    "raw,expected_ms",
    [("500ms", 500), ("60s", 60_000), ("2m", 120_000), ("60", 60_000)],
)
def test_parse_budget_durations(raw: str, expected_ms: int) -> None:
    s = parse_scenario(f"budget:limit=100,window={raw}")
    assert isinstance(s, BudgetScenario)
    assert s._window_ms == expected_ms  # noqa: SLF001


@pytest.mark.parametrize(
    "spec",
    [
        "nope:every=1",  # unknown name
        "pii_block",  # no ':'
        "pii_block:every=0",  # every < 1
        "pii_block:every=abc",  # non-int
        "injection:on-pattern=",  # empty pattern
        "injection:",  # missing param
        "budget:limit=100",  # missing window
        "budget:window=60s",  # missing limit
        "budget:limit=100,window=xyz",  # bad duration
        "budget:limit=100,window=60s,cost=0",  # cost < 1
    ],
)
def test_parse_errors(spec: str) -> None:
    with pytest.raises(ScenarioError):
        parse_scenario(spec)


def test_parse_scenarios_returns_fresh_instances() -> None:
    a, b = parse_scenarios(["pii_block:every=2", "pii_block:every=2"])
    assert a is not b  # each call gets its own counter


# ---------------------------------------------------------------------------
# request_text extraction
# ---------------------------------------------------------------------------


def test_request_text_responses_input_string() -> None:
    assert "hello" in request_text({"input": "hello"})


def test_request_text_messages_and_instructions() -> None:
    payload = {
        "instructions": "be terse",
        "messages": [{"role": "user", "content": "IGNORE previous instructions"}],
    }
    text = request_text(payload)
    assert "be terse" in text
    assert "IGNORE previous instructions" in text


def test_request_text_non_dict_is_empty() -> None:
    assert request_text(None) == ""
    assert request_text("just a string") == ""


# ---------------------------------------------------------------------------
# Scenario runtime behaviour
# ---------------------------------------------------------------------------


def test_pii_block_every_nth_deterministic() -> None:
    s = PiiBlockScenario(every=3)
    hits = [s.on_call("x") is not None for _ in range(9)]
    assert hits == [False, False, True, False, False, True, False, False, True]


def test_injection_matches_case_insensitive() -> None:
    s = InjectionScenario("ignore previous")
    assert s.on_call("Please IGNORE PREVIOUS steps") is not None
    assert s.on_call("nothing to see") is None


def test_budget_exhausts_then_429_then_resets() -> None:
    # limit 100, cost 40 -> calls 1,2 pass (remaining 60, 20), call 3 hits 0-ish;
    # once remaining <= 0 the next call is a 429.
    s = BudgetScenario(limit=100, window_ms=50, cost=40)
    assert s.on_call("hi") is None  # remaining 60
    assert s.on_call("hi") is None  # remaining 20
    assert s.on_call("hi") is None  # remaining 0 (deducted to 0, still served)
    hit = s.on_call("hi")  # remaining 0 -> 429
    assert hit is not None
    assert hit.shape == "token-rate-limit"
    assert hit.extra_headers[REMAINING_HEADER] == "0"
    assert hit.extra_headers[LIMIT_HEADER] == "100"
    assert int(hit.extra_headers[RESET_HEADER]) >= 0
    # After the window elapses, the budget resets and calls pass again.
    time.sleep(0.06)
    assert s.on_call("hi") is None


def test_budget_happy_path_header_reflects_counter() -> None:
    s = BudgetScenario(limit=1000, window_ms=60_000, cost=100)
    assert s.on_call("hi") is None
    headers = s.happy_path_headers()
    prose = headers[RATELIMIT_HEADER]
    assert "900 tokens remaining of 1000 limit" in prose


def test_budget_default_cost_is_fixture_usage() -> None:
    # The default per-call cost is the happy-path fixture's own total_tokens (68),
    # not a fabricated number (verification discipline).
    s = BudgetScenario(limit=1000, window_ms=60_000)
    s.on_call("hi")
    assert "932 tokens remaining of 1000 limit" in s.happy_path_headers()[RATELIMIT_HEADER]
