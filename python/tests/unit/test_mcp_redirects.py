"""A bound MCP server's redirect never takes the credential to another origin (#1415).

Every ``ToolSet`` binding whose framework takes an HTTP client, a client factory
or a transport gets one from ``tools/mcp.py`` (``client_factory``): its clients do
not follow redirects, and a request hook strips the binding's headers on a
request to any origin but the server's. Here ``mcp`` and each framework are
stand-ins in ``sys.modules`` over the real ``httpx``, so these run in every CI
leg. The real frameworks are bound to a redirecting server in
``tests/conformance/test_tool_binding.py``.
"""

from __future__ import annotations

import sys
import threading
import types
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import mcp as mcp_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet

_URL = "https://gw.example.test/inv/"
_ELSEWHERE = "https://elsewhere.example.test/inv/"
_HEADERS = {"client_id": "cid", "client_secret": "secret", "X-User-Token": "user-token"}
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])


def _install(monkeypatch: pytest.MonkeyPatch, modules: dict[str, dict[str, Any]]) -> None:
    for name, attrs in modules.items():
        module = types.ModuleType(name)
        for attr, value in attrs.items():
            setattr(module, attr, value)
        monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def transports() -> list[Any]:
    """The ``http_client`` each fake ``streamable_http_client`` was opened with."""
    return []


@pytest.fixture(autouse=True)
def fake_mcp(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> None:
    @asynccontextmanager
    async def streamable_http_client(url: str, *, http_client: Any) -> AsyncIterator[Any]:
        transports.append(http_client)
        yield ("read", "write", None)

    _install(
        monkeypatch,
        {
            "mcp": {},
            "mcp.client": {},
            "mcp.client.streamable_http": {
                "httpx": httpx,
                "streamable_http_client": streamable_http_client,
            },
        },
    )


# ---- the client --------------------------------------------------------------------


async def _assert_guarded(client: httpx.AsyncClient, *, user_header: bool = False) -> None:
    """``client`` does not follow redirects, keeps the credential on the server's
    origin and strips it, ``Authorization`` too, everywhere else. A custom header
    is stripped only when the client was made for it (``user_header``)."""
    assert client.follow_redirects is False
    (guard,) = client.event_hooks["request"]
    same = httpx.Request("POST", _URL + "?x=1", headers=_HEADERS)
    await guard(same)
    assert {k: same.headers.get(k) for k in _HEADERS} == _HEADERS
    other = httpx.Request("POST", _ELSEWHERE, headers={**_HEADERS, "Authorization": "Bearer t"})
    await guard(other)
    assert [k for k in other.headers if k.lower() in {"client_id", "client_secret"}] == []
    assert ("x-user-token" in other.headers) is not user_header
    assert "authorization" not in other.headers
    await client.aclose()


async def test_a_factory_client_strips_the_credential_off_the_servers_origin() -> None:
    factory = mcp_module.client_factory(_URL, _HEADERS)
    await _assert_guarded(factory(headers=_HEADERS), user_header=True)


def test_a_factory_client_keeps_the_frameworks_timeout_or_takes_mcps() -> None:
    factory = mcp_module.client_factory(_URL, _HEADERS)
    given = factory(timeout=httpx.Timeout(7.0))
    assert given.timeout == httpx.Timeout(7.0)
    assert factory().timeout == httpx.Timeout(30.0, read=300.0)


def test_the_factory_imports_mcp_only_when_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", None)
    factory = mcp_module.client_factory(_URL, _HEADERS)
    with pytest.raises(ImportError, match=r"donkey-kit\[mcp\]"):
        factory()


class _Servers:
    """A server that answers with a ``307`` to a second one, on another port."""

    def __init__(self) -> None:
        self.other: list[dict[str, str]] = []
        servers = self

        class Other(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                servers.other.append({k.lower(): v for k, v in self.headers.items()})
                self.rfile.read(int(self.headers.get("content-length") or 0))
                self.send_response(200)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self._other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
        target = f"http://127.0.0.1:{self._other.server_address[1]}"

        class Redirect(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length") or 0))
                self.send_response(307)
                self.send_header("location", f"{target}{self.path}")
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self._redirect = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        self.url = f"http://127.0.0.1:{self._redirect.server_address[1]}/mcp/"
        for server in (self._redirect, self._other):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        for server in (self._redirect, self._other):
            server.shutdown()
            server.server_close()


@pytest.fixture
def redirecting() -> Iterator[_Servers]:
    servers = _Servers()
    try:
        yield servers
    finally:
        servers.stop()


async def test_a_followed_cross_origin_redirect_arrives_without_the_credential(
    redirecting: _Servers,
) -> None:
    # An mcp before 1.30 (and 2.0 to 2.2) sends with follow_redirects=True.
    async with mcp_module.client_factory(redirecting.url, _HEADERS)(headers=_HEADERS) as client:
        not_followed = await client.post(redirecting.url, json={})
        assert not_followed.status_code == 307
        assert redirecting.other == []
        followed = await client.post(redirecting.url, json={}, follow_redirects=True)
    assert followed.status_code == 200
    (arrived,) = redirecting.other
    assert not {"client_id", "client_secret", "x-user-token"} & set(arrived)


# ---- each binding hands its framework the guarded client --------------------------


def _toolset() -> ToolSet:
    handle = McpServerHandle(
        ref=AssetRef("com.acme", "inv", "1.0.0"),
        endpoint_url=_URL,
        governance=_GOVERNED,
        tool_descriptors=({"name": "add"},),
    )
    return ToolSet([handle], _auth=ClientIdAuth("cid", "secret"))


class _Made:
    """What a fake framework class was constructed with."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        made.append(self)

    async def get_tools(self, **_: Any) -> list[Any]:
        return []

    async def to_tool_list_async(self) -> list[Any]:
        return []


made: list[_Made] = []


def _from_factory(factory: Callable[..., Any]) -> Any:
    return factory(headers={"client_id": "cid", "client_secret": "secret"})


async def _langgraph(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> Any:
    _install(
        monkeypatch,
        {
            "langchain_mcp_adapters": {},
            "langchain_mcp_adapters.client": {"MultiServerMCPClient": _Made},
        },
    )
    await _toolset().langgraph()
    (connection,) = made[0].args[0].values()
    return _from_factory(connection["httpx_client_factory"])


async def _llamaindex(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> Any:
    _install(
        monkeypatch,
        {
            "llama_index": {},
            "llama_index.core": {},
            "llama_index.core.tools": {"FunctionTool": _Made},
            "llama_index.tools": {},
            "llama_index.tools.mcp": {"BasicMCPClient": _Made, "McpToolSpec": _Made},
        },
    )
    await _toolset().llamaindex()
    assert "headers" not in made[0].kwargs
    return made[0].kwargs["http_client"]


async def _adk(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> Any:
    _install(
        monkeypatch,
        {
            "google": {},
            "google.adk": {},
            "google.adk.tools": {},
            "google.adk.tools.mcp_tool": {
                "McpToolset": _Made,
                "StreamableHTTPConnectionParams": _Made,
            },
        },
    )
    await _toolset().adk()
    return _from_factory(made[0].kwargs["httpx_client_factory"])


async def _openai(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> Any:
    _install(
        monkeypatch,
        {
            "agents": {},
            "agents.mcp": {
                "MCPServerStreamableHttp": _Made,
                "create_static_tool_filter": lambda **kw: kw,
            },
        },
    )
    await _toolset().openai()
    return _from_factory(made[0].kwargs["params"]["httpx_client_factory"])


async def _crewai(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> Any:
    class Adapter(_Made):
        tools: list[Any] = []

        def stop(self) -> None:
            pass

    _install(monkeypatch, {"crewai_tools": {"MCPServerAdapter": Adapter}})
    async with _toolset().crewai():
        pass
    return _from_factory(made[0].args[0]["httpx_client_factory"])


async def _strands(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> Any:
    _install(
        monkeypatch,
        {"strands": {}, "strands.tools": {}, "strands.tools.mcp": {"MCPClient": _Made}},
    )
    await _toolset().strands()
    assert set(made[0].kwargs) == {"transport_callable", "tool_filters"}
    async with made[0].kwargs["transport_callable"]() as streams:
        assert streams[:2] == ("read", "write")
    (client,) = transports
    assert client.headers["client_secret"] == "secret"
    return client


@pytest.mark.parametrize(
    "bind",
    [_langgraph, _llamaindex, _adk, _openai, _crewai, _strands],
    ids=["langgraph", "llamaindex", "adk", "openai", "crewai", "strands"],
)
async def test_the_binding_hands_its_framework_the_guarded_client(
    bind: Callable[..., Any], monkeypatch: pytest.MonkeyPatch, transports: list[Any]
) -> None:
    made.clear()
    await _assert_guarded(await bind(monkeypatch, transports))
