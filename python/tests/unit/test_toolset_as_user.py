"""``donkey.as_user(...)`` on the MCP ``ToolSet`` bindings (BG §2.4, #1366).

Inside the block, listing and binding send the user's token in the user-token
header (``Authorization: Bearer`` by default, docs/verified-apis.md §13) instead
of the credential's ``Authorization``; the credential's other headers still go.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Mapping
from typing import Any

import pytest

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


async def test_a_binding_keeps_the_user_it_was_made_for(
    listed: list[dict[str, str]], crewai_tools: type[_Adapter]
) -> None:
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
