"""Config resolution + all-at-once validation (§2.1)."""

from __future__ import annotations

import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import ConfigError


def test_validated_reports_all_missing_control_plane_fields_at_once() -> None:
    cfg = DonkeyConfig()  # nothing set
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need="control_plane")
    msg = str(exc.value)
    # All three must appear in ONE error (§2.1), not one-per-run.
    assert "client_id" in msg
    assert "client_secret" in msg
    assert "org_id" in msg


def test_validated_llm_is_independent_of_control_plane() -> None:
    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )
    # LLM creds present, control-plane absent: llm validation passes (§2.2).
    assert cfg.validated(need="llm") is cfg
    with pytest.raises(ConfigError):
        cfg.validated(need="control_plane")


def test_validated_llm_requires_client_id_and_secret_not_bearer() -> None:
    # Verified auth (§2/§3) is a client_id/secret pair — a bare url + api-key is
    # NOT sufficient, and the error names BOTH missing header credentials at once.
    cfg = DonkeyConfig(llm_proxy_url="https://proxy", llm_proxy_key="k")
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need="llm")
    msg = str(exc.value)
    assert "llm_proxy_client_id" in msg
    assert "llm_proxy_client_secret" in msg


def test_env_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cid")
    monkeypatch.setenv("ANYPOINT_REGION", "eu")
    monkeypatch.setenv("DONKEY_TELEMETRY", "false")
    cfg = DonkeyConfig.from_env()
    assert cfg.client_id == "cid"
    assert cfg.region == "eu"
    assert cfg.telemetry is False
    assert cfg.control_plane_url.startswith("https://eu1")


def test_unknown_region_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYPOINT_REGION", "mars")
    with pytest.raises(ConfigError):
        DonkeyConfig.from_env()


# --- telemetry_capture_content resolution (#306, BG §1.6) -------------------
# Safe by default: content is emitted on spans only when the developer opts in,
# through the normal kwarg → env → toml → default precedence.


def test_capture_content_defaults_to_false(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    assert DonkeyConfig().telemetry_capture_content is False  # dataclass default
    assert DonkeyConfig.from_env().telemetry_capture_content is False  # resolved default


def test_capture_content_from_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_TELEMETRY_CAPTURE_CONTENT", "true")
    assert DonkeyConfig.from_env().telemetry_capture_content is True


def test_capture_content_env_overrides_toml(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch, "telemetry_capture_content = false\n")
    assert DonkeyConfig.from_env().telemetry_capture_content is False  # toml layer
    monkeypatch.setenv("DONKEY_TELEMETRY_CAPTURE_CONTENT", "true")
    assert DonkeyConfig.from_env().telemetry_capture_content is True  # env wins


# --- cost-attribution tag resolution (§3, BG §1.7, #196) --------------------


def _isolate_toml(tmp_path, monkeypatch: pytest.MonkeyPatch, body: str | None = None) -> None:
    """Point config discovery at an empty temp dir (no stray ``.donkey-kit.toml``
    from the checkout), optionally writing one with ``body``."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    if body is not None:
        (tmp_path / ".donkey-kit.toml").write_text(body)


def test_cost_tags_default_to_empty(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    cfg = DonkeyConfig.from_env()
    assert cfg.cost.is_empty


def test_cost_tags_from_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_COST_TEAM", "support")
    monkeypatch.setenv("DONKEY_COST_PROJECT", "triage-v2")
    monkeypatch.setenv("DONKEY_COST_ENV", "prod")
    monkeypatch.setenv("DONKEY_COST_ENDUSER_ID", "user-42")
    cfg = DonkeyConfig.from_env()
    assert cfg.cost == CostTags(
        team="support", project="triage-v2", env="prod", enduser_id="user-42"
    )


def test_cost_tags_from_toml_table(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ``enduser.id`` keeps the dotted external key in the [donkey.cost] table.
    _isolate_toml(
        tmp_path,
        monkeypatch,
        '[donkey.cost]\nteam = "support"\n"enduser.id" = "user-7"\n',
    )
    cfg = DonkeyConfig.from_env()
    assert cfg.cost == CostTags(team="support", enduser_id="user-7")


def test_cost_env_overrides_toml_per_dimension(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_toml(
        tmp_path,
        monkeypatch,
        '[donkey.cost]\nteam = "toml-team"\nproject = "toml-project"\n',
    )
    monkeypatch.setenv("DONKEY_COST_TEAM", "env-team")  # wins for team only
    cfg = DonkeyConfig.from_env()
    assert cfg.cost == CostTags(team="env-team", project="toml-project")


def test_unknown_cost_key_in_toml_is_a_config_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC #1: an unknown dimension is a config error, never a silently-dropped tag.
    _isolate_toml(tmp_path, monkeypatch, '[donkey.cost]\nteem = "typo"\n')
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    assert "teem" in str(exc.value)


def test_cost_header_name_overrides_resolve(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_COST_TEAM_HEADER", "x-cost-team")
    monkeypatch.setenv("DONKEY_COST_PROJECT_HEADER", "x-cost-project")
    monkeypatch.setenv("DONKEY_COST_ENV_HEADER", "x-cost-env")
    monkeypatch.setenv("DONKEY_COST_ENDUSER_HEADER", "x-cost-enduser")
    cfg = DonkeyConfig.from_env()
    assert cfg.cost_team_header == "x-cost-team"
    assert cfg.cost_project_header == "x-cost-project"
    assert cfg.cost_env_header == "x-cost-env"
    assert cfg.cost_enduser_header == "x-cost-enduser"
