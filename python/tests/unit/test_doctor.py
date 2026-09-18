"""``donkey doctor`` diagnosis logic (#202).

Exercises the three look-alike failures the taxonomy tells apart, the
remediation-from-the-taxonomy contract (AC2), and the budget staleness line
(AC3) — all through an injected probe, so no gateway (and no ``[llm]`` extra)
is needed. Needs only ``[dev]``.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from donkey_kit.core.budget import Budget
from donkey_kit.core.errors import (
    AuthError,
    GatewayUnavailable,
    PIIDetected,
    UpstreamRequestError,
)
from donkey_kit.provisioning import doctor
from donkey_kit.provisioning.doctor import (
    Check,
    Level,
    ProbeResult,
    run_diagnostics,
)


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """A complete llm-proxy config via env, in an empty cwd (no stray
    ``.donkey-kit.toml``), so ``config`` passes and the probe runs."""
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.internal")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")


def _by_name(checks: list[Check], name: str) -> Check:
    return next(c for c in checks if c.name == name)


def _probe(result: ProbeResult) -> doctor.Probe:
    return lambda _cfg, _model: result


# --- config gate --------------------------------------------------------------


def test_incomplete_config_skips_probe_and_lists_missing_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    for var in ("DONKEY_LLM_PROXY_URL", "DONKEY_LLM_PROXY_CLIENT_ID",
                "DONKEY_LLM_PROXY_CLIENT_SECRET"):
        monkeypatch.delenv(var, raising=False)

    # A probe that would explode if called — proves the gate short-circuits.
    def _boom(_c: object, _m: object) -> ProbeResult:
        raise AssertionError("probe must not run when config is incomplete")

    checks = run_diagnostics("gpt-4o", probe=_boom)

    config = _by_name(checks, "config")
    assert config.level is Level.FAIL
    assert "DONKEY_LLM_PROXY_URL" in (config.remediation or "")
    # Everything downstream is honestly not-checked, never guessed.
    for name in ("credentials", "gateway", "model"):
        assert _by_name(checks, name).level is Level.SKIP
    assert doctor.has_failure(checks)


# --- the three look-alike failures --------------------------------------------


def test_wrong_url_diagnosed_as_gateway_unreachable(llm_env: None) -> None:
    err = GatewayUnavailable("boom", base_url="https://typo.example")
    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(err, None)))

    gw = _by_name(checks, "gateway")
    assert gw.level is Level.FAIL
    assert "https://typo.example" in gw.detail
    # AC2: wording is the exception's own, not a second copy.
    assert gw.remediation == GatewayUnavailable.remediation
    # Can't judge creds/model when the gateway never answered.
    assert _by_name(checks, "credentials").level is Level.SKIP
    assert _by_name(checks, "model").level is Level.SKIP


def test_wrong_credentials_diagnosed_as_auth(llm_env: None) -> None:
    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(AuthError("nope"), None)))

    assert _by_name(checks, "gateway").level is Level.OK
    creds = _by_name(checks, "credentials")
    assert creds.level is Level.FAIL
    assert creds.remediation == AuthError.remediation  # one source of wording
    assert _by_name(checks, "model").level is Level.SKIP


def test_model_not_allowed_diagnosed_from_verified_passthrough(llm_env: None) -> None:
    err = UpstreamRequestError(
        "The upstream model provider rejected the request (400): "
        "model 'gpt-4o' does not exist.",
        code="model_not_found",
        param="model",
    )
    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(err, None)))

    assert _by_name(checks, "gateway").level is Level.OK
    assert _by_name(checks, "credentials").level is Level.OK  # got past auth
    model = _by_name(checks, "model")
    assert model.level is Level.FAIL
    assert "does not exist" in model.detail
    assert model.remediation == UpstreamRequestError.remediation


def test_non_model_typed_error_leaves_model_ok_and_notes_it(llm_env: None) -> None:
    """A policy refusal on the probe means auth + model were accepted; it's
    surfaced on its own [i] line, never mis-attributed to credentials or model."""
    checks = run_diagnostics(
        "gpt-4o", probe=_probe(ProbeResult(PIIDetected("blocked"), None))
    )
    assert _by_name(checks, "credentials").level is Level.OK
    assert _by_name(checks, "model").level is Level.OK
    assert _by_name(checks, "policy").level is Level.INFO


def test_clean_success_is_all_ok(llm_env: None) -> None:
    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))
    for name in ("config", "gateway", "credentials", "model"):
        assert _by_name(checks, name).level is Level.OK
    assert not doctor.has_failure(checks)


# --- budget staleness (AC3) ---------------------------------------------------


def test_budget_line_states_staleness_and_reset(llm_env: None) -> None:
    b = Budget()
    b.limit, b.remaining = 20000, 18450
    b.observed_at = doctor._utcnow() - timedelta(seconds=5)
    # A +30s cushion so the floor lands on 42m despite the seconds that elapse
    # between here and when _budget_check recomputes the delta.
    b.reset_at = doctor._utcnow() + timedelta(minutes=42, seconds=30)

    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, b)))
    budget = _by_name(checks, "budget")

    assert budget.level is Level.INFO
    assert "18,450 / 20,000 remaining" in budget.detail
    assert "resets in 42m" in budget.detail
    assert "observed 5s ago" in budget.detail  # never implies live data


def test_unobserved_budget_says_so(llm_env: None) -> None:
    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))
    assert "not yet observed" in _by_name(checks, "budget").detail


# --- report rendering + exit signal ------------------------------------------


def test_report_renders_glyphs_and_indented_remediation() -> None:
    checks = [
        Check("config", Level.OK, "env (3 fields)"),
        Check("gateway", Level.FAIL, "unreachable", remediation="do the thing"),
    ]
    report = doctor.format_report(checks)
    assert "[ok] config" in report
    assert "[!!] gateway" in report
    assert "     remediation: do the thing" in report


def test_humanize_buckets() -> None:
    assert doctor._humanize(-3) == "0s"
    assert doctor._humanize(5) == "5s"
    assert doctor._humanize(120) == "2m"
    assert doctor._humanize(7200) == "2h"
    assert doctor._humanize(172800) == "2d"
