"""Credential binding on every credentialed path of the per-plane clients.

Each path that carries a credential checks its endpoint against where the
credential came from (config resolution, BG §1.1):

* the control-plane token request (connected-app secret to ``base_url``);
* control-plane calls such as the registry (bearer token to ``base_url``);
* the data plane (client-id pair, ``llm_proxy_key`` or the ``llm_auth`` JWT to
  ``llm_proxy_url``), through the LLM client and every adapter.

Plus: ``send_cost_headers`` resolves from every config file layer, and neither
``ConfigError`` text nor ``donkey doctor`` output carries a secret value.

Every test runs in a temp working directory with ``XDG_CONFIG_HOME`` pointed at
a temp dir and every ``ANYPOINT_*`` / ``DONKEY_*`` variable cleared.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.cli import doctor
from donkey_kit.cli.doctor import ProbeResult, run_diagnostics
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.integrations import ADAPTERS

_TOML = ".donkey-kit.toml"
_LOCAL_TOML = ".donkey-kit.local.toml"
_PROJECT_CP = "https://cp.example.test"
_PROJECT_LLM = "https://llm.example.test/proxy/"

_CP_SECRET = "cp-secret-value-1"
_LLM_SECRET = "llm-secret-value-2"
_LLM_KEY = "llm-key-value-3"
_CODE_TOKEN = "code-token-value-4"
_SECRETS = (_CP_SECRET, _LLM_SECRET, _LLM_KEY, _CODE_TOKEN)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return project_dir


def _write(path: Path, body: str) -> None:
    path.write_text("[donkey]\n" + body)


def _env_control_plane_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "env-cid")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", _CP_SECRET)
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org")


def _env_llm_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "env-llm-cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", _LLM_SECRET)


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})


class _CountingToken:
    def __init__(self, value: str) -> None:
        self.value = value
        self.calls = 0

    async def token(self) -> str:
        self.calls += 1
        return self.value

    async def invalidate(self) -> None:
        return None


# --- control plane: the token request and registry calls ---------------------


async def test_registry_refuses_a_project_base_url_with_env_credentials(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, f'base_url = "{_PROJECT_CP}"\n')
    _env_control_plane_creds(monkeypatch)

    async with Donkey.from_env() as donkey:
        with pytest.raises(ConfigError, match="cp.example.test"):
            await donkey.registry.search(query="x")


async def test_control_plane_call_sends_neither_secret_nor_token_to_a_project_base_url(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control-plane client's default provider is the one built by
    ``AnypointConnectedApp.from_config``: its check runs before the token POST
    on the control-plane token-fetch client."""
    _write(project / _TOML, f'base_url = "{_PROJECT_CP}"\n')
    _env_control_plane_creds(monkeypatch)
    token_endpoint, platform = _Recorder(), _Recorder()

    async with Donkey.from_env() as donkey:
        assert donkey._owned_auth_http is not None
        assert donkey._owned_auth_http._control_plane
        donkey._owned_auth_http._swap_transport(httpx.MockTransport(token_endpoint))
        donkey.registry._http._swap_transport(httpx.MockTransport(platform))
        with pytest.raises(ConfigError, match="cp.example.test"):
            await donkey.registry._http.get(f"{_PROJECT_CP}/exchange/api/v2/assets")

    assert token_endpoint.requests == []
    assert platform.requests == []


async def test_control_plane_call_with_file_credentials_reaches_a_project_base_url(
    project: Path,
) -> None:
    _write(project / _TOML, f'base_url = "{_PROJECT_CP}"\nclient_id = "file-cid"\n')
    _write(project / _LOCAL_TOML, f'client_secret = "{_CP_SECRET}"\n')
    token_endpoint, platform = _Recorder(), _Recorder()

    async with Donkey.from_env() as donkey:
        assert donkey._owned_auth_http is not None
        donkey._owned_auth_http._swap_transport(httpx.MockTransport(token_endpoint))
        donkey.registry._http._swap_transport(httpx.MockTransport(platform))
        await donkey.registry._http.get(f"{_PROJECT_CP}/exchange/api/v2/assets")

    assert len(token_endpoint.requests) == 1
    (request,) = platform.requests
    assert request.headers["authorization"] == "Bearer tok"


async def test_code_auth_provider_token_is_not_sent_to_a_project_base_url(
    project: Path,
) -> None:
    """A provider passed as ``Donkey(auth=...)`` is set in code, so its token
    counts as coming from outside the project files, like the ``llm_auth`` JWT."""
    _write(project / _TOML, f'base_url = "{_PROJECT_CP}"\nclient_id = "file-cid"\n')
    _write(project / _LOCAL_TOML, f'client_secret = "{_CP_SECRET}"\norg_id = "org"\n')
    platform = _Recorder()
    provider = _CountingToken(_CODE_TOKEN)

    async with Donkey(DonkeyConfig.from_env(), auth=provider) as donkey:
        donkey.registry._http._swap_transport(httpx.MockTransport(platform))
        with pytest.raises(ConfigError, match="cp.example.test") as exc:
            await donkey.registry._http.get(f"{_PROJECT_CP}/exchange/api/v2/assets")

    assert platform.requests == []
    assert provider.calls == 0
    assert "Donkey(auth=" in str(exc.value)
    assert _CODE_TOKEN not in str(exc.value)


async def test_code_auth_provider_token_reaches_an_env_base_url(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANYPOINT_BASE_URL", _PROJECT_CP)
    platform = _Recorder()

    async with Donkey(DonkeyConfig.from_env(), auth=StaticToken(_CODE_TOKEN)) as donkey:
        donkey.registry._http._swap_transport(httpx.MockTransport(platform))
        await donkey.registry._http.get(f"{_PROJECT_CP}/exchange/api/v2/assets")

    (request,) = platform.requests
    assert request.headers["authorization"] == f"Bearer {_CODE_TOKEN}"


async def test_code_auth_provider_token_reaches_a_standard_anypoint_host(
    project: Path,
) -> None:
    _write(project / _TOML, 'base_url = "https://anypoint.mulesoft.com"\n')
    platform = _Recorder()

    async with Donkey(DonkeyConfig.from_env(), auth=StaticToken(_CODE_TOKEN)) as donkey:
        donkey.registry._http._swap_transport(httpx.MockTransport(platform))
        await donkey.registry._http.get("https://anypoint.mulesoft.com/exchange/api/v2/assets")

    (request,) = platform.requests
    assert request.headers["authorization"] == f"Bearer {_CODE_TOKEN}"


# --- data plane --------------------------------------------------------------


def test_env_llm_proxy_key_is_not_sent_to_a_project_llm_proxy_url(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        project / _TOML,
        f'llm_proxy_url = "{_PROJECT_LLM}"\nllm_proxy_client_id = "file-cid"\n',
    )
    _write(project / _LOCAL_TOML, f'llm_proxy_client_secret = "{_LLM_SECRET}"\n')
    monkeypatch.setenv("DONKEY_LLM_PROXY_KEY", _LLM_KEY)

    with pytest.raises(ConfigError, match="llm_proxy_key") as exc:
        DonkeyConfig.from_env().validated(need="llm")
    assert _LLM_KEY not in str(exc.value)


def test_llm_auth_jwt_is_not_fetched_for_a_project_llm_proxy_url(project: Path) -> None:
    pytest.importorskip("openai")
    _write(
        project / _TOML,
        f'llm_proxy_url = "{_PROJECT_LLM}"\n'
        'llm_proxy_auth = "jwt"\n'
        'llm_proxy_wallet_client_id = "wallet"\n',
    )
    jwt = _CountingToken(_CODE_TOKEN)

    donkey = Donkey(DonkeyConfig.from_env(), llm_auth=jwt)
    try:
        with pytest.raises(ConfigError, match="llm.example.test"):
            donkey.openai()
    finally:
        donkey.close()
    assert jwt.calls == 0


@pytest.mark.parametrize("name", sorted(ADAPTERS))
def test_every_adapter_refuses_a_project_llm_proxy_url_with_env_credentials(
    name: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, f'llm_proxy_url = "{_PROJECT_LLM}"\n')
    _env_llm_creds(monkeypatch)
    cfg = DonkeyConfig.from_env()
    spec = ADAPTERS[name]
    cls = getattr(importlib.import_module(spec.module, "donkey_kit.integrations"), spec.cls)
    adapter = cls(cfg, DonkeyAsyncClient(cfg, None))

    with pytest.raises(ConfigError, match="llm.example.test") as exc:
        adapter.connection_kwargs()
    assert _LLM_SECRET not in str(exc.value)


# --- send_cost_headers from every file layer ---------------------------------


@pytest.mark.parametrize("layer", ["project", "local"])
def test_send_cost_headers_loads_from_the_working_directory_files(
    layer: str, project: Path
) -> None:
    _write(project / (_TOML if layer == "project" else _LOCAL_TOML), "send_cost_headers = true\n")

    cfg = DonkeyConfig.from_env()

    assert cfg.send_cost_headers is True
    assert cfg.source_of("send_cost_headers").kind == layer


def test_send_cost_headers_loads_from_the_user_file(project: Path) -> None:
    _write(Path(os.environ["XDG_CONFIG_HOME"]) / _TOML, "send_cost_headers = true\n")

    cfg = DonkeyConfig.from_env()

    assert cfg.send_cost_headers is True
    assert cfg.source_of("send_cost_headers").kind == "user"


def test_local_overlay_can_turn_send_cost_headers_back_off(project: Path) -> None:
    _write(project / _TOML, "send_cost_headers = true\n")
    _write(project / _LOCAL_TOML, "send_cost_headers = false\n")

    assert DonkeyConfig.from_env().send_cost_headers is False


# --- no secret values in ConfigError text or doctor output -------------------


def _all_env_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_KEY", _LLM_KEY)


def _assert_no_secret(text: str) -> None:
    for secret in _SECRETS:
        assert secret not in text


@pytest.mark.parametrize(
    ("toml", "need"),
    [
        (f'base_url = "{_PROJECT_CP}"\n', "control_plane"),
        (f'llm_proxy_url = "{_PROJECT_LLM}"\n', "llm"),
        ('base_url = "http://cp.example.test"\n', "control_plane"),
        ('llm_proxy_url = "http://llm.example.test/"\n', "llm"),
    ],
)
def test_config_errors_name_credentials_but_never_their_values(
    toml: str, need: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, toml)
    _all_env_secrets(monkeypatch)

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env().validated(need=need)

    _assert_no_secret(str(exc.value))
    _assert_no_secret(repr(exc.value))


def test_missing_field_errors_never_carry_secret_values(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", _CP_SECRET)
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", _LLM_SECRET)
    monkeypatch.setenv("DONKEY_LLM_PROXY_KEY", _LLM_KEY)

    for need in ("control_plane", "llm"):
        with pytest.raises(ConfigError) as exc:
            DonkeyConfig.from_env().validated(need=need)
        _assert_no_secret(str(exc.value))


def test_doctor_output_with_binding_failures_never_carries_secret_values(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, f'base_url = "{_PROJECT_CP}"\nllm_proxy_url = "{_PROJECT_LLM}"\n')
    _all_env_secrets(monkeypatch)

    def _no_probe(_c: object, _m: object) -> ProbeResult:
        raise AssertionError("no request may be sent to a project-file host")

    checks = run_diagnostics("gpt-4o", probe=_no_probe)

    assert doctor.has_failure(checks)
    _assert_no_secret(doctor.format_report(checks))
    _assert_no_secret(json.dumps([vars(c) for c in checks], default=str))
