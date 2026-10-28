"""``donkey.tools.discover()``: Exchange MCP assets to a ``ToolSet`` (BG §2.7, #221).

Discovery composes ``search()``, ``explain()`` and ``resolve_mcp()`` and opens no
MCP connection; the registry here is a fake that records those reads. The
``ToolSet`` lists tools lazily, after the governance guard: ``list_tools`` is
replaced so no server is needed (``scripts/mcp_binding/bind_toolset.py`` runs the
real bindings against a local server).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.errors import AuthError, ConfigError, RegistryError, ToolInvocationError
from donkey_kit.core.step_up import GatewayApprovalPending
from donkey_kit.registry.criteria import Check, GovernanceCriteria, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import discovery as discovery_module
from donkey_kit.tools import mcp as mcp_module
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import BearerAuth, ClientIdAuth, gateway_auth
from donkey_kit.tools.discovery import DISCOVER_LIMIT, Discovery
from donkey_kit.tools.session import ToolSet, UngovernedMcpServer

_CFG = DonkeyConfig(telemetry=False)
_AUTH = ClientIdAuth("cid", "secret")
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])
_UNGOVERNED = GovernanceReport(
    governed=False, checks=[Check("client_id_enforcement", False, "no policy")]
)


def _ref(asset_id: str) -> AssetRef:
    return AssetRef("com.acme", asset_id, "1.0.0")


class _Registry:
    """``search``/``explain``/``resolve_mcp`` over a fixed catalog, recording calls."""

    def __init__(
        self,
        catalog: Mapping[str, GovernanceReport],
        *,
        unresolved: tuple[str, ...] = (),
    ) -> None:
        self.catalog = dict(catalog)
        self.unresolved = unresolved
        self.searches: list[dict[str, Any]] = []
        self.explained: list[str] = []

    async def search(self, **kw: Any) -> list[AssetRef]:
        self.searches.append(kw)
        return [_ref(a) for a in self.catalog]

    async def explain(self, ref: AssetRef, *, criteria: GovernanceCriteria) -> GovernanceReport:
        self.explained.append(ref.asset_id)
        return self.catalog[ref.asset_id]

    async def resolve_mcp(self, ref: AssetRef) -> McpServerHandle:
        if ref.asset_id in self.unresolved:
            raise RegistryError(f"{ref.asset_id} has no deployed MCP instance")
        return McpServerHandle(ref=ref, endpoint_url=f"https://gw.example.test/{ref.asset_id}")


def _discovery(registry: _Registry, cfg: DonkeyConfig = _CFG) -> Discovery:
    return Discovery(registry, cfg, auth=_AUTH)  # type: ignore[arg-type]


async def _discover(d: Discovery, **kw: Any) -> ToolSet:
    args: dict[str, Any] = {"domain": None, "tags": None, "governed": None, "auth": None}
    return await d.discover(**{**args, **kw})


# ---- discovery -----------------------------------------------------------------


async def test_discover_reads_mcp_assets_and_opens_no_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_listing(*_: Any, **__: Any) -> Any:
        raise AssertionError("discover() must not connect")

    monkeypatch.setattr(session_module, "list_tools", no_listing)
    registry = _Registry({"inventory": _GOVERNED})
    ts = await _discover(_discovery(registry), tags=["retail"])
    assert registry.searches == [
        {"asset_types": ["mcp"], "tags": ["retail"], "domain": None, "limit": DISCOVER_LIMIT}
    ]
    (server,) = ts.servers
    assert server.endpoint_url == "https://gw.example.test/inventory"
    assert server.governance == _GOVERNED
    assert server.tool_descriptors is None


async def test_unfiltered_discover_lists_ungoverned_and_says_so_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    d = _discovery(_Registry({"inventory": _GOVERNED, "shadow": _UNGOVERNED}))
    with caplog.at_level(logging.INFO, logger=discovery_module.__name__):
        ts = await _discover(d)
        await _discover(d, tags=["x"])
    assert [s.ref.asset_id for s in ts.servers] == ["inventory", "shadow"]
    assert sum("is unfiltered" in r.getMessage() for r in caplog.records) == 1
    with pytest.raises(UngovernedMcpServer) as info:
        await ts.describe()
    assert info.value.servers == ("com.acme/shadow/1.0.0",)


async def test_governed_discover_leaves_out_ungoverned_with_reasons(
    caplog: pytest.LogCaptureFixture,
) -> None:
    d = _discovery(_Registry({"inventory": _GOVERNED, "shadow": _UNGOVERNED}))
    with caplog.at_level(logging.INFO, logger=discovery_module.__name__):
        ts = await _discover(d, governed=True)
    assert [s.ref.asset_id for s in ts.servers] == ["inventory"]
    (line,) = [r.getMessage() for r in caplog.records if "left out" in r.getMessage()]
    assert "com.acme/shadow/1.0.0" in line
    assert "client_id_enforcement: no policy" in line


@pytest.mark.parametrize(
    ("catalog", "expected"),
    [({}, "no MCP asset in Exchange matched"), ({"shadow": _UNGOVERNED}, "none governed")],
)
async def test_empty_result_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture, catalog: dict[str, GovernanceReport], expected: str
) -> None:
    with caplog.at_level(logging.INFO, logger=discovery_module.__name__):
        ts = await _discover(_discovery(_Registry(catalog)), governed=True)
    assert ts.servers == ()
    assert any(expected in r.getMessage() for r in caplog.records)


async def test_an_unresolved_server_is_listed_with_a_failed_check() -> None:
    d = _discovery(_Registry({"inventory": _GOVERNED}, unresolved=("inventory",)))
    (server,) = (await _discover(d)).servers
    assert server.endpoint_url == ""
    assert server.governance is not None and not server.governance.governed
    assert server.governance.reasons_failed() == [
        "resolve_mcp: inventory has no deployed MCP instance"
    ]
    assert (await _discover(d, governed=True)).servers == ()


async def test_discover_is_cached_until_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _Registry({"inventory": _GOVERNED})
    d = _discovery(registry)
    first = await _discover(d)
    again = await _discover(d)
    assert len(registry.searches) == 1
    assert again.discovered_at == first.discovered_at is not None
    assert not first.is_stale
    await _discover(d, tags=["other"])
    assert len(registry.searches) == 2
    d.refresh()
    await _discover(d)
    assert len(registry.searches) == 3
    monkeypatch.setenv("DONKEY_NO_CACHE", "1")
    await _discover(d)
    await _discover(d)
    assert len(registry.searches) == 5


async def test_a_set_is_stale_after_the_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    ts = await _discover(_discovery(_Registry({"inventory": _GOVERNED})))
    now = session_module.time.monotonic()
    monkeypatch.setattr(session_module.time, "monotonic", lambda: now + _CFG.registry_cache_ttl_s)
    assert ts.is_stale
    assert ts.filter(allow=["*"]).is_stale
    assert not ToolSet([]).is_stale
    assert ToolSet([]).discovered_at is None


async def test_discover_auth_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Mapping[str, str]] = []

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        seen.append(headers)
        return ({"name": "add"},)

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    d = _discovery(_Registry({"inventory": _GOVERNED}))
    await (await _discover(d)).describe()
    await (await _discover(d, auth=BearerAuth(StaticToken("tok")))).describe()
    assert seen == [
        {"client_id": "cid", "client_secret": "secret"},
        {"Authorization": "Bearer tok"},
    ]


async def test_discover_locked_is_blocked() -> None:
    async with Donkey(_CFG, auth=StaticToken("unused")) as donkey:
        with pytest.raises(NotImplementedError, match=r"^blocked on verification:"):
            await donkey.tools.discover(locked=True)


# ---- describe() and the bind plan ----------------------------------------------


def _handle(asset_id: str, governance: GovernanceReport = _GOVERNED) -> McpServerHandle:
    return McpServerHandle(
        ref=_ref(asset_id),
        endpoint_url=f"https://gw.example.test/{asset_id}",
        governance=governance,
    )


class _Lister:
    """A stand-in for ``tools.mcp.list_tools``: each server lists ``add`` and ``lookup``."""

    def __init__(self, *, refuse: int = 0) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.refuse = refuse

    async def __call__(self, url: str, headers: Mapping[str, str], *, server: str) -> Any:
        self.calls.append((url, dict(headers)))
        if self.refuse:
            self.refuse -= 1
            raise AuthError("HTTP 401")
        return ({"name": "add", "inputSchema": {"type": "object"}}, {"name": "lookup"})


class _CountingAuth(ClientIdAuth):
    def __init__(self) -> None:
        super().__init__("cid", "secret")
        self.invalidated = 0

    async def invalidate(self) -> None:
        self.invalidated += 1


@pytest.fixture
def lister(monkeypatch: pytest.MonkeyPatch) -> _Lister:
    fake = _Lister()
    monkeypatch.setattr(session_module, "list_tools", fake)
    return fake


async def test_describe_refuses_an_ungoverned_set_before_listing(lister: _Lister) -> None:
    ts = ToolSet([_handle("inventory"), _handle("shadow", _UNGOVERNED)], _auth=_AUTH)
    with pytest.raises(UngovernedMcpServer):
        await ts.describe()
    assert lister.calls == []


async def test_describe_lists_once_and_feeds_the_name_map(lister: _Lister) -> None:
    ts = ToolSet([_handle("inv-a"), _handle("inv-b")], _auth=_AUTH)
    described = await ts.describe()
    assert len(lister.calls) == 2
    assert described.name_map == {
        "inv-a__add": "add",
        "inv-a__lookup": "lookup",
        "inv-b__add": "add",
        "inv-b__lookup": "lookup",
    }
    assert await described.describe() is described
    assert len(lister.calls) == 2


async def test_describe_without_a_credential_is_a_config_error(lister: _Lister) -> None:
    with pytest.raises(ConfigError, match="No gateway credential") as info:
        await ToolSet([_handle("inventory")]).describe()
    assert "DONKEY_LLM_PROXY_CLIENT_ID" in (info.value.remediation or "")
    assert lister.calls == []


async def test_a_refused_credential_is_invalidated_and_retried_once(lister: _Lister) -> None:
    auth = _CountingAuth()
    lister.refuse = 1
    await ToolSet([_handle("inventory")], _auth=auth).describe()
    assert auth.invalidated == 1
    assert len(lister.calls) == 2
    lister.refuse = 2
    with pytest.raises(AuthError):
        await ToolSet([_handle("inventory")], _auth=auth).describe()
    assert auth.invalidated == 2


async def test_a_ciba_pending_answer_keeps_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gateway accepted the credential and is waiting for the user's approval (#1320):
    # no invalidation, no immediate second listing.
    calls: list[str] = []

    async def pending(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        calls.append(url)
        raise GatewayApprovalPending("waiting", retry_after=5.0)

    monkeypatch.setattr(session_module, "list_tools", pending)
    auth = _CountingAuth()
    with pytest.raises(GatewayApprovalPending):
        await ToolSet([_handle("inventory")], _auth=auth).describe()
    assert auth.invalidated == 0
    assert len(calls) == 1


async def test_filter_servers_narrows_before_listing(lister: _Lister) -> None:
    both = ToolSet([_handle("inv-a"), _handle("inv-b")], _auth=_AUTH)
    described = await both.filter(servers=["inv-b"]).describe()
    assert [url for url, _ in lister.calls] == ["https://gw.example.test/inv-b"]
    assert described.name_map == {"add": "add", "lookup": "lookup"}
    by_coordinates = both.filter(servers=["com.acme/inv-a/*"])
    assert [s.ref.asset_id for s in by_coordinates.servers] == ["inv-a"]


def test_filter_servers_refuses_a_typo_and_a_string() -> None:
    ts = ToolSet([_handle("inventory")], _auth=_AUTH)
    with pytest.raises(ConfigError, match="names no server in the set: invntory"):
        ts.filter(servers=["invntory"])
    with pytest.raises(ValueError, match="servers must be a list"):
        ts.filter(servers="inventory")  # type: ignore[arg-type]


@pytest.mark.parametrize(("status", "error"), [(401, AuthError), (400, ToolInvocationError)])
async def test_list_tools_reads_the_status_the_session_recorded(
    monkeypatch: pytest.MonkeyPatch, status: int, error: type[Exception]
) -> None:
    # mcp 2.x raises an MCPError without the HTTP response; the session's hook
    # recorded the status the gateway answered with.
    pytest.importorskip("mcp")  # list_tools imports it before opening the session

    @asynccontextmanager
    async def refused(
        url: str, headers: Mapping[str, str], *, errors: list[Any]
    ) -> AsyncIterator[Any]:
        errors.append(httpx.Response(status, request=httpx.Request("POST", url)))
        raise RuntimeError("Server returned an error response")
        yield  # pragma: no cover

    monkeypatch.setattr(mcp_module, "open_session", refused)
    with pytest.raises(error, match=f"HTTP {status}"):
        await mcp_module.list_tools("https://gw.example.test/x", {}, server="x")


async def test_a_filter_typo_surfaces_at_describe(lister: _Lister) -> None:
    ts = ToolSet([_handle("inventory")], _auth=_AUTH).filter(allow=["ad"])
    with pytest.raises(ConfigError):
        await ts.describe()


async def test_a_whole_server_plan_refuses_a_rename_and_a_gate(lister: _Lister) -> None:
    both = ToolSet([_handle("inv-a"), _handle("inv-b")], _auth=_AUTH)
    with pytest.raises(ConfigError, match="cannot rename"):
        await both._plan("adk()", per_tool=False)
    plan, headers = await both.filter(deny=["inv-b__*"])._plan("adk()", per_tool=False)
    assert [(s.ref.asset_id, sorted(t)) for s, t in plan] == [("inv-a", ["add", "lookup"])]
    assert headers == {"client_id": "cid", "client_secret": "secret"}
    gated = ToolSet([_handle("inv-a")], _auth=_AUTH).require_approval(tools=["add"])
    with pytest.raises(NotImplementedError, match=r"^blocked on verification:"):
        await gated._plan("adk()", per_tool=False)


async def test_a_per_tool_plan_renames_and_gates(lister: _Lister) -> None:
    both = ToolSet([_handle("inv-a"), _handle("inv-b")], _auth=_AUTH).require_approval(
        tools=["inv-a__add"]
    )
    plan, _ = await both._plan("langgraph()", per_tool=True)
    exposed = {t.exposed: t.rule is not None for _, tools in plan for t in tools.values()}
    assert exposed == {
        "inv-a__add": True,
        "inv-a__lookup": False,
        "inv-b__add": False,
        "inv-b__lookup": False,
    }


async def test_a_plan_that_binds_nothing_is_logged(
    lister: _Lister, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=session_module.__name__):
        plan, _ = await ToolSet([], _auth=_AUTH)._plan("adk()", per_tool=False)
    assert plan == []
    assert any("holds no MCP server" in r.getMessage() for r in caplog.records)


# ---- gateway_auth --------------------------------------------------------------


async def test_gateway_auth_client_id_mode_sends_only_the_pair() -> None:
    cfg = DonkeyConfig(
        telemetry=False,
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
        llm_proxy_key="provider-key",
    )
    auth = gateway_auth(cfg, None)
    assert auth is not None
    assert await auth.headers() == {"client_id": "cid", "client_secret": "secret"}


async def test_gateway_auth_token_mode_sends_a_bearer_token() -> None:
    cfg = DonkeyConfig(telemetry=False, llm_proxy_auth="jwt")
    auth = gateway_auth(cfg, StaticToken("tok"))
    assert auth is not None
    assert await auth.headers() == {"Authorization": "Bearer tok"}
    assert gateway_auth(cfg, None) is None


def test_gateway_auth_is_none_without_a_credential() -> None:
    assert gateway_auth(DonkeyConfig(telemetry=False, llm_proxy_client_id="cid"), None) is None
