"""Config values are checked on construction (#809): an out-of-range number, a
non-boolean switch or an unknown choice raises ONE :class:`ConfigError` listing
every bad field, and no builtin ``ValueError``, ``KeyError`` or
``AssertionError`` escapes — from code, ``with_overrides``, ``from_env`` or the
transport's retry loop."""

from __future__ import annotations

import dataclasses

import httpx
import pytest
from typer.testing import CliRunner

from donkey_kit.cli import app
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient

_CHECKED_ENV = (
    "ANYPOINT_REGION",
    "DONKEY_LLM_PROXY_AUTH",
    "DONKEY_ON_MODEL_SUBSTITUTION",
    "DONKEY_TIMEOUT_S",
    "DONKEY_MAX_RETRIES",
    "DONKEY_REGISTRY_CACHE_TTL_S",
    "DONKEY_TELEMETRY",
    "DONKEY_TELEMETRY_CAPTURE_CONTENT",
    "DONKEY_SEND_COST_HEADERS",
)


@pytest.fixture
def clean_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No config file and none of the checked env vars set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for var in _CHECKED_ENV:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# --- in code ---------------------------------------------------------------------


def test_every_bad_field_is_listed_in_one_error() -> None:
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig(
            region="mars",  # type: ignore[arg-type]
            llm_proxy_auth="oauth",  # type: ignore[arg-type]
            on_model_substitution="explode",  # type: ignore[arg-type]
            timeout_s=0,
            max_retries=-1,
            registry_cache_ttl_s=-5,
        )
    msg = str(exc.value)
    for fragment in (
        "region is 'mars'",
        "llm_proxy_auth is 'oauth'",
        "on_model_substitution is 'explode'",
        "timeout_s is 0",
        "max_retries is -1",
        "registry_cache_ttl_s is -5",
    ):
        assert fragment in msg
    assert "set in code" in msg
    assert exc.value.remediation


def test_defaults_and_boundary_values_are_accepted() -> None:
    DonkeyConfig()
    DonkeyConfig(max_retries=0, registry_cache_ttl_s=0, timeout_s=0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_s": -1.0},
        {"timeout_s": float("nan")},
        {"timeout_s": "60"},
        {"timeout_s": True},
        {"max_retries": 1.5},
        {"max_retries": True},
        {"registry_cache_ttl_s": "300"},
        {"telemetry": "false"},
        {"telemetry_capture_content": 1},
        {"send_cost_headers": None},
    ],
)
def test_wrong_type_or_range_is_a_config_error(kwargs: dict[str, object]) -> None:
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig(**kwargs)  # type: ignore[arg-type]
    assert next(iter(kwargs)) in str(exc.value)


def test_with_overrides_revalidates() -> None:
    with pytest.raises(ConfigError, match="max_retries is -1"):
        DonkeyConfig().with_overrides(max_retries=-1)
    with pytest.raises(ConfigError, match="region is 'mars'"):
        DonkeyConfig().with_overrides(region="mars")  # type: ignore[typeddict-item]


def test_dataclasses_replace_revalidates() -> None:
    with pytest.raises(ConfigError, match="timeout_s"):
        dataclasses.replace(DonkeyConfig(), timeout_s=0)


def test_an_unknown_region_never_reaches_control_plane_url() -> None:
    # Before #809 this config was accepted and control_plane_url raised KeyError.
    with pytest.raises(ConfigError):
        DonkeyConfig(region="mars").control_plane_url  # type: ignore[arg-type]  # noqa: B018


# --- from the environment and config files -----------------------------------------


def test_unparseable_env_numbers_raise_config_error(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DONKEY_TIMEOUT_S", "abc")
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    msg = str(exc.value)
    assert "timeout_s is 'abc'" in msg
    assert "DONKEY_TIMEOUT_S" in msg
    assert "expected a number" in msg


def test_misspelt_boolean_raises_config_error(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DONKEY_TELEMETRY", "flase")
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    assert "telemetry is 'flase'" in str(exc.value)
    assert "DONKEY_TELEMETRY" in str(exc.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("TRUE", True), (" yes ", True), ("on", True),
     ("0", False), ("False", False), ("no", False), ("off", False)],
)
def test_boolean_tokens_parse(clean_env: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    clean_env.setenv("DONKEY_TELEMETRY", raw)
    assert DonkeyConfig.from_env().telemetry is expected


def test_parse_and_range_errors_are_reported_together(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("DONKEY_TIMEOUT_S", "abc")
    clean_env.setenv("DONKEY_MAX_RETRIES", "-1")
    clean_env.setenv("DONKEY_REGISTRY_CACHE_TTL_S", "five")
    clean_env.setenv("DONKEY_SEND_COST_HEADERS", "maybe")
    clean_env.setenv("ANYPOINT_REGION", "mars")
    clean_env.setenv("DONKEY_LLM_PROXY_AUTH", "oauth")
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    msg = str(exc.value)
    for var in (
        "DONKEY_TIMEOUT_S",
        "DONKEY_MAX_RETRIES",
        "DONKEY_REGISTRY_CACHE_TTL_S",
        "DONKEY_SEND_COST_HEADERS",
        "ANYPOINT_REGION",
        "DONKEY_LLM_PROXY_AUTH",
    ):
        assert var in msg


def test_bad_value_in_the_project_file_names_the_file(
    clean_env: pytest.MonkeyPatch, tmp_path
) -> None:
    (tmp_path / ".donkey-kit.toml").write_text("[donkey]\nmax_retries = 2.5\ntelemetry = 'flase'\n")
    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    msg = str(exc.value)
    assert "max_retries is 2.5" in msg
    assert "telemetry is 'flase'" in msg
    assert str(tmp_path / ".donkey-kit.toml") in msg


def test_donkey_init_reports_a_config_error_not_a_value_error(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("DONKEY_TIMEOUT_S", "abc")
    result = CliRunner().invoke(app, ["init"])
    assert isinstance(result.exception, ConfigError)


# --- the transport's retry loop -----------------------------------------------------


def _negative_retries() -> DonkeyConfig:
    """A config whose max_retries was forced below 0 after construction, which
    ``DonkeyConfig`` itself refuses."""
    cfg = DonkeyConfig()
    object.__setattr__(cfg, "max_retries", -1)
    return cfg


def _never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError("no request should be sent")


async def test_async_retry_loop_with_no_attempts_raises_config_error() -> None:
    async with DonkeyAsyncClient(
        _negative_retries(), None, transport=httpx.MockTransport(_never_called)
    ) as client:
        with pytest.raises(ConfigError, match="max_retries is -1"):
            await client.get("https://proxy/thing")


def test_sync_retry_loop_with_no_attempts_raises_config_error() -> None:
    with DonkeyClient(
        _negative_retries(), transport=httpx.MockTransport(_never_called)
    ) as client:
        with pytest.raises(ConfigError, match="max_retries is -1"):
            client.get("https://proxy/thing")
