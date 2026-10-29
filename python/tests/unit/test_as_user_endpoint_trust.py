"""The ``donkey.as_user()`` token meets the endpoint-trust rule (ADR 0005, #1439).

The user token is supplied in code, so like any credential from outside the
working directory's ``.donkey-kit.toml`` / ``.donkey-kit.local.toml`` it is not
sent to an endpoint those files name unless ``DONKEY_TRUST_PROJECT_CONFIG`` opts
in. ``as_user()`` checks the LLM proxy URL when the scope is built; a discovered
``ToolSet`` checks the control plane its MCP gateway URLs came from before it
puts the token in a listing or binding header.

Every test runs in a temp working directory with ``XDG_CONFIG_HOME`` pointed at
a temp dir and every ``ANYPOINT_*`` / ``DONKEY_*`` variable cleared, so the
developer's real config is never read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.identity import UserScope, current_user
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.discovery import Discovery

_TOML = ".donkey-kit.toml"
_LOCAL_TOML = ".donkey-kit.local.toml"
_TOKEN = "user-access-token-7d2e"
_LABEL = "the donkey.as_user() token"
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])


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


def _project_proxy(project: Path, filename: str = _TOML) -> None:
    """An untrusted checkout: its own proxy URL and dummy consumer credentials,
    all from the working directory, so the binding rule alone lets them pair."""
    url = 'llm_proxy_url = "https://llm.attacker.test/"\n'
    creds = 'llm_proxy_client_id = "dummy"\nllm_proxy_client_secret = "dummy"\n'
    if filename == _TOML:
        _write(project / _TOML, url)
        _write(project / _LOCAL_TOML, creds)
    else:
        _write(project / _LOCAL_TOML, url + creds)


def _record(donkey: Donkey) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    donkey._http.governed_transport.replace_inner(httpx.MockTransport(handler))
    return seen


# --- the LLM proxy --------------------------------------------------------------


@pytest.mark.parametrize("filename", [_TOML, _LOCAL_TOML])
async def test_as_user_refuses_a_proxy_url_from_the_working_directory(
    project: Path, filename: str
) -> None:
    _project_proxy(project, filename)
    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="llm") is cfg  # the service's own pairing passes

    async with Donkey(cfg) as donkey:
        seen = _record(donkey)
        with pytest.raises(ConfigError) as caught:
            async with donkey.as_user(token=_TOKEN):
                await donkey._http.post("https://llm.attacker.test/chat")  # pragma: no cover
        assert current_user() is None
    assert seen == []
    message = str(caught.value)
    assert _LABEL in message
    assert "llm.attacker.test" in message
    assert str(project / filename) in message
    assert "DONKEY_LLM_PROXY_URL" in message
    assert "DONKEY_TRUST_PROJECT_CONFIG=1" in message
    assert _TOKEN not in message
    # The token is never in a file, so keeping it in the local overlay is no way out.
    assert "keep the credentials" not in message
    assert caught.value.remediation


async def test_trust_opt_in_lets_the_token_reach_a_project_proxy_url(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project_proxy(project)
    monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")

    async with Donkey(DonkeyConfig.from_env()) as donkey:
        seen = _record(donkey)
        async with donkey.as_user(token=_TOKEN):
            await donkey._http.post("https://llm.attacker.test/chat")
    assert [r.headers["Authorization"] for r in seen] == [f"Bearer {_TOKEN}"]


def test_a_proxy_url_from_the_environment_is_trusted(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project_proxy(project)
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.env.test/")

    UserScope(DonkeyConfig.from_env(), _TOKEN)


def test_a_proxy_url_set_in_code_is_trusted(project: Path) -> None:
    _project_proxy(project)
    cfg = DonkeyConfig.from_env().with_overrides(llm_proxy_url="https://llm.code.test/")

    UserScope(cfg, _TOKEN)


def test_no_proxy_url_is_nothing_to_check(project: Path) -> None:
    UserScope(DonkeyConfig.from_env(), _TOKEN)


@pytest.mark.parametrize(
    ("mode", "provider"),
    [
        ("bearer", "the token from the llm_auth provider"),
        ("jwt", "the JWT from the llm_auth provider"),
    ],
)
def test_a_token_mode_names_both_tokens(project: Path, mode: str, provider: str) -> None:
    _write(
        project / _TOML,
        'llm_proxy_url = "https://llm.attacker.test/"\n'
        f'llm_proxy_auth = "{mode}"\n'
        'llm_proxy_wallet_client_id = "wallet"\n'
        'user_token_header = "X-User-Token"\n',
    )

    with pytest.raises(ConfigError) as caught:
        UserScope(DonkeyConfig.from_env(), _TOKEN)
    message = str(caught.value)
    assert f"Not sending {provider}, {_LABEL} to llm.attacker.test" in message


# --- MCP: the gateway URLs come from the control plane --------------------------


class _Registry:
    """What discovery reads: every server governed, its URL from the control plane."""

    async def explain(self, *_: Any, **__: Any) -> GovernanceReport:
        return _GOVERNED

    async def resolve_mcp(self, ref: AssetRef) -> McpServerHandle:
        return McpServerHandle(ref=ref, endpoint_url="https://gw.attacker.test/inv/")


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """The headers of every ``tools/list`` call."""
    sent: list[dict[str, str]] = []

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        sent.append(dict(headers))
        return ({"name": "add"},)

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    return sent


def _project_control_plane(project: Path, monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    """``base_url`` and its credentials in the working directory; the proxy URL in env."""
    _write(project / _TOML, f'base_url = "{host}"\norg_id = "org"\n')
    _write(project / _LOCAL_TOML, 'client_id = "cid"\nclient_secret = "secret"\n')
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://llm.env.test/")


async def _discovered(cfg: DonkeyConfig) -> Any:
    discovery = Discovery(_Registry(), cfg, auth=ClientIdAuth("cid", "secret"))  # type: ignore[arg-type]
    return await discovery.resolve("com.acme/inv/1.0.0", auth=None)


async def test_a_discovered_set_refuses_the_token_for_a_project_control_plane(
    project: Path, monkeypatch: pytest.MonkeyPatch, listed: list[dict[str, str]]
) -> None:
    _project_control_plane(project, monkeypatch, "https://cp.attacker.test")
    cfg = DonkeyConfig.from_env()
    ts = await _discovered(cfg)

    async with UserScope(cfg, _TOKEN):
        for attempt in (ts.describe(), ts.filter(servers=["inv"])._plan("x()", per_tool=True)):
            with pytest.raises(ConfigError) as caught:
                await attempt
            message = str(caught.value)
            assert f"Not sending {_LABEL} to cp.attacker.test" in message
            assert "base_url" in message and "ANYPOINT_BASE_URL" in message
            assert "DONKEY_TRUST_PROJECT_CONFIG=1" in message
    assert listed == []
    # The service's own binding is unchanged.
    await ts.describe()
    assert listed == [{"client_id": "cid", "client_secret": "secret"}]


@pytest.mark.parametrize("trusted", ["opt-in", "standard host", "env"])
async def test_a_trusted_control_plane_gets_the_token(
    project: Path, monkeypatch: pytest.MonkeyPatch, listed: list[dict[str, str]], trusted: str
) -> None:
    host = "https://cp.attacker.test"
    if trusted == "standard host":
        host = "https://eu1.anypoint.mulesoft.com"
    _project_control_plane(project, monkeypatch, host)
    if trusted == "opt-in":
        monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")
    elif trusted == "env":
        monkeypatch.setenv("ANYPOINT_BASE_URL", "https://cp.env.test")
    cfg = DonkeyConfig.from_env()
    ts = await _discovered(cfg)

    async with UserScope(cfg, _TOKEN):
        await ts.describe()
    assert listed[0]["Authorization"] == f"Bearer {_TOKEN}"
