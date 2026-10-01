"""The conformance harness + suite, driven against toy agents (#191, BG §1.5).

The plugin is the shipped surface, but the logic lives in
:func:`donkey_kit.conformance.harness.run_conformance`: build a fresh
``Donkey`` per scenario, arm the gateway with a captured fixture, drive
``agent.run(...)`` and observe. These tests exercise that logic directly against
in-repo toy agents — a well-behaved one that passes every scenario, and four
deliberately-broken ones that each break *exactly one* thing — so every test
can assert both that the target scenario fails and that the other three still
pass. A regression in any scenario's verdict is caught without a real gateway.

The toy agents call ``donkey.openai()``, so this module needs ``openai`` (the
plugin itself does not — see ``test_conformance_base_only``). Under ``[dev]``
alone the whole module skips.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

pytest.importorskip("openai")

import openai  # noqa: E402 — after importorskip

from donkey_kit.conformance import harness as harness_module  # noqa: E402
from donkey_kit.conformance import run_conformance, validate_known_limitations  # noqa: E402
from donkey_kit.conformance.harness import (  # noqa: E402
    _PLACEHOLDER,
    _PLACEHOLDER_URL,
    ConformanceUsageError,
    _build_agent,
    _offline_config,
)
from donkey_kit.conformance.suite import (  # noqa: E402
    NO_MODEL_CALL,
    SCENARIOS,
    Observation,
    _no_model_call,
)
from donkey_kit.core.errors import classify  # noqa: E402
from donkey_kit.core.telemetry import current_correlation_id  # noqa: E402
from donkey_kit.donkey import Donkey  # noqa: E402

_LOG = logging.getLogger("donkey_kit.tests.toy_agent")

_MODEL = "gpt-5.1"


def _statuses(results: list) -> dict[str, str]:
    return {r.scenario: r.status for r in results}


def _detail(results: list, scenario: str) -> str:
    return next(r.detail for r in results if r.scenario == scenario)


def _expect_only_failure(results: list, failing: str) -> None:
    """Assert exactly ``failing`` failed and every other scenario passed — the
    heart of these tests: each broken agent trips its own scenario and no other."""
    expected = {s.name: ("fail" if s.name == failing else "pass") for s in SCENARIOS}
    assert _statuses(results) == expected


# --- toy agents --------------------------------------------------------------
# GoodAgent does everything right and passes all four scenarios. Each broken
# agent inherits it and overrides run() to break exactly one behaviour, so its
# test can assert the other three scenarios still pass.


class GoodAgent:
    """Bridges refusals with classify(), never retries a refusal, logs the
    correlation id, and tolerates an absent budget."""

    def __init__(self, donkey: Donkey) -> None:
        self._client = donkey.openai()
        self._budget = donkey.budget

    async def run(self, prompt: str) -> str:
        try:
            resp = await self._client.responses.create(model=_MODEL, input=prompt)
        except openai.APIStatusError as exc:
            # Bridge the raw HTTP refusal to the typed taxonomy — do NOT retry.
            raise classify(exc.response) from exc
        _LOG.info("run complete correlation_id=%s", current_correlation_id())
        # Only reason about the budget when it was actually observed.
        if self._budget.remaining is not None and self._budget.remaining < 0:
            raise RuntimeError("over budget")
        return resp.output_text


class RetriesBudgetAgent(GoodAgent):
    """The headline bug: retries a terminal budget refusal (429) in a loop.
    Every other refusal is still bridged, and success still logs the id — so
    only the retry scenario fails."""

    async def run(self, prompt: str) -> str:
        for attempt in range(3):
            try:
                resp = await self._client.responses.create(model=_MODEL, input=prompt)
            except openai.RateLimitError as exc:  # a 429 budget refusal — retried!
                if attempt == 2:
                    raise classify(exc.response) from exc
                continue
            except openai.APIStatusError as exc:
                raise classify(exc.response) from exc
            _LOG.info("run complete correlation_id=%s", current_correlation_id())
            return resp.output_text
        raise AssertionError("unreachable")


class SwallowsPiiAgent(GoodAgent):
    """Catches the refusal and re-raises a generic RuntimeError, losing the typed
    PIIDetected. Does not retry, so only the PII scenario fails."""

    async def run(self, prompt: str) -> str:
        try:
            resp = await self._client.responses.create(model=_MODEL, input=prompt)
        except openai.APIStatusError as exc:  # swallows the typed refusal
            raise RuntimeError(f"agent failed: {exc}") from exc
        _LOG.info("run complete correlation_id=%s", current_correlation_id())
        return resp.output_text


class DropsCorrelationAgent(GoodAgent):
    """Completes fine and bridges refusals, but never carries the correlation id
    into its logs — so only the correlation scenario fails."""

    async def run(self, prompt: str) -> str:
        try:
            resp = await self._client.responses.create(model=_MODEL, input=prompt)
        except openai.APIStatusError as exc:
            raise classify(exc.response) from exc
        _LOG.info("run complete")  # no correlation id
        return resp.output_text


class CrashesWithoutBudgetAgent(GoodAgent):
    """Assumes budget headers are always present and does arithmetic on
    ``remaining``. It logs the id before crashing (so correlation still passes)
    and only trips over an unobserved budget — so only that scenario fails."""

    async def run(self, prompt: str) -> str:
        try:
            resp = await self._client.responses.create(model=_MODEL, input=prompt)
        except openai.APIStatusError as exc:
            raise classify(exc.response) from exc
        _LOG.info("run complete correlation_id=%s", current_correlation_id())
        # TypeError when remaining is None (no x-token-* headers on the response).
        if self._budget.remaining < 100:  # type: ignore[operator]
            _LOG.warning("low budget")
        return resp.output_text


class SyncGoodAgent:
    """A well-behaved agent on the blocking client, to exercise sync arming."""

    def __init__(self, donkey: Donkey) -> None:
        self._client = donkey.openai(sync=True)
        self._budget = donkey.budget

    def run(self, prompt: str) -> str:
        try:
            resp = self._client.responses.create(model=_MODEL, input=prompt)
        except openai.APIStatusError as exc:
            raise classify(exc.response) from exc
        _LOG.info("run complete correlation_id=%s", current_correlation_id())
        if self._budget.remaining is not None and self._budget.remaining < 0:
            raise RuntimeError("over budget")
        return resp.output_text


# --- the good agents pass everything -----------------------------------------


async def test_good_agent_passes_every_scenario() -> None:
    results = await run_conformance(GoodAgent)
    assert _statuses(results) == {s.name: "pass" for s in SCENARIOS}


async def test_sync_good_agent_passes_every_scenario() -> None:
    # Same guarantees on the blocking surface: the harness arms both transports.
    results = await run_conformance(SyncGoodAgent)
    assert _statuses(results) == {s.name: "pass" for s in SCENARIOS}


# --- each broken agent trips exactly its scenario ----------------------------


async def test_retrying_agent_fails_only_the_retry_scenario() -> None:
    results = await run_conformance(RetriesBudgetAgent)
    _expect_only_failure(results, "retries_token_budget")
    assert "retried" in _detail(results, "retries_token_budget").lower()


async def test_swallowing_agent_fails_only_the_pii_scenario() -> None:
    results = await run_conformance(SwallowsPiiAgent)
    _expect_only_failure(results, "swallows_pii_as_generic")
    assert "swallowed" in _detail(results, "swallows_pii_as_generic").lower()


async def test_dropping_agent_fails_only_the_correlation_scenario() -> None:
    results = await run_conformance(DropsCorrelationAgent)
    _expect_only_failure(results, "correlation_id_propagated")


async def test_crashing_agent_fails_only_the_budget_headers_scenario() -> None:
    results = await run_conformance(CrashesWithoutBudgetAgent)
    _expect_only_failure(results, "works_without_budget_headers")
    assert "TypeError" in _detail(results, "works_without_budget_headers")


# --- exemptions --------------------------------------------------------------


async def test_exemption_marks_scenario_exempt_and_skips_the_run() -> None:
    # Even though this agent WOULD fail the retry scenario, an asserted exemption
    # records it as exempt (with the reason) and does not run the check.
    reason = "this demo agent has no retry loop to exercise"
    results = await run_conformance(
        RetriesBudgetAgent, known_limitations={"retries_token_budget": reason}
    )
    statuses = _statuses(results)
    assert statuses["retries_token_budget"] == "exempt"
    assert _detail(results, "retries_token_budget") == reason
    # Every other scenario still runs and passes for this agent.
    assert statuses["works_without_budget_headers"] == "pass"


async def test_invalid_exemption_raises_before_running() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        await run_conformance(GoodAgent, known_limitations={"not_a_scenario": "x"})


# --- validate_known_limitations ---------------------------------------------


def test_validate_known_limitations_none_is_empty() -> None:
    assert validate_known_limitations(None) == {}


def test_validate_known_limitations_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        validate_known_limitations(["retries_token_budget"])


def test_validate_known_limitations_rejects_unknown_scenario() -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        validate_known_limitations({"nope": "reason"})


@pytest.mark.parametrize("reason", ["", "   ", None, 123])
def test_validate_known_limitations_requires_nonempty_reason(reason: object) -> None:
    with pytest.raises(ValueError, match="non-empty reason"):
        validate_known_limitations({"retries_token_budget": reason})


def test_validate_known_limitations_returns_plain_dict() -> None:
    out = validate_known_limitations({"swallows_pii_as_generic": "handled upstream"})
    assert out == {"swallows_pii_as_generic": "handled upstream"}


# --- factory introspection (_build_agent) ------------------------------------


def _fresh_donkey() -> Donkey:
    return Donkey(_offline_config())


def test_build_agent_passes_donkey_positionally() -> None:
    fab = _fresh_donkey()
    try:
        seen = {}

        def factory(donkey: Donkey) -> str:
            seen["donkey"] = donkey
            return "agent"

        assert _build_agent(factory, fab) == "agent"
        assert seen["donkey"] is fab
    finally:
        fab.close()


def test_build_agent_passes_donkey_keyword_only() -> None:
    fab = _fresh_donkey()
    try:
        seen = {}

        def factory(*, donkey: Donkey) -> str:
            seen["donkey"] = donkey
            return "agent"

        assert _build_agent(factory, fab) == "agent"
        assert seen["donkey"] is fab
    finally:
        fab.close()


def test_build_agent_calls_zero_arg_factory_without_donkey() -> None:
    fab = _fresh_donkey()
    try:
        def factory() -> str:
            return "agent"

        assert _build_agent(factory, fab) == "agent"
    finally:
        fab.close()


# --- agent contract errors ---------------------------------------------------


async def test_agent_without_run_raises_usage_error() -> None:
    class NoRun:
        def __init__(self, donkey: Donkey) -> None:
            pass

    with pytest.raises(ConformanceUsageError, match="run"):
        await run_conformance(NoRun)


async def test_harness_runs_scenarios_in_canonical_order() -> None:
    results = await run_conformance(GoodAgent)
    assert [r.scenario for r in results] == [s.name for s in SCENARIOS]
    assert all(isinstance(r.title, str) and r.title for r in results)


# --- agents that bypass the Donkey client never pass (#737) ------------------
# The suite observes the Donkey's transport, so an agent whose model calls do not
# go through it must fail every scenario, never pass by default. And whatever
# client it builds itself must not reach the network.

_ELSEWHERE = "https://not-the-donkey.invalid/v1"


class NoCallAgent:
    """Makes no model call at all: logs the correlation id and returns a canned
    string. Before #737 this passed every scenario but the PII one."""

    def __init__(self, donkey: Donkey) -> None:
        pass

    async def run(self, prompt: str) -> str:
        _LOG.info("run complete correlation_id=%s", current_correlation_id())
        return "canned"


class OwnHttpxAgent:
    """Calls the model through a stock httpx client of its own and swallows the
    outage, so it never raises."""

    def __init__(self, donkey: Donkey) -> None:
        self._client = httpx.AsyncClient(base_url=_ELSEWHERE)

    async def run(self, prompt: str) -> str:
        _LOG.info("run correlation_id=%s", current_correlation_id())
        try:
            resp = await self._client.post("/responses", json={"input": prompt})
        except httpx.HTTPError:
            return "fallback answer"
        finally:
            await self._client.aclose()
        return resp.text


class OwnOpenAIAgent:
    """Calls the model through a stock OpenAI client it built itself, pointed
    somewhere other than the Donkey."""

    def __init__(self, donkey: Donkey) -> None:
        self._client = openai.AsyncOpenAI(base_url=_ELSEWHERE, api_key="sk-own", max_retries=0)

    async def run(self, prompt: str) -> str:
        _LOG.info("run correlation_id=%s", current_correlation_id())
        resp = await self._client.responses.create(model=_MODEL, input=prompt)
        return resp.output_text


@pytest.fixture
def real_transport_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record any request that reaches httpx's real network transports. The
    harness swaps its own blocker in for the run, so a send that gets past it
    lands here; the spy fails the request too, so a test never goes online."""
    sent: list[str] = []

    def spy_sync(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        raise httpx.ConnectError("test spy", request=request)

    async def spy_async(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        raise httpx.ConnectError("test spy", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", spy_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", spy_async)
    return sent


@pytest.mark.parametrize(
    ("agent", "reason"),
    [
        (NoCallAgent, "made no model call"),
        (OwnHttpxAgent, "client other than the Donkey's"),
        (OwnOpenAIAgent, "client other than the Donkey's"),
    ],
)
async def test_agent_bypassing_the_donkey_fails_every_scenario(
    agent: type, reason: str, real_transport_spy: list[str]
) -> None:
    results = await run_conformance(agent)
    assert _statuses(results) == {s.name: "fail" for s in SCENARIOS}
    for r in results:
        assert r.detail.startswith(NO_MODEL_CALL), r.detail
        assert reason in r.detail
    # The harness blocked every send, so nothing reached a real transport.
    assert real_transport_spy == []


async def test_harness_restores_real_transports_after_the_run(
    real_transport_spy: list[str],
) -> None:
    spy_sync = httpx.HTTPTransport.handle_request
    spy_async = httpx.AsyncHTTPTransport.handle_async_request
    await run_conformance(OwnHttpxAgent)
    assert httpx.HTTPTransport.handle_request is spy_sync
    assert httpx.AsyncHTTPTransport.handle_async_request is spy_async


def test_unobservable_adapter_is_named_with_the_exemption_route() -> None:
    # A header-only adapter (CrewAI, LlamaIndex, MAF, ADK model()) builds its own
    # client, so the finding names the adapter and the KNOWN_LIMITATIONS route
    # rather than telling the developer to use the donkey they already used.
    obs = Observation(
        returned=None,
        raised=None,
        wire_sends=0,
        log_text="",
        bypass_attempts=3,
        unobservable_adapters=("crewai",),
    )
    outcome = _no_model_call(obs)
    assert outcome is not None and not outcome.passed
    assert outcome.detail.startswith(NO_MODEL_CALL)
    assert "donkey.crewai" in outcome.detail
    assert "3 request(s) blocked" in outcome.detail
    assert "KNOWN_LIMITATIONS" in outcome.detail


def test_a_call_through_the_donkey_is_not_flagged() -> None:
    obs = Observation(returned="ok", raised=None, wire_sends=1, log_text="")
    assert _no_model_call(obs) is None


# --- configured credentials are never used (#737) ----------------------------

_REAL_ENV = {
    "DONKEY_LLM_PROXY_URL": "https://real-gateway.example.com/",
    "DONKEY_LLM_PROXY_CLIENT_ID": "real-client-id",
    "DONKEY_LLM_PROXY_CLIENT_SECRET": "real-client-secret",
    "DONKEY_LLM_PROXY_KEY": "real-proxy-key",
    "DONKEY_LLM_PROXY_WALLET_CLIENT_ID": "real-wallet-id",
    "ANYPOINT_CLIENT_ID": "real-anypoint-id",
    "ANYPOINT_CLIENT_SECRET": "real-anypoint-secret",
}


@pytest.fixture
def real_credentials(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for key, value in _REAL_ENV.items():
        monkeypatch.setenv(key, value)
    return _REAL_ENV


def test_offline_config_replaces_configured_credentials(
    real_credentials: dict[str, str],
) -> None:
    cfg = _offline_config()
    assert cfg.llm_proxy_url == _PLACEHOLDER_URL
    assert cfg.llm_proxy_client_id == _PLACEHOLDER
    assert cfg.llm_proxy_client_secret == _PLACEHOLDER
    assert cfg.llm_proxy_auth == "client-id"
    for unset in ("llm_proxy_key", "llm_proxy_wallet_client_id", "client_id", "client_secret"):
        assert getattr(cfg, unset) is None, unset


async def test_harness_never_sends_configured_credentials(
    real_credentials: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Record every request the probe serves, then run a well-behaved agent with
    # real-looking credentials in the environment: none of them may appear.
    seen: list[httpx.Request] = []
    responder = harness_module._fixture_responder

    def recording(*args: Any, **kwargs: Any) -> Any:
        respond = responder(*args, **kwargs)

        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return respond(request)

        return wrapped

    monkeypatch.setattr(harness_module, "_fixture_responder", recording)
    results = await run_conformance(GoodAgent)

    assert _statuses(results) == {s.name: "pass" for s in SCENARIOS}
    assert seen
    for request in seen:
        assert request.url.host == httpx.URL(_PLACEHOLDER_URL).host
        sent = " ".join(f"{k}={v}" for k, v in request.headers.items())
        for value in real_credentials.values():
            assert value not in sent
