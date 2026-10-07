"""Config precedence, one test per layer (§2.1, #727).

``DonkeyConfig.resolve(*, path=None, **overrides)`` resolves every field along
``kwargs > env > .donkey-kit.local.toml > .donkey-kit.toml > user file >
default``, merging the three files key by key instead of reading whichever is
found first. ``Donkey.from_env(**overrides)`` forwards to it.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Callable
from dataclasses import fields
from pathlib import Path
from typing import get_type_hints

import pytest

from donkey_kit import Donkey
from donkey_kit.core import config as config_module
from donkey_kit.core.config import ConfigOverrides, DonkeyConfig
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import ConfigError


@pytest.fixture
def layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """An empty environment, a working directory and a user config directory."""
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    work = tmp_path / "work"
    work.mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return {
        "user": xdg / ".donkey-kit.toml",
        "project": work / ".donkey-kit.toml",
        "local": work / ".donkey-kit.local.toml",
    }


def _write(path: Path, **values: object) -> None:
    body = "\n".join(f"{key} = {value!r}" for key, value in values.items())
    path.write_text(f"[donkey]\n{body}\n".replace("'", '"'))


# --- one test per layer --------------------------------------------------------------------------


def test_default_is_the_bottom_layer(layers: dict[str, Path]) -> None:
    cfg = DonkeyConfig.resolve()
    assert cfg.timeout_s == 60.0
    assert cfg.source_of("timeout_s").kind == "default"


def test_user_file_beats_the_default(layers: dict[str, Path]) -> None:
    _write(layers["user"], timeout_s=5.0)
    cfg = DonkeyConfig.resolve()
    assert cfg.timeout_s == 5.0
    assert cfg.source_of("timeout_s").kind == "user"


def test_project_file_beats_the_user_file(layers: dict[str, Path]) -> None:
    _write(layers["user"], timeout_s=5.0)
    _write(layers["project"], timeout_s=6.0)
    cfg = DonkeyConfig.resolve()
    assert cfg.timeout_s == 6.0
    assert cfg.source_of("timeout_s").kind == "project"


def test_local_file_beats_the_project_file(layers: dict[str, Path]) -> None:
    _write(layers["user"], timeout_s=5.0)
    _write(layers["project"], timeout_s=6.0)
    _write(layers["local"], timeout_s=7.0)
    cfg = DonkeyConfig.resolve()
    assert cfg.timeout_s == 7.0
    assert cfg.source_of("timeout_s").kind == "local"


def test_env_beats_every_file(layers: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _write(layers["user"], timeout_s=5.0)
    _write(layers["project"], timeout_s=6.0)
    _write(layers["local"], timeout_s=7.0)
    monkeypatch.setenv("DONKEY_TIMEOUT_S", "8")
    cfg = DonkeyConfig.resolve()
    assert cfg.timeout_s == 8.0
    assert cfg.source_of("timeout_s").kind == "env"


def test_kwargs_beat_the_environment(
    layers: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(layers["local"], timeout_s=7.0)
    monkeypatch.setenv("DONKEY_TIMEOUT_S", "8")
    cfg = DonkeyConfig.resolve(timeout_s=9.0)
    assert cfg.timeout_s == 9.0
    assert cfg.source_of("timeout_s").kind == "explicit"


def test_the_layer_order_constant_matches_what_resolve_does() -> None:
    assert config_module._PRECEDENCE == ("explicit", "env", "local", "project", "user", "default")


# --- the files merge instead of first-found-wins -------------------------------------------------


def test_user_file_fills_what_the_working_directory_files_leave_unset(
    layers: dict[str, Path],
) -> None:
    _write(layers["user"], org_id="user-org", environment="Design", timeout_s=5.0)
    _write(layers["project"], environment="Production")
    _write(layers["local"], timeout_s=7.0)
    cfg = DonkeyConfig.resolve()
    assert (cfg.org_id, cfg.environment, cfg.timeout_s) == ("user-org", "Production", 7.0)
    assert cfg.source_of("org_id") == config_module.ConfigSource("user", layers["user"])
    assert cfg.source_of("environment").kind == "project"
    assert cfg.source_of("timeout_s").kind == "local"


def test_user_file_is_read_beside_a_local_file_alone(layers: dict[str, Path]) -> None:
    # Before #727 a working directory with only the .local file hid the user file.
    _write(layers["user"], org_id="user-org")
    _write(layers["local"], environment="Production")
    cfg = DonkeyConfig.resolve()
    assert (cfg.org_id, cfg.environment) == ("user-org", "Production")


def test_cost_tables_merge_across_all_three_files(layers: dict[str, Path]) -> None:
    layers["user"].write_text('[donkey.cost]\nteam = "user-team"\nenv = "dev"\n')
    layers["project"].write_text('[donkey.cost]\nproject = "triage"\n')
    layers["local"].write_text('[donkey.cost]\nenv = "prod"\n')
    cfg = DonkeyConfig.resolve()
    assert cfg.cost == CostTags(team="user-team", project="triage", env="prod")
    assert cfg.source_of("cost.team").kind == "user"
    assert cfg.source_of("cost.env").kind == "local"


# --- path= ---------------------------------------------------------------------------------------


def test_path_replaces_the_working_directory_files(
    layers: dict[str, Path], tmp_path: Path
) -> None:
    _write(layers["project"], environment="from-cwd", org_id="cwd-org")
    _write(layers["user"], org_id="user-org")
    other = tmp_path / "elsewhere"
    other.mkdir()
    chosen = other / "team.toml"
    _write(chosen, environment="from-path")
    _write(other / ".donkey-kit.local.toml", timeout_s=4.0)

    cfg = DonkeyConfig.resolve(path=chosen)

    assert cfg.environment == "from-path"
    assert cfg.source_of("environment") == config_module.ConfigSource("project", chosen)
    assert cfg.timeout_s == 4.0
    assert cfg.source_of("timeout_s").kind == "local"
    # The user file still sits beneath it; the working directory's file is not read.
    assert cfg.org_id == "user-org"


def test_path_accepts_a_string(layers: dict[str, Path], tmp_path: Path) -> None:
    chosen = tmp_path / "cfg.toml"
    _write(chosen, environment="from-path")
    assert DonkeyConfig.resolve(path=str(chosen)).environment == "from-path"


def test_a_missing_path_is_an_error(layers: dict[str, Path], tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no config file at"):
        DonkeyConfig.resolve(path=tmp_path / "missing.toml")


def test_env_still_beats_the_path_file(
    layers: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "cfg.toml"
    _write(chosen, environment="from-path")
    monkeypatch.setenv("ANYPOINT_ENV", "from-env")
    assert DonkeyConfig.resolve(path=chosen).environment == "from-env"


# --- overrides -----------------------------------------------------------------------------------


def test_an_override_shadows_an_invalid_lower_value(
    layers: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The environment's value never applies, so it is never parsed.
    monkeypatch.setenv("DONKEY_TIMEOUT_S", "abc")
    assert DonkeyConfig.resolve(timeout_s=3.0).timeout_s == 3.0


def test_bad_overrides_and_bad_env_values_are_reported_together(
    layers: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_TIMEOUT_S", "abc")
    monkeypatch.setenv("DONKEY_TELEMETRY", "flase")
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.resolve(max_retries=-1)
    msg = str(exc.value)
    assert "timeout_s is 'abc', set in the environment (DONKEY_TIMEOUT_S)" in msg
    assert "telemetry is 'flase'" in msg
    assert "max_retries is -1, set in code" in msg


def test_unknown_overrides_are_all_named(layers: dict[str, Path]) -> None:
    with pytest.raises(TypeError, match="'timeout', 'tlemetry'"):
        DonkeyConfig.resolve(timeout=1, tlemetry=False)  # type: ignore[call-arg]


def test_cost_override_merges_per_dimension(
    layers: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_COST_TEAM", "env-team")
    monkeypatch.setenv("DONKEY_COST_ENV", "prod")
    cfg = DonkeyConfig.resolve(cost=CostTags(team="code-team"))
    assert cfg.cost == CostTags(team="code-team", env="prod")
    assert cfg.source_of("cost.team").kind == "explicit"
    assert cfg.source_of("cost.env").kind == "env"


def test_from_env_is_resolve_without_arguments(
    layers: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(layers["project"], environment="Production")
    monkeypatch.setenv("DONKEY_TIMEOUT_S", "8")
    assert DonkeyConfig.from_env() == DonkeyConfig.resolve()


# --- Donkey.from_env(**overrides) ----------------------------------------------------------------


def test_donkey_from_env_accepts_every_config_field() -> None:
    accepted = set(inspect.signature(Donkey.from_env).parameters)
    hinted = set(get_type_hints(ConfigOverrides))
    public = {f.name for f in fields(DonkeyConfig) if not f.name.startswith("_")}
    assert hinted == public
    assert {"team", "project", "env", "enduser_id", "path", "overrides"} <= accepted


@pytest.mark.usefixtures("layers")
def test_donkey_from_env_forwards_overrides_and_path(tmp_path: Path) -> None:
    chosen = tmp_path / "cfg.toml"
    _write(chosen, environment="from-path")
    donkey = Donkey.from_env(
        path=chosen,
        timeout_s=2.5,
        telemetry_capture_content=True,
        on_model_substitution="raise",
        team="support",
    )
    try:
        cfg = donkey.config
        assert cfg.environment == "from-path"
        assert (cfg.timeout_s, cfg.telemetry_capture_content) == (2.5, True)
        assert cfg.on_model_substitution == "raise"
        assert cfg.cost.team == "support"
        assert cfg.source_of("timeout_s").kind == "explicit"
    finally:
        donkey.close()


@pytest.mark.usefixtures("layers")
def test_donkey_from_env_cost_shorthands_merge_over_a_cost_override() -> None:
    donkey = Donkey.from_env(cost=CostTags(team="a", project="p"), team="b")
    try:
        assert donkey.config.cost == CostTags(team="b", project="p")
    finally:
        donkey.close()


@pytest.mark.parametrize("spec", config_module._FIELDS, ids=lambda spec: spec.name)
def test_donkey_from_env_sets_each_field_in_code(
    layers: dict[str, Path], monkeypatch: pytest.MonkeyPatch, spec: config_module._Field
) -> None:
    # Each field's default, passed as a kwarg over a different env value: the
    # kwarg wins and the field counts as set in code.
    default = {f.name: f.default for f in fields(DonkeyConfig)}[spec.name]
    if spec.name == "region":
        monkeypatch.setenv(spec.env, "eu")
    elif spec.check is None and spec.name not in {"llm_proxy_url", "base_url"}:
        monkeypatch.setenv(spec.env, "X-From-Env")
    donkey = Donkey.from_env(**{spec.name: default})  # type: ignore[misc]
    try:
        assert getattr(donkey.config, spec.name) == default
        assert donkey.config.source_of(spec.name).kind == "explicit"
    finally:
        donkey.close()


@pytest.mark.usefixtures("layers")
def test_donkey_from_env_reports_a_non_cost_tags_cost_with_a_shorthand() -> None:
    with pytest.raises(ConfigError, match="cost must be a CostTags"):
        Donkey.from_env(cost=None, team="t")  # type: ignore[arg-type]


def test_resolve_and_with_overrides_hints_resolve_at_runtime() -> None:
    # Doc tools and the public API surface check resolve these annotations.
    assert "overrides" in get_type_hints(DonkeyConfig.resolve)
    assert "kw" in get_type_hints(DonkeyConfig.with_overrides)


# --- merging the user file keeps the endpoint rule -----------------------------------------------


def test_user_file_credentials_are_not_sent_to_a_project_file_url(
    layers: dict[str, Path],
) -> None:
    # The user file now merges beneath the project file (#727). Its credentials
    # must still not reach a URL that the project file names.
    _write(layers["user"], llm_proxy_client_id="cid", llm_proxy_client_secret="user-secret")
    _write(layers["project"], llm_proxy_url="https://proxy.example.com/agent/")
    cfg = DonkeyConfig.resolve()
    assert cfg.source_of("llm_proxy_client_secret").kind == "user"
    with pytest.raises(ConfigError, match="Not sending llm_proxy_client_id") as exc:
        cfg.validated(need="llm")
    assert "user-secret" not in str(exc.value)


def test_a_path_file_counts_as_a_project_file_for_the_endpoint_rule(
    layers: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "deploy.toml"
    _write(chosen, llm_proxy_url="https://proxy.example.com/agent/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "env-secret")
    cfg = DonkeyConfig.resolve(path=chosen)
    assert cfg.source_of("llm_proxy_url") == config_module.ConfigSource("project", chosen)
    with pytest.raises(ConfigError, match="Not sending llm_proxy_client_id") as exc:
        cfg.validated(need="llm")
    # The file may live anywhere, so the message must not call it the working directory's.
    assert str(chosen) in str(exc.value)
    assert "working directory" not in str(exc.value)
    assert "credentials from outside the project config files" in str(exc.value)
    # Credentials in the overlay beside the named file are sent.
    _write(
        tmp_path / ".donkey-kit.local.toml",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="local-secret",
    )
    monkeypatch.delenv("DONKEY_LLM_PROXY_CLIENT_ID")
    monkeypatch.delenv("DONKEY_LLM_PROXY_CLIENT_SECRET")
    DonkeyConfig.resolve(path=chosen).validated(need="llm")


# --- the secrets warning points at the caller ----------------------------------------------------


def _donkey_from_env() -> None:
    Donkey.from_env().close()


@pytest.mark.parametrize(
    "build",
    [DonkeyConfig.resolve, DonkeyConfig.from_env, _donkey_from_env],
    ids=["resolve", "DonkeyConfig.from_env", "Donkey.from_env"],
)
def test_the_secrets_warning_names_the_callers_file(
    layers: dict[str, Path], build: Callable[[], object]
) -> None:
    _write(layers["project"], client_secret="s")
    with pytest.warns(config_module.ConfigWarning, match="client_secret") as record:
        build()
    # #1010: `record` holds every warning raised in the block, including another
    # package's, depending on what a lazy first import emits. Check ours only.
    ours = [w for w in record if issubclass(w.category, config_module.ConfigWarning)]
    seen = [f"{w.category.__name__} {w.filename}: {w.message}" for w in record]
    assert {Path(w.filename).name for w in ours} == {Path(__file__).name}, seen
