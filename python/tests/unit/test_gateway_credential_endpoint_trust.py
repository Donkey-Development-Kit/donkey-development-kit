"""The gateway credential meets the endpoint-trust rule (ADR 0005, ADR 0019, #1479).

Discovery reads the MCP and A2A gateway URLs from the control plane at
``base_url``. When ``base_url`` comes from the working directory's
``.donkey-kit.toml`` / ``.donkey-kit.local.toml``, the service's gateway
credential (the ``llm_proxy_client_id``/``_secret`` pair, the ``llm_auth``
token, or a ``discover(auth=...)`` credential) goes to those URLs only if it was
read from the same files, ``base_url`` is a standard Anypoint host, or
``DONKEY_TRUST_PROJECT_CONFIG`` opts in. Otherwise a discovered ``ToolSet``
refuses to list or bind, and ``Discovery.agent_card()`` to read a card, with a
``ConfigError`` raised before anything is sent.

Every test runs in a temp working directory with ``XDG_CONFIG_HOME`` pointed at
a temp dir and every ``ANYPOINT_*`` / ``DONKEY_*`` variable cleared, so the
developer's real config is never read.
"""

from __future__ import annotations

import os
import sys
import types
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit import ApprovalRequired, Approvals
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.identity import UserScope
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AgentHandle, AssetRef, McpServerHandle
from donkey_kit.tools import discovery as discovery_module
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth, McpAuth, gateway_auth
from donkey_kit.tools.discovery import Discovery
from donkey_kit.tools.session import ToolSet

_TOML = ".donkey-kit.toml"
_LOCAL_TOML = ".donkey-kit.local.toml"
_UNTRUSTED = "https://cp.attacker.test"
_GW = "https://gw.attacker.test/inv/"
_PAIR = "llm_proxy_client_id (from env), llm_proxy_client_secret (from env)"
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


def _checkout(project: Path, *, host: str = _UNTRUSTED, local: str = "") -> None:
    """An untrusted checkout: its own ``base_url`` plus dummy control-plane
    credentials, all from the working directory, so the control-plane check alone
    lets them pair. ``local`` adds lines to the ``.local`` overlay."""
    _write(project / _TOML, f'base_url = "{host}"\norg_id = "org"\n')
    _write(project / _LOCAL_TOML, 'client_id = "cid"\nclient_secret = "dummy"\n' + local)


def _env_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The service's real gateway pair, from the environment."""
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "svc-id")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "svc-secret")


class _Registry:
    """What discovery reads: every asset governed, its URL from the control plane."""

    async def search(self, **_: Any) -> list[AssetRef]:
        return [AssetRef.parse("com.acme/inv/1.0.0")]

    async def explain(self, *_: Any, **__: Any) -> GovernanceReport:
        return _GOVERNED

    async def resolve_mcp(self, ref: AssetRef) -> McpServerHandle:
        return McpServerHandle(ref=ref, endpoint_url=_GW)

    async def resolve_agent(self, ref: AssetRef) -> AgentHandle:
        return AgentHandle(ref=ref, endpoint_url=_GW)


class _Provider:
    """An ``llm_auth`` provider for the ``jwt``/``bearer`` modes."""

    async def token(self) -> str:
        return "llm-auth-token"

    async def invalidate(self) -> None:
        return None


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """The headers of every ``tools/list`` call and every card read."""
    seen: list[dict[str, str]] = []

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        seen.append(dict(headers))
        return ({"name": "add"},)

    def card(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.headers))
        return httpx.Response(200, json={"name": "agent"})

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    monkeypatch.setattr(
        discovery_module,
        "_card_client",
        lambda timeout_s: httpx.AsyncClient(transport=httpx.MockTransport(card)),
    )
    return seen


def _discovery(cfg: DonkeyConfig) -> Discovery:
    """Discovery as ``Donkey`` wires it: the default credential from ``gateway_auth``."""
    llm_auth = _Provider() if cfg.llm_proxy_auth != "client-id" else None
    return Discovery(_Registry(), cfg, auth=gateway_auth(cfg, llm_auth))  # type: ignore[arg-type]


async def _refused(*attempts: Any) -> list[str]:
    messages = []
    for attempt in attempts:
        with pytest.raises(ConfigError) as caught:
            await attempt
        assert caught.value.remediation
        messages.append(str(caught.value))
    return messages


# --- refused: the credential comes from outside the project files ---------------


async def test_an_env_gateway_pair_is_not_sent_to_a_project_control_plane(
    project: Path, monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, str]]
) -> None:
    _checkout(project)
    _env_pair(monkeypatch)
    cfg = DonkeyConfig.from_env()
    assert cfg.validated(need="control_plane") is cfg  # the registry reads pass
    discovery = _discovery(cfg)
    ts = await discovery.resolve("com.acme/inv/1.0.0", auth=None)

    messages = await _refused(
        ts.describe(),
        ts.filter(servers=["inv"])._plan("langgraph()", per_tool=True),
        discovery.agent_card("com.acme/agent/1.0.0"),
    )
    assert sent == []
    for message in messages:
        assert f"Not sending {_PAIR} to cp.attacker.test" in message
        assert str(project / _TOML) in message
        assert "ANYPOINT_BASE_URL" in message
        assert "DONKEY_TRUST_PROJECT_CONFIG=1" in message
        # The pair can move into the overlay; that is a way out.
        assert str(project / _LOCAL_TOML) in message
        assert "svc-secret" not in message


async def test_a_pair_from_the_user_file_is_not_sent_either(
    project: Path, tmp_path: Path, sent: list[dict[str, str]]
) -> None:
    _checkout(project)
    _write(
        tmp_path / "xdg" / _TOML,
        'llm_proxy_client_id = "svc-id"\nllm_proxy_client_secret = "svc-secret"\n',
    )
    discovery = _discovery(DonkeyConfig.from_env())
    ts = await discovery.resolve("com.acme/inv/1.0.0", auth=None)

    (message,) = await _refused(ts.describe())
    assert "llm_proxy_client_id (from user file" in message
    assert sent == []


@pytest.mark.parametrize(
    ("mode", "label"),
    [
        ("bearer", "the token from the llm_auth provider"),
        ("jwt", "the JWT from the llm_auth provider"),
    ],
)
async def test_the_llm_auth_token_is_not_sent_to_a_project_control_plane(
    project: Path, sent: list[dict[str, str]], mode: str, label: str
) -> None:
    _checkout(project, local=f'llm_proxy_auth = "{mode}"\n')
    discovery = _discovery(DonkeyConfig.from_env())
    ts = await discovery.resolve("com.acme/inv/1.0.0", auth=None)

    messages = await _refused(ts.describe(), discovery.agent_card("com.acme/agent/1.0.0"))
    assert sent == []
    for message in messages:
        assert f"Not sending {label} to cp.attacker.test" in message
        # The token is never in a file, so the overlay is no way out.
        assert "keep the credentials" not in message


async def test_a_discover_auth_credential_counts_as_set_in_code(
    project: Path, sent: list[dict[str, str]]
) -> None:
    _checkout(project, local='llm_proxy_client_id = "a"\nllm_proxy_client_secret = "b"\n')
    discovery = _discovery(DonkeyConfig.from_env())
    explicit: McpAuth = ClientIdAuth("code-id", "code-secret")
    ts = await discovery.discover(domain=None, tags=None, governed=True, auth=explicit)

    (message,) = await _refused(ts.describe())
    assert "Not sending the discover(auth=...) credential to cp.attacker.test" in message
    assert sent == []


async def test_inside_as_user_both_the_pair_and_the_token_are_named(
    project: Path, monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, str]]
) -> None:
    _checkout(project)
    _env_pair(monkeypatch)
    cfg = DonkeyConfig.from_env()
    ts = await _discovery(cfg).resolve("com.acme/inv/1.0.0", auth=None)

    async with UserScope(cfg, "user-token"):
        (message,) = await _refused(ts.describe())
    assert f"Not sending {_PAIR}, the donkey.as_user() token to cp.attacker.test" in message
    assert sent == []


@pytest.fixture
def connections(monkeypatch: pytest.MonkeyPatch) -> list[Mapping[str, Any]]:
    """What a fake ``MultiServerMCPClient`` is built with, for ``ToolSet.langgraph()``;
    its one tool, ``add``, records its call's arguments under ``"called"``."""
    made: list[Any] = []

    class _Tool:
        name = "add"

        async def coroutine(self, **arguments: Any) -> str:
            made.append({"called": arguments})
            return "sent"

    class _Client:
        def __init__(self, connections: Mapping[str, Any]) -> None:
            made.append(connections)

        async def get_tools(self, *, server_name: str) -> list[Any]:
            return [_Tool()]

    package = types.ModuleType("langchain_mcp_adapters")
    client = types.ModuleType("langchain_mcp_adapters.client")
    client.MultiServerMCPClient = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", package)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", client)
    return made


@pytest.mark.parametrize("trusted", [False, True])
async def test_a_gated_tool_bound_as_a_user_resumes_only_behind_a_checked_endpoint(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    sent: list[dict[str, str]],
    connections: list[Mapping[str, Any]],
    trusted: bool,
) -> None:
    # The approved call re-runs under its requester (#1416) with the headers of the
    # bind, so the bind is where the user token meets the endpoint check.
    _checkout(project)
    _env_pair(monkeypatch)
    if trusted:
        monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")
    cfg = DonkeyConfig.from_env()
    registry = Approvals()
    discovery = Discovery(_Registry(), cfg, auth=gateway_auth(cfg, None), approvals=registry)  # type: ignore[arg-type]
    ts = (await discovery.resolve("com.acme/inv/1.0.0", auth=None)).require_approval(tools=["add"])

    if not trusted:
        async with UserScope(cfg, "alice-token"):
            (message,) = await _refused(ts.langgraph())
        assert "the donkey.as_user() token to cp.attacker.test" in message
        assert (sent, connections, registry.pending()) == ([], [], ())
        return

    async with UserScope(cfg, "alice-token"):
        (tool,) = await ts.langgraph()
        with pytest.raises(ApprovalRequired) as paused:
            await tool.coroutine(a=1)
    assert [a.id for a in registry.pending()] == [paused.value.approval.id]
    assert await registry.resolve(paused.value.approval.id, approved_by="m.keller") == "sent"
    (bound, called) = connections
    assert bound["com.acme/inv/1.0.0"]["headers"]["Authorization"] == "Bearer alice-token"
    assert called == {"called": {"a": 1}}


# --- sent: the rule lets it through ----------------------------------------------


@pytest.mark.parametrize("trusted", ["opt-in", "standard host", "pair in the overlay", "env url"])
async def test_a_trusted_control_plane_gets_the_credential(
    project: Path, monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, str]], trusted: str
) -> None:
    host = "https://eu1.anypoint.mulesoft.com" if trusted == "standard host" else _UNTRUSTED
    if trusted == "pair in the overlay":
        _checkout(project, local='llm_proxy_client_id = "svc-id"\nllm_proxy_client_secret = "s"\n')
    else:
        _checkout(project, host=host)
        _env_pair(monkeypatch)
    if trusted == "opt-in":
        monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")
    elif trusted == "env url":
        monkeypatch.setenv("ANYPOINT_BASE_URL", "https://cp.env.test")
    discovery = _discovery(DonkeyConfig.from_env())
    ts = await discovery.resolve("com.acme/inv/1.0.0", auth=None)

    await ts.describe()
    await discovery.agent_card("com.acme/agent/1.0.0")
    assert [h["client_id"] for h in sent] == ["svc-id", "svc-id"]


async def test_a_hand_built_set_has_its_urls_in_code(
    project: Path, monkeypatch: pytest.MonkeyPatch, sent: list[dict[str, str]]
) -> None:
    _checkout(project)
    _env_pair(monkeypatch)
    cfg = DonkeyConfig.from_env()
    server = McpServerHandle(
        ref=AssetRef.parse("com.acme/inv/1.0.0"), endpoint_url=_GW, governance=_GOVERNED
    )
    ts = ToolSet([server], _auth=gateway_auth(cfg, None))

    await ts.describe()
    assert sent == [{"client_id": "svc-id", "client_secret": "svc-secret"}]
