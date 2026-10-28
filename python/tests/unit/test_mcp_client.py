"""``tools/mcp.py``'s ``tools/list`` call and ``ToolSet.crewai()`` over fake modules (#221).

The real clients run in ``scripts/mcp_binding/`` (local servers) and
``bind_live.py`` (the gateway). Here ``mcp`` and ``crewai_tools`` are stand-ins in
``sys.modules``, so these run in every CI leg, with or without the packages.
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from donkey_kit.core.errors import AuthError, ToolInvocationError
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import mcp as mcp_module
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet

_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name

    def model_dump(self, **_: Any) -> dict[str, object]:
        return {"name": self.name}


class _Page:
    def __init__(self, names: list[str], cursor: str | None) -> None:
        self.tools = [_Tool(n) for n in names]
        self.nextCursor = cursor


class _Params:
    def __init__(self, cursor: str) -> None:
        self.cursor = cursor


class _Server:
    """What the fake ``mcp`` client talks to: pages of tools, or a refusal."""

    def __init__(self, pages: dict[str | None, _Page], *, status: int | None = None) -> None:
        self.pages = pages
        self.status = status
        self.headers: dict[str, str] = {}
        self.initialized = False


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> _Server:
    """Install a fake ``mcp`` package whose client reaches one ``_Server``."""
    srv = _Server({None: _Page(["add"], "p2"), "p2": _Page(["lookup"], None)})

    @asynccontextmanager
    async def streamable_http_client(url: str, *, http_client: Any) -> AsyncIterator[Any]:
        srv.headers = dict(http_client.headers)
        if srv.status is not None:
            response = httpx.Response(srv.status, request=httpx.Request("POST", url))
            for hook in http_client.event_hooks["response"]:
                await hook(response)
            raise RuntimeError("Server returned an error response")  # as mcp 2.x does
        yield ("read", "write")

    class ClientSession:
        def __init__(self, read: str, write: str) -> None:
            assert (read, write) == ("read", "write")

        async def __aenter__(self) -> ClientSession:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def initialize(self) -> None:
            srv.initialized = True

        async def list_tools(self, params: _Params | None = None) -> _Page:
            return srv.pages[params.cursor if params else None]

    streamable = types.ModuleType("mcp.client.streamable_http")
    streamable.httpx = httpx  # type: ignore[attr-defined]
    streamable.streamable_http_client = streamable_http_client  # type: ignore[attr-defined]
    client = types.ModuleType("mcp.client")
    client.streamable_http = streamable  # type: ignore[attr-defined]
    mcp_types = types.ModuleType("mcp.types")
    mcp_types.PaginatedRequestParams = _Params  # type: ignore[attr-defined]
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = ClientSession  # type: ignore[attr-defined]
    mcp.client = client  # type: ignore[attr-defined]
    mcp.types = mcp_types  # type: ignore[attr-defined]
    for name, module in {
        "mcp": mcp,
        "mcp.client": client,
        "mcp.client.streamable_http": streamable,
        "mcp.types": mcp_types,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return srv


# ---- list_tools ----------------------------------------------------------------


async def test_list_tools_follows_the_cursor_with_the_headers(server: _Server) -> None:
    tools = await mcp_module.list_tools(
        "https://gw.example.test/x/", {"client_id": "c"}, server="x"
    )
    assert tools == ({"name": "add"}, {"name": "lookup"})
    assert server.initialized
    assert server.headers["client_id"] == "c"


@pytest.mark.parametrize(("status", "error"), [(403, AuthError), (502, ToolInvocationError)])
async def test_list_tools_maps_the_recorded_status(
    server: _Server, status: int, error: type[Exception]
) -> None:
    server.status = status
    with pytest.raises(error, match=f"HTTP {status}"):
        await mcp_module.list_tools("https://gw.example.test/x/", {}, server="x")


async def test_without_mcp_listing_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", None)
    with pytest.raises(ImportError, match=r"pip install 'donkey-kit\[mcp\]'"):
        await mcp_module.list_tools("https://gw.example.test/x/", {}, server="x")


def _refused(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://gw.example.test/x/")
    return httpx.HTTPStatusError(
        "refused", request=request, response=httpx.Response(status, request=request)
    )


class _Group(Exception):
    """An ``ExceptionGroup`` look-alike (Python 3.10 has none built in)."""

    def __init__(self, *exceptions: BaseException) -> None:
        super().__init__("task group")
        self.exceptions = exceptions


def test_status_is_read_from_inside_a_task_group_or_a_cause() -> None:
    # mcp 1.x raises the httpx error inside a task group's exception group.
    grouped = _Group(ValueError("other"), _refused(401))
    assert mcp_module._status(grouped) == 401
    try:
        raise RuntimeError("wrapped") from _refused(400)
    except RuntimeError as exc:
        assert mcp_module._status(exc) == 400
    assert mcp_module._status(RuntimeError("no response")) is None


# ---- ToolSet.crewai() ----------------------------------------------------------


class _Adapter:
    """A stand-in for ``crewai_tools.MCPServerAdapter``, recording what it got."""

    made: list[_Adapter] = []
    stopped: list[str] = []

    def __init__(self, params: Mapping[str, Any], *tool_names: str) -> None:
        self.params = dict(params)
        self.names = tool_names
        self.tools = [f"{params['url']}#{n}" for n in tool_names]
        _Adapter.made.append(self)

    def stop(self) -> None:
        _Adapter.stopped.append(self.params["url"])


@pytest.fixture
def crewai_tools(monkeypatch: pytest.MonkeyPatch) -> type[_Adapter]:
    _Adapter.made, _Adapter.stopped = [], []
    module = types.ModuleType("crewai_tools")
    module.MCPServerAdapter = _Adapter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "crewai_tools", module)

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        return ({"name": "add"}, {"name": "lookup"})

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    return _Adapter


def _toolset(*asset_ids: str) -> ToolSet:
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("com.acme", a, "1.0.0"),
                endpoint_url=f"https://gw.example.test/{a}/",
                governance=_GOVERNED,
            )
            for a in asset_ids
        ],
        _auth=ClientIdAuth("cid", "secret"),
    )


async def test_crewai_binds_one_adapter_per_server_and_stops_them(
    crewai_tools: type[_Adapter],
) -> None:
    ts = _toolset("inv-a", "inv-b").filter(deny=["inv-b__*", "inv-a__lookup"])
    async with ts.crewai() as tools:  # inv-b has no tool left, so no adapter
        assert tools == ["https://gw.example.test/inv-a/#add"]
        assert _Adapter.stopped == []
    (adapter,) = crewai_tools.made
    assert adapter.params == {
        "url": "https://gw.example.test/inv-a/",
        "transport": "streamable-http",
        "headers": {"client_id": "cid", "client_secret": "secret"},
    }
    assert adapter.names == ("add",)
    assert _Adapter.stopped == ["https://gw.example.test/inv-a/"]


async def test_crewai_stops_the_adapters_when_the_block_raises(
    crewai_tools: type[_Adapter],
) -> None:
    with pytest.raises(RuntimeError, match="agent failed"):
        async with _toolset("inv-a").crewai():
            raise RuntimeError("agent failed")
    assert _Adapter.stopped == ["https://gw.example.test/inv-a/"]


async def test_without_crewai_tools_binding_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "crewai_tools", None)
    with pytest.raises(ImportError, match=r"crewai-tools\[mcp\]"):
        async with _toolset("inv-a").crewai():
            pass
