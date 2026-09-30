"""Endpoint scheme checks and credential-to-endpoint binding (config resolution).

Three layers of behaviour, each exercised through the documented surfaces
(``DonkeyConfig.from_env()`` / ``validated()``, ``Donkey.from_env()``, and the
connected-app token fetch):

* **Scheme** — ``base_url``, ``llm_proxy_url`` and the token endpoint must be
  ``https://``; plain ``http://`` is accepted for loopback hosts (the local
  simulator and local development), and for other hosts only with
  ``DONKEY_ALLOW_HTTP=1`` in the environment, which warns.
* **Binding** — an endpoint read from the working directory's
  ``.donkey-kit.toml`` (or its ``.local`` overlay) only receives credentials
  from that same file pair, unless the host is a standard Anypoint control-plane
  host or ``DONKEY_TRUST_PROJECT_CONFIG=1`` is set. Loopback hosts get no
  exemption here: they are exempt from the https rule only.
* **Overlay** — ``.donkey-kit.local.toml`` is layered over ``.donkey-kit.toml``;
  environment variables still win over both.

Every test runs in a temp working directory with ``XDG_CONFIG_HOME`` pointed at
a temp dir and every ``ANYPOINT_*`` / ``DONKEY_*`` variable cleared, so the
developer's real config is never read.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import json
import os
import threading
import warnings
from collections.abc import Callable, Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import config as config_module
from donkey_kit.core.auth import AnypointConnectedApp
from donkey_kit.core.config import ConfigWarning, DonkeyConfig
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import ConfigError

_TOML = ".donkey-kit.toml"
_LOCAL_TOML = ".donkey-kit.local.toml"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project dir as cwd, an empty user config dir, and a clean env."""
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
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org")


def _env_llm_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "env-llm-cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "env-llm-secret")


def _recording_client(seen: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- scheme -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://llm.example.test/proxy/", "ftp://llm.example.test", "llm.example.test"]
)
def test_non_https_remote_llm_proxy_url_is_rejected(url: str) -> None:
    cfg = DonkeyConfig(
        llm_proxy_url=url, llm_proxy_client_id="cid", llm_proxy_client_secret="secret"
    )
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need="llm")
    msg = str(exc.value)
    assert "llm_proxy_url" in msg
    assert "https://" in msg


def test_plain_http_remote_base_url_is_rejected() -> None:
    cfg = DonkeyConfig(
        client_id="cid",
        client_secret="secret",
        org_id="org",
        base_url="http://cp.example.test",
    )
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need="control_plane")
    assert "base_url" in str(exc.value)
    assert "https://" in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080",
        "http://LOCALHOST:8080/",
        "http://127.0.0.1:8080",
        "http://127.10.20.30:9000/proxy/",
        "http://[::1]:8080",
    ],
)
def test_plain_http_loopback_endpoints_are_allowed(url: str) -> None:
    cfg = DonkeyConfig(
        client_id="cid",
        client_secret="secret",
        org_id="org",
        base_url=url,
        llm_proxy_url=url,
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
    )
    assert cfg.validated(need="llm") is cfg
    assert cfg.validated(need="control_plane") is cfg


async def test_token_request_refuses_plain_http_remote_endpoint() -> None:
    seen: list[httpx.Request] = []
    async with _recording_client(seen) as http_client:
        auth = AnypointConnectedApp(
            client_id="cid",
            client_secret="secret",
            control_plane_url="http://cp.example.test",
            http_client=http_client,
        )
        with pytest.raises(ConfigError) as exc:
            await auth.token()
    assert "https://" in str(exc.value)
    assert seen == []  # the credentials were never posted


async def test_token_request_allows_plain_http_loopback_endpoint() -> None:
    seen: list[httpx.Request] = []
    async with _recording_client(seen) as http_client:
        auth = AnypointConnectedApp(
            client_id="cid",
            client_secret="secret",
            control_plane_url="http://127.0.0.1:8081",
            http_client=http_client,
        )
        assert await auth.token() == "tok"
    assert len(seen) == 1


# --- DONKEY_ALLOW_HTTP: env-only switch for plain http to other hosts ---------


def _remote_http_config() -> DonkeyConfig:
    return DonkeyConfig(
        client_id="cid",
        client_secret="secret",
        org_id="org",
        base_url="http://cp.example.test",
        llm_proxy_url="http://llm.example.test/proxy/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
    )


def test_plain_http_error_names_the_allow_http_switch(project: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        _remote_http_config().validated(need="llm")
    msg = str(exc.value)
    assert "https://" in msg
    assert "DONKEY_ALLOW_HTTP=1" in msg


@pytest.mark.parametrize("value", ["1", "true"])
@pytest.mark.parametrize(
    ("need", "key", "host"),
    [
        ("llm", "llm_proxy_url", "llm.example.test"),
        ("control_plane", "base_url", "cp.example.test"),
    ],
)
def test_allow_http_switch_lets_remote_http_through_with_a_warning(
    project: Path, monkeypatch: pytest.MonkeyPatch, value: str, need: str, key: str, host: str
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", value)
    cfg = _remote_http_config()

    with pytest.warns(ConfigWarning) as record:
        assert cfg.validated(need=need) is cfg
    messages = [str(w.message) for w in record]
    assert any(key in m and host in m and "DONKEY_ALLOW_HTTP" in m for m in messages)


async def test_allow_http_switch_lets_the_token_request_through(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", "1")
    seen: list[httpx.Request] = []
    async with _recording_client(seen) as http_client:
        auth = AnypointConnectedApp(
            client_id="cid",
            client_secret="secret",
            control_plane_url="http://cp.example.test",
            http_client=http_client,
        )
        with pytest.warns(ConfigWarning, match="token endpoint"):
            assert await auth.token() == "tok"
    assert [r.url.host for r in seen] == ["cp.example.test"]


def test_allow_http_switch_off_values_keep_the_check(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", "0")
    with pytest.raises(ConfigError, match="https://"):
        _remote_http_config().validated(need="llm")


def test_allow_http_switch_does_not_accept_other_schemes(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", "1")
    cfg = DonkeyConfig(
        llm_proxy_url="ftp://llm.example.test",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
    )
    with pytest.raises(ConfigError, match="https://"):
        cfg.validated(need="llm")


def test_loopback_http_does_not_warn_with_the_switch_on(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", "1")
    cfg = DonkeyConfig(
        llm_proxy_url="http://127.0.0.1:8080",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert cfg.validated(need="llm") is cfg


@pytest.mark.parametrize("filename", [_TOML, _LOCAL_TOML])
@pytest.mark.parametrize("line", ['allow_http = true\n', 'DONKEY_ALLOW_HTTP = "1"\n'])
def test_allow_http_switch_cannot_come_from_a_config_file(
    project: Path, filename: str, line: str
) -> None:
    body = (
        'llm_proxy_url = "http://llm.example.test/proxy/"\n'
        'llm_proxy_client_id = "cid"\n'
        'llm_proxy_client_secret = "secret"\n'
    )
    _write(project / filename, body + line)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = DonkeyConfig.from_env()
    with pytest.raises(ConfigError, match="https://"):
        cfg.validated(need="llm")


def test_allow_http_switch_does_not_change_the_binding_rule(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", "1")
    _write(project / _TOML, 'llm_proxy_url = "http://llm.example.test/proxy/"\n')
    _env_llm_creds(monkeypatch)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with pytest.raises(ConfigError) as exc:
            DonkeyConfig.from_env().validated(need="llm")
    msg = str(exc.value)
    assert str(project / _TOML) in msg
    assert "DONKEY_TRUST_PROJECT_CONFIG" in msg


# --- binding: project-file endpoints vs. credentials from elsewhere ---------


def test_project_base_url_with_env_credentials_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'base_url = "https://cp.example.test"\n')
    _env_control_plane_creds(monkeypatch)

    cfg = DonkeyConfig.from_env()
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need="control_plane")

    msg = str(exc.value)
    assert str(project / _TOML) in msg
    assert "base_url" in msg
    assert "cp.example.test" in msg
    # The three ways out.
    assert "ANYPOINT_BASE_URL" in msg
    assert _LOCAL_TOML in msg
    assert "DONKEY_TRUST_PROJECT_CONFIG=1" in msg


async def test_project_base_url_with_env_credentials_sends_no_token_request(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'base_url = "https://cp.example.test"\n')
    _env_control_plane_creds(monkeypatch)

    seen: list[httpx.Request] = []
    async with _recording_client(seen) as http_client:
        auth = AnypointConnectedApp.from_config(DonkeyConfig.from_env(), http_client=http_client)
        with pytest.raises(ConfigError, match="cp.example.test"):
            await auth.token()
    assert seen == []


async def test_donkey_from_env_sends_no_credentials_to_a_project_base_url(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through ``Donkey.from_env()``: whatever path would first need a
    control-plane token, nothing reaches the project file's host."""
    pytest.importorskip("openai")
    _write(project / _TOML, 'base_url = "https://cp.example.test"\n')
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/proxy/")

    seen: list[httpx.Request] = []

    async def _no_network(
        self: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        seen.append(request)
        return httpx.Response(599)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _no_network)

    donkey = Donkey.from_env()
    try:
        with contextlib.suppress(Exception):
            await donkey.openai().responses.create(model="m", input="ping")
    finally:
        await donkey.aclose()
    assert [r.url.host for r in seen if r.url.host == "cp.example.test"] == []


def test_project_llm_proxy_url_with_env_credentials_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'llm_proxy_url = "https://llm.example.test/proxy/"\n')
    _env_llm_creds(monkeypatch)

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env().validated(need="llm")
    msg = str(exc.value)
    assert str(project / _TOML) in msg
    assert "llm_proxy_url" in msg
    assert "llm.example.test" in msg
    assert "DONKEY_LLM_PROXY_URL" in msg


def test_donkey_llm_client_refuses_project_url_with_env_credentials(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'llm_proxy_url = "https://llm.example.test/proxy/"\n')
    _env_llm_creds(monkeypatch)

    donkey = Donkey.from_env()
    try:
        with pytest.raises(ConfigError, match="llm.example.test"):
            donkey.openai()
    finally:
        donkey.close()


def test_project_llm_proxy_url_with_mixed_scope_credentials_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The id may live in the project file; a secret from the env still binds."""
    _write(
        project / _TOML,
        'llm_proxy_url = "https://llm.example.test/"\nllm_proxy_client_id = "cid"\n',
    )
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "env-llm-secret")

    with pytest.raises(ConfigError, match="llm_proxy_client_secret"):
        DonkeyConfig.from_env().validated(need="llm")


def test_project_llm_proxy_url_in_jwt_mode_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In jwt mode the bearer JWT comes from the caller's AuthProvider, which is
    never part of the project file's scope."""
    _write(
        project / _TOML,
        'llm_proxy_url = "https://llm.example.test/"\n'
        'llm_proxy_auth = "jwt"\n'
        'llm_proxy_wallet_client_id = "wallet"\n',
    )
    with pytest.raises(ConfigError, match="llm.example.test"):
        DonkeyConfig.from_env().validated(need="llm")


def test_project_base_url_does_not_block_llm_only_use(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'base_url = "https://cp.example.test"\n')
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/")

    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="llm") is cfg


def test_trust_opt_in_allows_project_endpoints(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        project / _TOML,
        'base_url = "https://cp.example.test"\nllm_proxy_url = "https://llm.example.test/"\n',
    )
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")

    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="control_plane") is cfg
    assert cfg.validated(need="llm") is cfg


async def test_trust_opt_in_lets_the_token_request_through(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'base_url = "https://cp.example.test"\n')
    _env_control_plane_creds(monkeypatch)
    monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")

    seen: list[httpx.Request] = []
    async with _recording_client(seen) as http_client:
        auth = AnypointConnectedApp.from_config(DonkeyConfig.from_env(), http_client=http_client)
        assert await auth.token() == "tok"
    assert [r.url.host for r in seen] == ["cp.example.test"]


def test_trust_opt_in_cannot_come_from_the_project_file(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        project / _TOML,
        'base_url = "https://cp.example.test"\ntrust_project_config = true\n',
    )
    _env_control_plane_creds(monkeypatch)
    with pytest.raises(ConfigError):
        DonkeyConfig.from_env().validated(need="control_plane")


def test_credentials_in_local_overlay_are_allowed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        project / _TOML,
        'org_id = "org"\nbase_url = "https://cp.example.test"\n'
        'llm_proxy_url = "https://llm.example.test/"\n',
    )
    _write(
        project / _LOCAL_TOML,
        'client_id = "cid"\nclient_secret = "secret"\n'
        'llm_proxy_client_id = "llm-cid"\nllm_proxy_client_secret = "llm-secret"\n',
    )

    cfg = DonkeyConfig.from_env()
    assert cfg.client_secret == "secret"
    assert cfg.validated(need="control_plane") is cfg
    assert cfg.validated(need="llm") is cfg


def test_endpoint_in_local_overlay_with_env_credentials_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The overlay lives in the working directory too, so it gets the same rule."""
    _write(project / _LOCAL_TOML, 'llm_proxy_url = "https://llm.example.test/"\n')
    _env_llm_creds(monkeypatch)

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env().validated(need="llm")
    assert str(project / _LOCAL_TOML) in str(exc.value)


def test_env_url_with_env_credentials_is_allowed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        project / _TOML,
        'base_url = "https://cp.example.test"\nllm_proxy_url = "https://llm.example.test/"\n',
    )
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    monkeypatch.setenv("ANYPOINT_BASE_URL", "https://cp.env.test")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.env.test/")

    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="control_plane") is cfg
    assert cfg.validated(need="llm") is cfg


@pytest.mark.parametrize(
    "host", ["https://anypoint.mulesoft.com", "https://eu1.anypoint.mulesoft.com/"]
)
def test_project_base_url_on_a_standard_anypoint_host_is_allowed(
    project: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    _write(project / _TOML, f'base_url = "{host}"\n')
    _env_control_plane_creds(monkeypatch)

    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="control_plane") is cfg


def test_lookalike_anypoint_host_is_not_treated_as_standard(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'base_url = "https://anypoint.mulesoft.com.example.test"\n')
    _env_control_plane_creds(monkeypatch)
    with pytest.raises(ConfigError):
        DonkeyConfig.from_env().validated(need="control_plane")


# --- loopback: exempt from https-only, not from binding -----------------------


@pytest.fixture
def listener() -> Iterator[tuple[str, list[str]]]:
    """A real HTTP listener on 127.0.0.1 that records every request path and
    answers like a token endpoint."""
    received: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            received.append(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            body = json.dumps({"access_token": "tok", "expires_in": 3600}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _answer

        def log_message(self, *args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", received
    finally:
        server.shutdown()
        server.server_close()


def test_project_loopback_base_url_with_env_credentials_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    url, _ = listener
    _write(project / _TOML, f'base_url = "{url}"\n')
    _env_control_plane_creds(monkeypatch)

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env().validated(need="control_plane")
    msg = str(exc.value)
    assert str(project / _TOML) in msg
    assert "base_url" in msg
    assert "127.0.0.1" in msg


def test_project_loopback_llm_proxy_url_with_env_credentials_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    url, _ = listener
    _write(project / _TOML, f'llm_proxy_url = "{url}/proxy/"\n')
    _env_llm_creds(monkeypatch)

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env().validated(need="llm")
    msg = str(exc.value)
    assert str(project / _TOML) in msg
    assert "llm_proxy_url" in msg
    assert "127.0.0.1" in msg


async def test_project_loopback_token_endpoint_receives_no_env_credentials(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    url, received = listener
    _write(project / _TOML, f'base_url = "{url}"\n')
    _env_control_plane_creds(monkeypatch)

    async with httpx.AsyncClient() as http_client:
        auth = AnypointConnectedApp.from_config(DonkeyConfig.from_env(), http_client=http_client)
        with pytest.raises(ConfigError, match="127.0.0.1"):
            await auth.token()
    assert received == []


async def test_donkey_from_env_sends_nothing_to_a_project_loopback_base_url(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    """The original scenario: the project file points the control plane at a
    local listener and the secrets are in env."""
    pytest.importorskip("openai")
    url, received = listener
    _write(project / _TOML, f'base_url = "{url}"\n')
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/proxy/")

    real = httpx.AsyncHTTPTransport.handle_async_request

    async def _route(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        if request.url.host == "127.0.0.1":
            return await real(self, request)
        return httpx.Response(599)  # the remote LLM proxy is not reachable in tests

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _route)

    donkey = Donkey.from_env()
    try:
        with contextlib.suppress(Exception):
            await donkey.openai().responses.create(model="m", input="ping")
    finally:
        await donkey.aclose()
    assert received == []


def test_donkey_llm_client_refuses_project_loopback_url_with_env_credentials(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    url, received = listener
    _write(project / _TOML, f'llm_proxy_url = "{url}/proxy/"\n')
    _env_llm_creds(monkeypatch)

    donkey = Donkey.from_env()
    try:
        with pytest.raises(ConfigError, match="127.0.0.1"):
            donkey.openai()
    finally:
        donkey.close()
    assert received == []


async def test_env_loopback_url_with_env_credentials_works_over_http(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    url, received = listener
    _write(project / _TOML, 'application_name = "app"\n')
    monkeypatch.setenv("ANYPOINT_BASE_URL", url)
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", f"{url}/proxy/")
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)

    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="llm") is cfg
    async with httpx.AsyncClient() as http_client:
        auth = AnypointConnectedApp.from_config(cfg, http_client=http_client)
        assert await auth.token() == "tok"
    assert len(received) == 1


async def test_code_loopback_url_with_env_credentials_works_over_http(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    url, received = listener
    _write(project / _TOML, f'base_url = "{url}"\n')
    _env_control_plane_creds(monkeypatch)

    cfg = DonkeyConfig.from_env().with_overrides(base_url=url)
    assert cfg.validated(need="control_plane") is cfg
    async with httpx.AsyncClient() as http_client:
        auth = AnypointConnectedApp.from_config(cfg, http_client=http_client)
        assert await auth.token() == "tok"
    assert len(received) == 1


@pytest.mark.parametrize("credential_file", [_TOML, _LOCAL_TOML])
async def test_project_loopback_url_with_file_credentials_works_over_http(
    project: Path, listener: tuple[str, list[str]], credential_file: str
) -> None:
    url, received = listener
    endpoints = f'org_id = "org"\nbase_url = "{url}"\nllm_proxy_url = "{url}/proxy/"\n'
    creds = (
        'client_id = "cid"\nclient_secret = "secret"\n'
        'llm_proxy_client_id = "llm-cid"\nllm_proxy_client_secret = "llm-secret"\n'
    )
    if credential_file == _TOML:
        _write(project / _TOML, endpoints + creds)
    else:
        _write(project / _TOML, endpoints)
        _write(project / _LOCAL_TOML, creds)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="llm") is cfg
    assert cfg.validated(need="control_plane") is cfg
    async with httpx.AsyncClient() as http_client:
        auth = AnypointConnectedApp.from_config(cfg, http_client=http_client)
        assert await auth.token() == "tok"
    assert len(received) == 1


async def test_trust_opt_in_allows_a_project_loopback_url(
    project: Path, monkeypatch: pytest.MonkeyPatch, listener: tuple[str, list[str]]
) -> None:
    url, received = listener
    _write(project / _TOML, f'base_url = "{url}"\nllm_proxy_url = "{url}/proxy/"\n')
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")

    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="llm") is cfg
    async with httpx.AsyncClient() as http_client:
        auth = AnypointConnectedApp.from_config(cfg, http_client=http_client)
        assert await auth.token() == "tok"
    assert len(received) == 1


def test_user_file_endpoint_with_env_credentials_is_allowed(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "xdg" / _TOML, 'llm_proxy_url = "https://llm.example.test/"\n')
    _env_llm_creds(monkeypatch)

    cfg = DonkeyConfig.from_env()
    assert cfg.llm_proxy_url == "https://llm.example.test/"
    assert cfg.validated(need="llm") is cfg


def test_project_file_credentials_with_project_endpoint_are_allowed(
    project: Path,
) -> None:
    _write(
        project / _TOML,
        'llm_proxy_url = "https://llm.example.test/"\n'
        'llm_proxy_client_id = "cid"\nllm_proxy_client_secret = "secret"\n',
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="llm") is cfg


def test_explicit_endpoint_override_is_trusted(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'llm_proxy_url = "https://llm.example.test/"\n')
    _env_llm_creds(monkeypatch)

    cfg = DonkeyConfig.from_env().with_overrides(llm_proxy_url="https://llm.code.test/")
    assert cfg.validated(need="llm") is cfg


def test_config_built_in_code_is_trusted() -> None:
    cfg = DonkeyConfig(
        client_id="cid",
        client_secret="secret",
        org_id="org",
        base_url="https://cp.example.test",
        llm_proxy_url="https://llm.example.test/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
    )
    assert cfg.validated(need="control_plane") is cfg
    assert cfg.validated(need="llm") is cfg


# --- sources follow values: a value changed in code counts as set in code ----

_CODE_SECRET = "code-set-placeholder-4f1c"


def _project_endpoints_with_local_credentials(project: Path) -> DonkeyConfig:
    """Both endpoints in the project file, every credential in the local
    overlay: a combination the binding rule accepts."""
    _write(
        project / _TOML,
        'org_id = "org"\nbase_url = "https://cp.example.test"\n'
        'llm_proxy_url = "https://llm.example.test/"\n',
    )
    _write(
        project / _LOCAL_TOML,
        'client_id = "cid"\nclient_secret = "file-secret"\n'
        'llm_proxy_client_id = "llm-cid"\nllm_proxy_client_secret = "llm-file-secret"\n'
        'llm_proxy_key = "file-key"\n',
    )
    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="control_plane") is cfg
    assert cfg.validated(need="llm") is cfg
    return cfg


@pytest.mark.parametrize(
    ("secret", "need"),
    [
        ("client_secret", "control_plane"),
        ("llm_proxy_client_secret", "llm"),
        ("llm_proxy_key", "llm"),
    ],
)
def test_secret_replaced_in_code_is_not_sent_to_a_project_url(
    project: Path, secret: str, need: str
) -> None:
    cfg = dataclasses.replace(
        _project_endpoints_with_local_credentials(project), **{secret: _CODE_SECRET}
    )

    assert cfg.source_of(secret).kind == "explicit"
    with pytest.raises(ConfigError) as exc:
        cfg.validated(need=need)
    msg = str(exc.value)
    assert f"{secret} (from code)" in msg
    assert str(project / _TOML) in msg
    assert _CODE_SECRET not in msg


@pytest.mark.parametrize(
    ("key", "url", "need"),
    [
        ("llm_proxy_url", "https://llm.code.test/", "llm"),
        ("base_url", "https://cp.code.test", "control_plane"),
    ],
)
def test_url_replaced_in_code_is_trusted(
    project: Path, monkeypatch: pytest.MonkeyPatch, key: str, url: str, need: str
) -> None:
    _write(
        project / _TOML,
        'base_url = "https://cp.example.test"\nllm_proxy_url = "https://llm.example.test/"\n',
    )
    _env_control_plane_creds(monkeypatch)
    _env_llm_creds(monkeypatch)
    loaded = DonkeyConfig.from_env()
    with pytest.raises(ConfigError):
        loaded.validated(need=need)

    cfg = dataclasses.replace(loaded, **{key: url})

    assert cfg.source_of(key).kind == "explicit"
    assert cfg.validated(need=need) is cfg


@pytest.mark.parametrize(
    "derive",
    [
        lambda c: dataclasses.replace(c, timeout_s=5.0),
        lambda c: dataclasses.replace(c, llm_proxy_url=c.llm_proxy_url),
        copy.copy,
        copy.deepcopy,
    ],
    ids=["replace-other-field", "replace-same-value", "copy", "deepcopy"],
)
def test_unchanged_values_keep_their_file_label(
    project: Path, monkeypatch: pytest.MonkeyPatch, derive: Callable[[DonkeyConfig], DonkeyConfig]
) -> None:
    _write(project / _TOML, 'llm_proxy_url = "https://llm.example.test/"\n')
    _write(project / _LOCAL_TOML, 'llm_proxy_client_id = "llm-cid"\n')
    _env_llm_creds(monkeypatch)
    monkeypatch.delenv("DONKEY_LLM_PROXY_CLIENT_ID")

    cfg = derive(DonkeyConfig.from_env())

    assert cfg.source_of("llm_proxy_url").kind == "project"
    assert cfg.source_of("llm_proxy_client_id").kind == "local"
    assert cfg.source_of("llm_proxy_client_secret").kind == "env"
    with pytest.raises(ConfigError, match="llm_proxy_client_secret"):
        cfg.validated(need="llm")


def test_with_overrides_still_marks_every_given_key_as_code(project: Path) -> None:
    loaded = _project_endpoints_with_local_credentials(project)

    same = loaded.with_overrides(client_secret=loaded.client_secret)
    assert same.source_of("client_secret").kind == "explicit"
    with pytest.raises(ConfigError, match="client_secret"):
        same.validated(need="control_plane")

    moved = loaded.with_overrides(base_url="https://cp.code.test")
    assert moved.validated(need="control_plane") is moved


def test_value_tracking_keeps_secrets_out_of_repr_and_equality(project: Path) -> None:
    loaded = _project_endpoints_with_local_credentials(project)
    cfg = dataclasses.replace(loaded, client_secret=_CODE_SECRET)

    for text in (repr(cfg), str(cfg), repr(cfg.source_of("client_secret"))):
        assert _CODE_SECRET not in text
        assert "file-secret" not in text
        assert "_sources" not in text
    assert dataclasses.replace(loaded, _sources={}) == loaded


# --- provenance survives asdict() and never copies a value --------------------


def _project_url_env_secret(project: Path, monkeypatch: pytest.MonkeyPatch) -> DonkeyConfig:
    """A project-file proxy URL with env credentials: refused by the binding rule."""
    _write(project / _TOML, 'llm_proxy_url = "https://llm.example.test/"\n')
    _env_llm_creds(monkeypatch)
    cfg = DonkeyConfig.from_env()
    with pytest.raises(ConfigError):
        cfg.validated(need="llm")
    return cfg


def _strings(value: object) -> list[str]:
    """Every string anywhere inside ``value`` (mappings, sequences, dataclasses)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [s for item in value for s in _strings(item)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _strings(dataclasses.asdict(value))
    return []


def test_asdict_round_trip_validates(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/")
    _env_llm_creds(monkeypatch)
    cfg = DonkeyConfig.from_env()

    again = DonkeyConfig(**dataclasses.asdict(cfg))

    assert again.validated(need="llm") is again
    assert again.source_of("llm_proxy_url").kind == "env"


def test_asdict_round_trip_keeps_the_binding_refusal(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _project_url_env_secret(project, monkeypatch)

    again = DonkeyConfig(**dataclasses.asdict(cfg))

    assert again.source_of("llm_proxy_url").kind == "project"
    assert again.source_of("llm_proxy_client_secret").kind == "env"
    with pytest.raises(ConfigError, match="llm_proxy_client_secret"):
        again.validated(need="llm")


def test_asdict_holds_no_second_copy_of_any_value(project: Path) -> None:
    cfg = _project_endpoints_with_local_credentials(project)
    as_dict = dataclasses.asdict(cfg)

    provenance = _strings(as_dict["_sources"])
    for name in ("client_secret", "llm_proxy_client_secret", "llm_proxy_key", "llm_proxy_url"):
        value = getattr(cfg, name)
        assert value
        assert not [s for s in provenance if value in s], name


def test_provenance_recorded_in_another_process_does_not_make_a_url_trusted(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provenance that can't be checked here (it was recorded under another
    process's key) keeps an endpoint's file label and counts every credential as
    set in code, so the config is never more trusted than when it was loaded."""
    as_dict = dataclasses.asdict(_project_url_env_secret(project, monkeypatch))
    monkeypatch.setattr(config_module, "_PROCESS_KEY", os.urandom(32))

    again = DonkeyConfig(**as_dict)

    assert again.source_of("llm_proxy_url").kind == "project"
    assert again.source_of("llm_proxy_client_secret").kind == "explicit"
    with pytest.raises(ConfigError, match="llm_proxy_client_secret"):
        again.validated(need="llm")


def test_unreadable_provenance_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    as_dict = dataclasses.asdict(_project_url_env_secret(project, monkeypatch))
    as_dict["_sources"] = {**as_dict["_sources"], "llm_proxy_url": "project"}

    with pytest.raises(ConfigError, match="llm_proxy_url"):
        DonkeyConfig(**as_dict)


def test_dropping_all_provenance_counts_everything_as_set_in_code(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no provenance at all the config is one built in code: endpoints and
    credentials alike count as code, never a mix of code URLs and file secrets."""
    as_dict = dataclasses.asdict(_project_url_env_secret(project, monkeypatch))
    del as_dict["_sources"]

    again = DonkeyConfig(**as_dict)

    assert {again.source_of(n).kind for n in ("llm_proxy_url", "llm_proxy_client_secret")} == {
        "explicit"
    }


# --- the .local.toml overlay ------------------------------------------------


def test_local_overlay_is_layered_over_the_project_file(project: Path) -> None:
    _write(project / _TOML, 'application_name = "from-project"\nbusiness_group = "bg"\n')
    _write(project / _LOCAL_TOML, 'application_name = "from-local"\n')

    cfg = DonkeyConfig.from_env()
    assert cfg.application_name == "from-local"
    assert cfg.business_group == "bg"


def test_local_overlay_merges_key_by_key_with_per_key_labels(project: Path) -> None:
    _write(project / _TOML, 'application_name = "from-project"\nbusiness_group = "bg"\n')
    _write(project / _LOCAL_TOML, 'application_name = "from-local"\ntimeout_s = 5\n')

    cfg = DonkeyConfig.from_env()
    assert (cfg.application_name, cfg.business_group, cfg.timeout_s) == ("from-local", "bg", 5.0)
    assert cfg.source_of("application_name").kind == "local"
    assert cfg.source_of("business_group").kind == "project"
    assert cfg.source_of("timeout_s").kind == "local"
    assert cfg.source_of("max_retries").kind == "default"


def test_local_overlay_merges_nested_cost_table_recursively(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / _TOML).write_text(
        '[donkey]\n[donkey.cost]\nteam = "t-project"\nproject = "p-project"\n'
        '"enduser.id" = "u-project"\n'
    )
    (project / _LOCAL_TOML).write_text('[donkey.cost]\nproject = "p-local"\n')
    monkeypatch.setenv("DONKEY_COST_ENDUSER_ID", "u-env")

    cfg = DonkeyConfig.from_env()
    assert cfg.cost == CostTags(team="t-project", project="p-local", enduser_id="u-env")
    assert cfg.source_of("cost.team").kind == "project"
    assert cfg.source_of("cost.project").kind == "local"
    assert cfg.source_of("cost.enduser_id").kind == "env"
    assert cfg.source_of("cost.env").kind == "default"


def test_unknown_cost_key_in_the_local_overlay_is_still_an_error(project: Path) -> None:
    (project / _TOML).write_text('[donkey.cost]\nteam = "t"\n')
    (project / _LOCAL_TOML).write_text('[donkey.cost]\nteem = "t"\n')
    with pytest.raises(ConfigError, match="teem"):
        DonkeyConfig.from_env()


def test_local_overlay_linked_to_the_user_file_is_refused(
    project: Path, tmp_path: Path
) -> None:
    """A committed link from the overlay to the user file would label the user
    file's credentials as the local overlay's."""
    user = tmp_path / "xdg" / _TOML
    _write(user, 'client_id = "cid"\nclient_secret = "user-file-value"\norg_id = "org"\n')
    _write(project / _TOML, 'base_url = "https://other.example.test"\n')
    (project / _LOCAL_TOML).symlink_to(os.path.relpath(user, project))

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    msg = str(exc.value)
    assert str(project / _LOCAL_TOML) in msg
    assert str(user.resolve()) in msg
    assert "outside the working directory" in msg
    assert "user-file-value" not in msg


def test_project_file_linked_outside_the_working_directory_is_refused(
    project: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere.toml"
    _write(elsewhere, 'base_url = "https://other.example.test"\n')
    (project / _TOML).symlink_to(elsewhere)

    with pytest.raises(ConfigError, match="outside the working directory") as exc:
        DonkeyConfig.from_env()
    assert str(project / _TOML) in str(exc.value)


def test_link_that_stays_inside_the_working_directory_is_read(project: Path) -> None:
    (project / "config").mkdir()
    _write(project / "config" / "local.toml", 'application_name = "linked"\n')
    (project / _LOCAL_TOML).symlink_to(Path("config") / "local.toml")

    cfg = DonkeyConfig.from_env()
    assert cfg.application_name == "linked"
    assert cfg.source_of("application_name").kind == "local"


def test_env_wins_over_the_local_overlay(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(project / _TOML, 'application_name = "from-project"\n')
    _write(project / _LOCAL_TOML, 'application_name = "from-local"\n')
    monkeypatch.setenv("DONKEY_APP_NAME", "from-env")

    assert DonkeyConfig.from_env().application_name == "from-env"


def test_malformed_local_overlay_is_a_config_error(project: Path) -> None:
    (project / _LOCAL_TOML).write_text("[donkey\n")
    with pytest.raises(ConfigError, match=_LOCAL_TOML):
        DonkeyConfig.from_env()


def test_user_file_is_still_used_when_the_project_has_no_config(
    project: Path, tmp_path: Path
) -> None:
    _write(tmp_path / "xdg" / _TOML, 'application_name = "from-user"\n')
    assert DonkeyConfig.from_env().application_name == "from-user"


# --- secrets in the committed file ------------------------------------------


def test_secret_in_project_file_warns_and_points_to_local_overlay(project: Path) -> None:
    _write(project / _TOML, 'client_secret = "s"\n')
    with pytest.warns(UserWarning, match=r"\.donkey-kit\.local\.toml") as record:
        DonkeyConfig.from_env()
    assert "client_secret" in str(record[0].message)


def test_secret_in_local_overlay_does_not_warn(project: Path) -> None:
    _write(project / _LOCAL_TOML, 'client_secret = "s"\nllm_proxy_key = "k"\n')
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        DonkeyConfig.from_env()
