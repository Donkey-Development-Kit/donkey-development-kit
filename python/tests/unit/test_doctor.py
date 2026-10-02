"""``donkey doctor`` diagnosis logic (#202).

Exercises the three look-alike failures the taxonomy tells apart, the
remediation-from-the-taxonomy contract (AC2), and the budget staleness line
(AC3) — all through an injected probe, so no gateway (and no ``[llm]`` extra)
is needed. Needs only ``[dev]``.
"""

from __future__ import annotations

import os
import socket
import warnings
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from donkey_kit.core._verify import UnverifiedValueWarning
from donkey_kit.core.budget import Budget
from donkey_kit.core.config import ConfigWarning
from donkey_kit.core.errors import (
    AuthError,
    ConfigError,
    GatewayUnavailable,
    PIIDetected,
    UpstreamRequestError,
)
from donkey_kit.provisioning import doctor
from donkey_kit.provisioning.doctor import (
    DoctorCheck,
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


def _by_name(checks: list[DoctorCheck], name: str) -> DoctorCheck:
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


# --- the live probe's error bridge (#813) --------------------------------------


def test_live_probe_reports_the_transports_own_outage() -> None:
    """Through the OpenAI client the transport's GatewayUnavailable arrives only
    on ``APIConnectionError.__cause__``; the probe must surface that instance, not
    rebuild a generic one from the wrapper."""
    pytest.importorskip("openai")
    from donkey_kit.core.config import DonkeyConfig

    # A just-released loopback port: nothing listens, so no network is touched.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = DonkeyConfig(
        llm_proxy_url=f"http://127.0.0.1:{port}/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
        timeout_s=2.0,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UnverifiedValueWarning)
        result = doctor._live_probe(cfg, "gpt-4o")

    err = result.error
    assert isinstance(err, GatewayUnavailable)
    assert isinstance(err.__cause__, httpx.TransportError)  # not the SDK's wrapper
    assert err.base_url == f"http://127.0.0.1:{port}"
    assert err.call_id is not None  # the ids the transport sent, carried through
    gw = _by_name(doctor._probe_checks(result), "gateway")
    assert gw.level is Level.FAIL


def test_bridge_prefers_a_typed_cause_over_an_outage_guess() -> None:
    """A typed error the transport raised (here a closed-client ConfigError) is
    reported as itself, never misreported as the gateway being unreachable."""
    cause = ConfigError("closed", remediation="make a new Donkey")
    wrapper = RuntimeError("Connection error.")
    wrapper.__cause__ = cause
    cfg = object.__new__(doctor.DonkeyConfig)

    assert doctor._bridge(wrapper, cfg) is cause


def test_clean_success_is_all_ok(llm_env: None) -> None:
    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))
    for name in ("config", "gateway", "credentials", "model"):
        assert _by_name(checks, name).level is Level.OK
    assert not doctor.has_failure(checks)


# --- endpoint sources + binding ------------------------------------------------


@pytest.fixture
def clean_project(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An empty project cwd, an empty user config dir, and no ANYPOINT_*/DONKEY_* env."""
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "xdg").mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return project


def test_endpoint_lines_name_host_and_source(
    clean_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (clean_project / ".donkey-kit.toml").write_text(
        '[donkey]\nllm_proxy_url = "https://llm.example.test/proxy/"\n'
    )
    (clean_project / ".donkey-kit.local.toml").write_text(
        '[donkey]\nllm_proxy_client_id = "cid"\nllm_proxy_client_secret = "secret"\n'
    )

    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))

    assert _by_name(checks, "llm endpoint").detail == "llm.example.test (project file)"
    assert _by_name(checks, "control plane").detail == "anypoint.mulesoft.com (default)"
    assert not doctor.has_failure(checks)


@pytest.mark.parametrize(
    ("where", "label"),
    [("env", "env"), ("local", "local overlay"), ("user", "user file")],
)
def test_endpoint_source_labels(
    clean_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    label: str,
) -> None:
    url = "https://llm.example.test/proxy/"
    if where == "env":
        monkeypatch.setenv("DONKEY_LLM_PROXY_URL", url)
    elif where == "local":
        (clean_project / ".donkey-kit.local.toml").write_text(
            f'[donkey]\nllm_proxy_url = "{url}"\n'
        )
    else:
        (tmp_path / "xdg" / ".donkey-kit.toml").write_text(
            f'[donkey]\nllm_proxy_url = "{url}"\n'
        )
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))

    assert f"({label})" in _by_name(checks, "llm endpoint").detail


def test_project_url_with_env_credentials_fails_config_without_probing(
    clean_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (clean_project / ".donkey-kit.toml").write_text(
        '[donkey]\nllm_proxy_url = "https://llm.example.test/proxy/"\n'
    )
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    def _boom(_c: object, _m: object) -> ProbeResult:
        raise AssertionError("no request may be sent to a project-file host")

    checks = run_diagnostics("gpt-4o", probe=_boom)

    config = _by_name(checks, "config")
    assert config.level is Level.FAIL
    assert "llm.example.test" in (config.remediation or "")
    assert str(clean_project / ".donkey-kit.toml") in (config.remediation or "")
    assert "project file" in _by_name(checks, "llm endpoint").detail
    assert doctor.has_failure(checks)


def test_unused_control_plane_binding_is_reported_but_does_not_fail(
    clean_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor probes the LLM proxy only; a control-plane endpoint problem is
    shown with its remediation but does not fail the LLM diagnosis."""
    (clean_project / ".donkey-kit.toml").write_text(
        '[donkey]\nbase_url = "https://cp.example.test"\n'
    )
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cid")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))

    cp = _by_name(checks, "control plane")
    assert "cp.example.test (project file)" in cp.detail
    assert cp.level is not Level.FAIL
    assert "DONKEY_TRUST_PROJECT_CONFIG" in (cp.remediation or "")
    assert not doctor.has_failure(checks)


def test_project_loopback_control_plane_shows_binding_remediation(
    clean_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (clean_project / ".donkey-kit.toml").write_text(
        '[donkey]\nbase_url = "http://127.0.0.1:8081"\n'
    )
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cid")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))

    cp = _by_name(checks, "control plane")
    assert "127.0.0.1 (project file)" in cp.detail
    assert "DONKEY_TRUST_PROJECT_CONFIG" in (cp.remediation or "")


@pytest.mark.parametrize("value", ["1", "true"])
def test_allow_http_switch_is_shown_when_on(
    clean_project: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", value)
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "http://llm.example.test/proxy/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    with pytest.warns(ConfigWarning):
        checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))

    line = _by_name(checks, "plain http")
    assert line.level is Level.INFO
    assert line.detail == f"allowed to non-loopback hosts (DONKEY_ALLOW_HTTP={value} in env)"
    assert _by_name(checks, "config").level is Level.OK


def test_allow_http_line_is_absent_when_off(
    clean_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/proxy/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")

    checks = run_diagnostics("gpt-4o", probe=_probe(ProbeResult(None, Budget())))

    assert "plain http" not in [c.name for c in checks]


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
        DoctorCheck("config", Level.OK, "env (3 fields)"),
        DoctorCheck("gateway", Level.FAIL, "unreachable", remediation="do the thing"),
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
