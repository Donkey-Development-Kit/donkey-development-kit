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

import asyncio
import importlib.util
import sys
import threading
import types
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.core.errors import PIIDetected
from donkey_kit.core.refusals import translate
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


@pytest.mark.parametrize("mode", ["large", "infinite", "slow", "offorigin", "compressed"])
async def test_binding_error_reads_are_bounded_and_keep_status(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setattr(mcp_module, "_ERROR_BODY_TIMEOUT", 0.02, raising=False)

    class Stream(httpx.AsyncByteStream):
        reads = 0

        async def __aiter__(self) -> AsyncIterator[bytes]:
            while True:
                self.reads += 1
                if self.reads > 10:
                    raise AssertionError("error body read exceeded test safety bound")
                if mode == "slow":
                    await asyncio.sleep(0.01)
                    yield b" "
                else:
                    yield b"x" * (128 * 1024 if mode == "large" else 8192)

    stream = Stream()
    client = mcp_module.client_factory(_URL, {})()
    guard = client.event_hooks["request"][0]
    hook = client.event_hooks["response"][0]
    request = httpx.Request("POST", _ELSEWHERE if mode == "offorigin" else _URL)
    await guard(request)
    response = httpx.Response(
        403,
        stream=stream,
        request=request,
        headers={"content-encoding": "gzip"} if mode == "compressed" else {},
    )
    try:
        try:
            await asyncio.wait_for(hook(response), 0.2)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            assert exc.response.status_code == 403
            assert not isinstance(translate(exc), PIIDetected)
            assert translate(exc) is None
        assert stream.reads == 0 if mode in {"offorigin", "compressed"} else stream.reads <= 9
    finally:
        await response.aclose()
        await client.aclose()


async def test_buffered_error_body_cannot_bypass_binding_cap() -> None:
    client = mcp_module.client_factory(_URL, {})()
    request = httpx.Request("POST", _URL)
    await client.event_hooks["request"][0](request)
    response = httpx.Response(500, content=b"x" * (64 * 1024 + 1), request=request)
    try:
        with pytest.raises(httpx.HTTPStatusError) as caught:
            await client.event_hooks["response"][0](response)
        assert caught.value.response.status_code == 500
        assert translate(caught.value) is None
    finally:
        await client.aclose()


async def test_under_cap_malformed_stream_eof_is_not_a_policy() -> None:
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'{"jsonrpc":"2.0","error":'

    client = mcp_module.client_factory(_URL, {})()
    request = httpx.Request("POST", _URL)
    await client.event_hooks["request"][0](request)
    response = httpx.Response(403, stream=Stream(), request=request)
    try:
        await client.event_hooks["response"][0](response)
        with pytest.raises(httpx.HTTPStatusError) as caught:
            response.raise_for_status()
        assert translate(caught.value) is None
    finally:
        await response.aclose()
        await client.aclose()


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
                path = self.path.replace("\r", "").replace("\n", "")
                self.send_header("location", f"{target}{path}")
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


@pytest.fixture(params=["unit", "harness"])
def redirect_url(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Exercise both redirect helpers without starting a real MCP framework."""
    if request.param == "unit":
        servers = _Servers()
        try:
            yield servers.url
        finally:
            servers.stop()
        return
    path = Path(__file__).resolve().parents[2] / "scripts" / "mcp_binding" / "_harness.py"
    spec = importlib.util.spec_from_file_location("ddk_mcp_binding_harness", path)
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, harness)
    spec.loader.exec_module(harness)
    with harness.cross_origin_redirect(types.SimpleNamespace(url="http://127.0.0.1:9/mcp/")) as srv:
        yield srv.url


@pytest.mark.parametrize(
    "path",
    [
        "/mcp/",
        "/mcp/a%2Fb?next=%2Ftools&value=one%20two",
        "/mcp/?value=%0D%0AX-Injected%3Ayes",
        "/mcp/\rX-Injected:yes",
        "/mcp/\nX-Injected:yes",
        "/mcp/\r\nX-Injected:yes\r\n\r\ninjected-body",
    ],
)
def test_redirect_headers_strip_line_breaks_but_preserve_valid_paths(
    redirect_url: str, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """#1528: enforce header safety even if the HTTP parser supplies raw CR/LF."""
    parse_request = BaseHTTPRequestHandler.parse_request

    def parse_with_path(handler: BaseHTTPRequestHandler) -> bool:
        parsed = parse_request(handler)
        if parsed:
            handler.path = path
        return parsed

    monkeypatch.setattr(BaseHTTPRequestHandler, "parse_request", parse_with_path)
    with httpx.Client(trust_env=False) as client:
        response = client.post(redirect_url)
    assert response.status_code == 307
    assert httpx.URL(response.headers["location"]).path.startswith("/mcp/")
    assert response.headers["location"].endswith(path.replace("\r", "").replace("\n", ""))
    assert "x-injected" not in response.headers
    assert response.content == b""


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


async def _openai_agents(monkeypatch: pytest.MonkeyPatch, transports: list[Any]) -> Any:
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
    await _toolset().openai_agents()
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
    [_langgraph, _llamaindex, _adk, _openai_agents, _crewai, _strands],
    ids=["langgraph", "llamaindex", "adk", "openai_agents", "crewai", "strands"],
)
async def test_the_binding_hands_its_framework_the_guarded_client(
    bind: Callable[..., Any], monkeypatch: pytest.MonkeyPatch, transports: list[Any]
) -> None:
    made.clear()
    await _assert_guarded(await bind(monkeypatch, transports))
