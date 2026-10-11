"""Config resolution + all-at-once validation."""

from __future__ import annotations

import warnings
from dataclasses import fields
from typing import get_type_hints

import pytest

from donkey_kit.core._verify import UnverifiedValueWarning
from donkey_kit.core.config import ConfigOverrides, DonkeyConfig, Region
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import ConfigError


def test_validated_reports_all_missing_control_plane_fields_at_once() -> None:
    cfg = DonkeyConfig()  # nothing set
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need="control_plane")
    msg = str(exc.value)
    # All three must appear in ONE error (config resolution), not one-per-run.
    assert "client_id" in msg
    assert "client_secret" in msg
    assert "org_id" in msg


def test_validated_llm_is_independent_of_control_plane() -> None:
    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )
    # LLM creds present, control-plane absent: llm validation passes (BG §1.1).
    assert cfg.validated(need="llm") is cfg
    with pytest.raises(ConfigError):
        cfg.validated(need="control_plane")


def test_validated_llm_requires_client_id_and_secret_not_bearer() -> None:
    # Verified auth (docs/verified-apis.md §2/§3) is a client_id/secret pair — a bare url + api-key
    # is NOT sufficient, and the error names BOTH missing header credentials at once.
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
    with pytest.warns(UnverifiedValueWarning, match="docs/verified-apis.md §1"):
        assert cfg.control_plane_url.startswith("https://eu1")


@pytest.mark.parametrize("region", ["eu", "ca", "jp"])
def test_unconfirmed_region_host_warns_once(region: Region) -> None:
    # docs/verified-apis.md §1: only the US host is confirmed.
    cfg = DonkeyConfig(region=region)
    with pytest.warns(UnverifiedValueWarning, match=f"anypoint.region_host.{region}"):
        assert cfg.control_plane_url == f"https://{region}1.anypoint.mulesoft.com"
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        assert cfg.control_plane_url == f"https://{region}1.anypoint.mulesoft.com"


def test_us_region_and_base_url_override_are_quiet() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        assert DonkeyConfig().control_plane_url == "https://anypoint.mulesoft.com"
        override = DonkeyConfig(region="eu", base_url="https://eu1.example.test")
        assert override.control_plane_url == "https://eu1.example.test"


def test_unknown_region_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYPOINT_REGION", "mars")
    with pytest.raises(ConfigError):
        DonkeyConfig.from_env()


# --- telemetry_capture_content resolution (#306, BG §1.6) -------------------
# Safe by default: content is emitted on spans only when the developer opts in,
# through the normal set-in-code → env → toml → default precedence.


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


# --- telemetry_install_global resolution (#732, BG §1.6) -------------------
# No hidden global side effect: DDK takes the process-global OTel provider only
# when the developer opts in, through the normal precedence.


def test_install_global_defaults_to_false(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.delenv("DONKEY_TELEMETRY_INSTALL_GLOBAL", raising=False)
    assert DonkeyConfig().telemetry_install_global is False  # dataclass default
    assert DonkeyConfig.from_env().telemetry_install_global is False  # resolved default


def test_install_global_env_overrides_toml(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch, "[donkey]\ntelemetry_install_global = true\n")
    monkeypatch.delenv("DONKEY_TELEMETRY_INSTALL_GLOBAL", raising=False)
    assert DonkeyConfig.from_env().telemetry_install_global is True  # toml layer
    monkeypatch.setenv("DONKEY_TELEMETRY_INSTALL_GLOBAL", "false")
    assert DonkeyConfig.from_env().telemetry_install_global is False  # env wins


# --- on_model_substitution resolution + validation (BG §1.1, #309) ---------------
# Off by default: a routing substitution is surfaced passively on last_call
# unless the developer opts into a hard error, through the normal precedence.


def test_on_model_substitution_defaults_to_off(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    assert DonkeyConfig().on_model_substitution == "off"  # dataclass default
    assert DonkeyConfig.from_env().on_model_substitution == "off"  # resolved default


def test_on_model_substitution_from_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_ON_MODEL_SUBSTITUTION", "raise")
    assert DonkeyConfig.from_env().on_model_substitution == "raise"


def test_on_model_substitution_env_overrides_toml(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_toml(tmp_path, monkeypatch, 'on_model_substitution = "off"\n')
    assert DonkeyConfig.from_env().on_model_substitution == "off"  # toml layer
    monkeypatch.setenv("DONKEY_ON_MODEL_SUBSTITUTION", "raise")
    assert DonkeyConfig.from_env().on_model_substitution == "raise"  # env wins


def test_on_model_substitution_is_case_insensitive(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_ON_MODEL_SUBSTITUTION", "RAISE")
    assert DonkeyConfig.from_env().on_model_substitution == "raise"


def test_unknown_on_model_substitution_is_a_config_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A typo like "error" must fail loudly at resolve time — a silent fall-back to
    # "off" would leave a caller believing they had opted into strictness (#309).
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_ON_MODEL_SUBSTITUTION", "error")
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    assert "error" in str(exc.value)


# --- llm_proxy_auth mode resolution + validation (BG §1.1, #509) ------------------
# client-id by default (the LIVE-VERIFIED CIE pair); "jwt" selects the
# model-wallet ingress, which requires NO client_secret but DOES require the
# durable wallet-selector client id. An unknown mode fails loudly at resolve time.


def test_llm_proxy_auth_defaults_to_client_id(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    assert DonkeyConfig().llm_proxy_auth == "client-id"  # dataclass default
    assert DonkeyConfig.from_env().llm_proxy_auth == "client-id"  # resolved default


def test_llm_proxy_auth_from_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "jwt")
    assert DonkeyConfig.from_env().llm_proxy_auth == "jwt"


def test_llm_proxy_auth_is_case_insensitive(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "JWT")
    assert DonkeyConfig.from_env().llm_proxy_auth == "jwt"


def test_unknown_llm_proxy_auth_is_a_config_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A typo like "oauth" must fail loudly at resolve time — a silent fall-back to
    # "client-id" would leave a caller believing they had selected the wallet
    # ingress (#509), mirroring on_model_substitution.
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "oauth")
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    assert "oauth" in str(exc.value)


def test_wallet_client_id_from_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_toml(tmp_path, monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_WALLET_CLIENT_ID", "wallet-42")
    assert DonkeyConfig.from_env().llm_proxy_wallet_client_id == "wallet-42"


def test_validated_jwt_requires_url_and_wallet_client_id_not_secret() -> None:
    # jwt mode: CIE is disabled, so NO client_secret is required — the missing
    # field is the durable wallet-selector client id, and the error names it (not
    # client_secret). The rotating JWT is not a config field (it rides an
    # AuthProvider), so its absence is checked in LLMClient, not here (#509).
    cfg = DonkeyConfig(llm_proxy_auth="jwt")
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need="llm")
    msg = str(exc.value)
    assert "llm_proxy_url" in msg
    assert "llm_proxy_wallet_client_id" in msg
    assert "client_secret" not in msg


def test_validated_jwt_passes_with_url_and_wallet_client_id() -> None:
    cfg = DonkeyConfig(
        llm_proxy_auth="jwt",
        llm_proxy_url="https://proxy",
        llm_proxy_wallet_client_id="wallet-42",
    )
    # No client_id/client_secret needed in jwt mode (BG §1.1).
    assert cfg.validated(need="llm") is cfg


# --- cost-attribution tag resolution (docs/verified-apis.md §3, BG §1.7, #196) --------------------


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


def test_cost_env_overrides_toml_per_dimension(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_cost_header_name_overrides_resolve(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_config_overrides_lists_every_public_field_with_its_type() -> None:
    # with_overrides() is typed by ConfigOverrides (#716); a field added to the
    # dataclass but not here would be rejected by mypy for a valid override.
    hints = get_type_hints(DonkeyConfig)
    public = {f.name: hints[f.name] for f in fields(DonkeyConfig) if not f.name.startswith("_")}
    assert get_type_hints(ConfigOverrides) == public


def test_with_overrides_replaces_the_named_fields() -> None:
    cfg = DonkeyConfig(timeout_s=60.0).with_overrides(timeout_s=1.0, telemetry=False)
    assert (cfg.timeout_s, cfg.telemetry) == (1.0, False)


# --- user config file location (XDG Base Directory default, #837) ---------------------------------


@pytest.mark.parametrize("xdg", [None, "", "relative/dir"])
def test_user_file_defaults_to_home_config_when_xdg_unset(
    tmp_path, monkeypatch: pytest.MonkeyPatch, xdg: str | None
) -> None:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    user = home / ".config" / ".donkey-kit.toml"
    user.write_text('[donkey]\norg_id = "home-org"\n')
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("ANYPOINT_ORG_ID", raising=False)
    if xdg is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", xdg)

    cfg = DonkeyConfig.from_env()

    assert cfg.org_id == "home-org"
    assert cfg.source_of("org_id").kind == "user"
    assert cfg.source_of("org_id").path == user


def test_user_file_reads_only_xdg_config_home_when_set(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".config").mkdir(parents=True)
    (home / ".config" / ".donkey-kit.toml").write_text('[donkey]\norg_id = "home-org"\n')
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("ANYPOINT_ORG_ID", raising=False)

    assert DonkeyConfig.from_env().org_id is None

    (xdg / ".donkey-kit.toml").write_text('[donkey]\norg_id = "xdg-org"\n')
    cfg = DonkeyConfig.from_env()
    assert cfg.org_id == "xdg-org"
    assert cfg.source_of("org_id").kind == "user"
