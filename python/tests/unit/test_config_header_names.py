"""Configurable header names are checked wherever they come from (config resolution).

``correlation_header``, ``call_id_header`` and the four ``cost_*_header`` keys
name request headers the SDK writes on every request. A name that routes or
frames the request, carries a credential, collides with another header the SDK
sets, or isn't a valid HTTP token is refused with a :class:`ConfigError` naming
the key, where it was set and the header, before any client is built.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError

_TOML = ".donkey-kit.toml"

ROUTING_AND_FRAMING = [
    "Host",
    "host",
    "Forwarded",
    "X-Forwarded-Host",
    "x-forwarded-for",
    "X-Forwarded-Proto",
    "X-Real-IP",
    "Content-Length",
    "Transfer-Encoding",
    "Connection",
    "Upgrade",
    "TE",
    "Trailer",
    "Expect",
]
CREDENTIALS = [
    "Authorization",
    "Proxy-Authorization",
    "Cookie",
    "client_id",
    "CLIENT_SECRET",
    "X-Client-Id",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
]
SDK_SET = [
    "X-Anypoint-Client-Application",  # attribution
    "x-cache-skip",  # semantic-cache steering
    "Content-Type",
    "X-Donkey-Request-Id",  # the call-id header's default name
]
OVERRIDES = [
    "X-HTTP-Method-Override",
    "X-HTTP-Method",
    "X-Method-Override",
    "X-Original-URL",
    "X-Original-URI",
    "X-Rewrite-URL",
    "x-stainless-retry-count",  # set by the OpenAI and Anthropic SDKs
]
RESERVED = ROUTING_AND_FRAMING + CREDENTIALS + SDK_SET + OVERRIDES
NOT_A_TOKEN = ["Bad Header", "X:Y", "", "X-Tab\tHere", "X-Ümlaut"]
WITHOUT_X_PREFIX = [
    "Team",
    "Content-Encoding",
    "Via",
    "Origin",
    "Keep-Alive",
    "Proxy-Connection",
    "Range",
    "If-Match",
    "Max-Forwards",
    "anthropic-version",
    "OpenAI-Organization",
]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (tmp_path / "xdg").mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return project_dir


@pytest.mark.parametrize("name", RESERVED)
def test_reserved_name_from_the_project_file_is_refused(project: Path, name: str) -> None:
    (project / _TOML).write_text(f'[donkey]\ncost_team_header = "{name}"\n')

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    msg = str(exc.value)
    assert "cost_team_header" in msg
    assert str(project / _TOML) in msg
    assert repr(name) in msg


@pytest.mark.parametrize("name", RESERVED + NOT_A_TOKEN)
def test_reserved_or_invalid_name_from_env_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("DONKEY_CORRELATION_HEADER", name)
    if name == "":
        pytest.skip("an empty env value leaves the name unset")

    with pytest.raises(ConfigError) as exc:
        DonkeyConfig.from_env()
    msg = str(exc.value)
    assert "correlation_header" in msg
    assert "DONKEY_CORRELATION_HEADER" in msg
    assert repr(name) in msg


@pytest.mark.parametrize("name", WITHOUT_X_PREFIX)
def test_name_without_the_x_prefix_is_refused_from_any_source(
    project: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (project / _TOML).write_text(f'[donkey]\ncost_team_header = "{name}"\n')
    with pytest.raises(ConfigError, match="must start with X-") as exc:
        DonkeyConfig.from_env()
    assert repr(name) in str(exc.value)

    (project / _TOML).unlink()
    monkeypatch.setenv("DONKEY_CALL_ID_HEADER", name)
    with pytest.raises(ConfigError, match="call_id_header"):
        DonkeyConfig.from_env()

    with pytest.raises(ConfigError, match="correlation_header"):
        DonkeyConfig(correlation_header=name)


@pytest.mark.parametrize(
    "key",
    [
        "correlation_header",
        "call_id_header",
        "cost_team_header",
        "cost_project_header",
        "cost_env_header",
        "cost_enduser_header",
    ],
)
def test_every_header_key_is_checked_in_code_too(key: str) -> None:
    with pytest.raises(ConfigError, match=key):
        DonkeyConfig(**{key: "Host"})


def test_two_keys_naming_the_same_header_are_refused() -> None:
    with pytest.raises(ConfigError, match="call_id_header"):
        DonkeyConfig(correlation_header="X-Trace", call_id_header="x-trace")


def test_valid_custom_names_are_accepted(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / _TOML).write_text(
        "[donkey]\n"
        'cost_team_header = "X-Team-Id"\n'
        'cost_project_header = "x-project_id"\n'
        'call_id_header = "X-Call.Id"\n'
    )
    monkeypatch.setenv("DONKEY_CORRELATION_HEADER", "X-Request-Trace")

    cfg = DonkeyConfig.from_env()

    assert cfg.cost_team_header == "X-Team-Id"
    assert cfg.correlation_header == "X-Request-Trace"


async def test_valid_custom_names_reach_the_request(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / _TOML).write_text(
        '[donkey]\nsend_cost_headers = true\ncost_team_header = "X-Team-Id"\n'
        '[donkey.cost]\nteam = "payments"\n'
    )
    monkeypatch.setenv("DONKEY_CORRELATION_HEADER", "X-Request-Trace")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.internal/proxy/")
    seen: list[httpx.Request] = []

    async def _record(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _record)

    # A model request: cost headers go on the data plane only (#833).
    async with Donkey(DonkeyConfig.from_env()) as donkey:
        await donkey._http.post("https://gw.example.internal/proxy/chat/completions", json={})

    (request,) = seen
    assert request.headers["X-Team-Id"] == "payments"
    assert "X-Request-Trace" in request.headers


async def test_refused_names_send_nothing(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reported case: the URL and secret in env, header names in the file."""
    (project / _TOML).write_text(
        "[donkey]\n"
        "send_cost_headers = true\n"
        'cost_team_header = "Host"\n'
        'cost_project_header = "X-Forwarded-Host"\n'
        "[donkey.cost]\n"
        'team = "other-app.example"\n'
        'project = "other-app.example"\n'
    )
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/proxy/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "placeholder-value")
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cid")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "placeholder-value")
    seen: list[httpx.Request] = []

    async def _record(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})

    def _record_sync(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _record)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _record_sync)

    with pytest.raises(ConfigError, match="cost_team_header"):
        Donkey.from_env()
    assert seen == []


async def test_method_and_url_override_names_send_nothing(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / _TOML).write_text(
        "[donkey]\n"
        "send_cost_headers = true\n"
        'cost_team_header = "X-HTTP-Method-Override"\n'
        'cost_project_header = "X-Original-URL"\n'
        "[donkey.cost]\n"
        'team = "DELETE"\n'
        'project = "/admin/other"\n'
    )
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.example.test/proxy/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "placeholder-value")
    seen: list[httpx.Request] = []

    async def _record(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _record)

    with pytest.raises(ConfigError, match="cost_team_header"):
        Donkey.from_env()
    assert seen == []
