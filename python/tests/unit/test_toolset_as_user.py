"""``donkey.as_user(...)`` on the MCP ``ToolSet`` bindings (BG §2.4, #1366).

Inside the block, listing and binding send the user's token in the user-token
header (``Authorization: Bearer`` by default, docs/verified-apis.md §13) instead
of the credential's ``Authorization``; the credential's other headers still go.
A per-tool binding refuses a call under any other user than the one it was
bound for (#1418); a whole-server binding keeps the bind's headers.
"""

from __future__ import annotations

import contextlib
import sys
import types
from collections.abc import Mapping
from typing import Any

import pytest

from donkey_kit import ApprovalRequired, Approvals
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError, ConfigError
from donkey_kit.core.identity import UserScope
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import BearerAuth, ClientIdAuth, McpAuth
from donkey_kit.tools.discovery import Discovery
from donkey_kit.tools.session import ToolSet

_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])
_USER_TOKEN = "user-access-token"
_PROXY = "https://llm.example.test/proxy/"


def _cfg(**kw: object) -> DonkeyConfig:
    base: dict[str, object] = {
        "llm_proxy_url": _PROXY,
        "llm_proxy_client_id": "cid",
        "llm_proxy_client_secret": "secret",
    }
    return DonkeyConfig(**{**base, **kw})


def _toolset(auth: McpAuth | None = None, *, user_header: str | None = None) -> ToolSet:
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("com.acme", "inv", "1.0.0"),
                endpoint_url="https://gw.example.test/inv/",
                governance=_GOVERNED,
            )
        ],
        _auth=auth,
        _user_header=user_header,
    )


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """The headers of every ``tools/list`` call."""
    sent: list[dict[str, str]] = []

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        sent.append(dict(headers))
        return ({"name": "add"},)

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    return sent


class _Adapter:
    made: list[_Adapter] = []

    def __init__(self, params: Mapping[str, Any], *tool_names: str) -> None:
        self.params = dict(params)
        self.tools = list(tool_names)
        _Adapter.made.append(self)

    def stop(self) -> None:
        pass


@pytest.fixture
def crewai_tools(monkeypatch: pytest.MonkeyPatch) -> type[_Adapter]:
    _Adapter.made = []
    module = types.ModuleType("crewai_tools")
    module.MCPServerAdapter = _Adapter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "crewai_tools", module)
    return _Adapter


async def test_outside_a_block_the_credential_goes_unchanged(
    listed: list[dict[str, str]],
) -> None:
    _, headers = await _toolset(ClientIdAuth("cid", "secret"))._plan("x()", per_tool=True)
    assert listed == [{"client_id": "cid", "client_secret": "secret"}]
    assert headers == {"client_id": "cid", "client_secret": "secret"}


async def test_inside_a_block_listing_and_binding_carry_the_user_token(
    listed: list[dict[str, str]],
) -> None:
    async with UserScope(_cfg(), _USER_TOKEN):
        _, headers = await _toolset(ClientIdAuth("cid", "secret"))._plan("x()", per_tool=True)
    want = {"client_id": "cid", "client_secret": "secret", "Authorization": f"Bearer {_USER_TOKEN}"}
    assert listed == [want]
    assert headers == want


async def test_the_user_token_replaces_the_credentials_authorization(
    listed: list[dict[str, str]],
) -> None:
    async with UserScope(_cfg(), _USER_TOKEN):
        _, headers = await _toolset(BearerAuth(StaticToken("agent-token")))._plan(
            "x()", per_tool=True
        )
    assert headers == {"Authorization": f"Bearer {_USER_TOKEN}"}


async def test_a_custom_user_header_carries_the_raw_token_beside_the_credential(
    listed: list[dict[str, str]],
) -> None:
    cfg = _cfg(llm_proxy_auth="bearer", user_token_header="X-User-Token")
    async with UserScope(cfg, _USER_TOKEN):
        _, headers = await _toolset(
            BearerAuth(StaticToken("agent-token")), user_header="X-User-Token"
        )._plan("x()", per_tool=True)
    assert headers == {"Authorization": "Bearer agent-token", "X-User-Token": _USER_TOKEN}


async def test_the_user_token_is_enough_without_a_configured_credential(
    listed: list[dict[str, str]],
) -> None:
    with pytest.raises(ConfigError, match="No gateway credential"):
        await _toolset().describe()
    async with UserScope(_cfg(), _USER_TOKEN):
        _, headers = await _toolset()._plan("x()", per_tool=True)
    assert headers == {"Authorization": f"Bearer {_USER_TOKEN}"}


async def test_a_refused_user_token_is_not_retried_with_the_service_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, str]] = []

    async def refuse(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        sent.append(dict(headers))
        raise AuthError("The gateway refused the credential (HTTP 401).")

    class _Counting(ClientIdAuth):
        dropped = 0

        async def invalidate(self) -> None:
            _Counting.dropped += 1

    monkeypatch.setattr(session_module, "list_tools", refuse)
    async with UserScope(_cfg(), _USER_TOKEN):
        with pytest.raises(AuthError, match=r"as_user\(\) token") as caught:
            await _toolset(_Counting("cid", "secret")).describe()
    assert len(sent) == 1
    assert _Counting.dropped == 0
    assert "audience" in caught.value.remediation


class _LcTool:
    """The shape of a ``langchain_mcp_adapters`` tool: ``name`` and ``coroutine``."""

    def __init__(self, name: str, calls: list[dict[str, Any]]) -> None:
        self.name = name

        async def call_tool(**arguments: Any) -> str:
            calls.append(arguments)
            return "sent"

        self.coroutine = call_tool


@pytest.fixture
def mcp_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every tool call a fake ``MultiServerMCPClient`` sent, for ``ToolSet.langgraph()``."""
    calls: list[dict[str, Any]] = []

    class _Client:
        def __init__(self, connections: Mapping[str, Any]) -> None:
            self.connections = connections

        async def get_tools(self, *, server_name: str) -> list[_LcTool]:
            return [_LcTool("add", calls)]

    package = types.ModuleType("langchain_mcp_adapters")
    client = types.ModuleType("langchain_mcp_adapters.client")
    client.MultiServerMCPClient = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters", package)
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", client)
    return calls


_ALICE, _BOB = "alice-token", "bob-token"


def _scope(token: str | None) -> Any:
    """``as_user(token)``, or no block for ``None`` (the service)."""
    return UserScope(_cfg(), token) if token is not None else contextlib.nullcontext()


async def _bind(ts: ToolSet, token: str | None) -> Any:
    async with _scope(token):
        (tool,) = await ts.langgraph()
    return tool


async def _call(tool: Any, token: str | None) -> Any:
    async with _scope(token):
        return await tool.coroutine(a=1)


@pytest.mark.parametrize("token", [None, _ALICE])
async def test_a_per_tool_binding_runs_for_the_user_it_was_bound_for(
    listed: list[dict[str, str]], mcp_calls: list[dict[str, Any]], token: str | None
) -> None:
    tool = await _bind(_toolset(ClientIdAuth("cid", "secret")), token)
    # A fresh block with the same token is the same user.
    assert await _call(tool, token) == "sent"
    async with _scope(token):
        (same_block,) = await _toolset(ClientIdAuth("cid", "secret")).langgraph()
        assert await same_block.coroutine(a=2) == "sent"
    assert mcp_calls == [{"a": 1}, {"a": 2}]


@pytest.mark.parametrize(
    ("bound", "called", "made", "runs"),
    [
        (_ALICE, _BOB, "inside donkey.as_user()", "with a different token"),
        (None, _BOB, "as the service", "with a different token"),
        (_ALICE, None, "inside donkey.as_user()", "outside any donkey.as_user() block"),
    ],
)
async def test_a_per_tool_binding_refuses_a_call_under_another_user(
    listed: list[dict[str, str]],
    mcp_calls: list[dict[str, Any]],
    bound: str | None,
    called: str | None,
    made: str,
    runs: str,
) -> None:
    tool = await _bind(_toolset(ClientIdAuth("cid", "secret")), bound)
    with pytest.raises(AuthError) as caught:
        await _call(tool, called)
    assert mcp_calls == []
    message = str(caught.value)
    assert "'add' on com.acme/inv/1.0.0" in message
    assert made in message and runs in message
    assert "Bind the ToolSet" in caught.value.remediation
    for token in (_ALICE, _BOB):
        assert token not in message and token not in caught.value.remediation


async def test_an_approved_tool_runs_as_its_requester_wherever_it_is_resolved(
    listed: list[dict[str, str]], mcp_calls: list[dict[str, Any]]
) -> None:
    ts = _toolset(ClientIdAuth("cid", "secret")).require_approval(tools=["add"])
    tool = await _bind(ts, _ALICE)
    with pytest.raises(ApprovalRequired) as alice:
        await _call(tool, _ALICE)
    with pytest.raises(ApprovalRequired) as bob:
        await _call(tool, _BOB)
    # Resolved outside any block: each runs as its requester (#1416), and Bob
    # is not the user the binding sends.
    assert await Approvals().resolve(alice.value.approval.id, approved_by="m.keller") == "sent"
    with pytest.raises(AuthError, match="with a different token"):
        await Approvals().resolve(bob.value.approval.id, approved_by="m.keller")
    assert mcp_calls == [{"a": 1}]


async def test_a_whole_server_binding_sends_the_user_it_was_bound_for(
    listed: list[dict[str, str]], crewai_tools: type[_Adapter]
) -> None:
    # The framework owns the call, so the SDK cannot refuse it (#1418): the
    # documented limit is that the headers stay those of the bind.
    ts = _toolset(ClientIdAuth("cid", "secret"))
    async with UserScope(_cfg(), _USER_TOKEN), ts.crewai():
        pass
    async with ts.crewai():
        pass
    inside, outside = (a.params["headers"] for a in crewai_tools.made)
    assert inside["Authorization"] == f"Bearer {_USER_TOKEN}"
    assert "Authorization" not in outside


class _Registry:
    async def explain(self, *_: Any, **__: Any) -> GovernanceReport:
        return _GOVERNED

    async def resolve_mcp(self, ref: AssetRef) -> McpServerHandle:
        return McpServerHandle(ref=ref, endpoint_url="https://gw.example.test/inv/")


async def test_discovery_passes_the_configured_user_header(
    listed: list[dict[str, str]],
) -> None:
    cfg = _cfg(llm_proxy_auth="bearer", user_token_header="X-User-Token")
    discovery = Discovery(_Registry(), cfg, auth=BearerAuth(StaticToken("agent-token")))  # type: ignore[arg-type]
    ts = await discovery.resolve("com.acme/inv/1.0.0", auth=None)
    async with UserScope(cfg, _USER_TOKEN):
        await ts.describe()
    assert listed == [{"Authorization": "Bearer agent-token", "X-User-Token": _USER_TOKEN}]
