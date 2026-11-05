"""Offline native MCP injection contracts (BG sections 1.4 / 1.5, #1391)."""

from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest
from test_tool_binding import BINDINGS, _require

from donkey_kit import typed_refusals
from donkey_kit.core.errors import ConfigError, PIIDetected
from donkey_kit.core.tool_access import ToolNotInProfile
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.simulator.mcp import mcp_baseline, simulate_mcp
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.mcp import open_session
from donkey_kit.tools.session import ToolSet

_URL = "https://gateway.example/mcp/"
pytestmark = [pytest.mark.disable_socket, pytest.mark.timeout(15)]


def _tools(url: str = _URL) -> ToolSet:
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("example", "tools", "1.0.0"),
                endpoint_url=url,
                governance=GovernanceReport(
                    governed=True, checks=[Check("deployed", True, "offline")]
                ),
            )
        ],
        _auth=ClientIdAuth("offline-id", "offline-secret"),
    )


_BASELINE = mcp_baseline(
    _URL,
    tools=[
        {
            "name": "calculate",
            "description": "Offline tool",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ],
    call_tool=lambda name, arguments: {"content": [{"type": "text", "text": "success"}]},
)


def _protocol(request: httpx.Request) -> httpx.Response:
    return _BASELINE.handle_request(request)


@pytest.mark.parametrize("version", ["2024-11-05", "2025-06-18", "unsupported-test-version"])
async def test_baseline_protocol_version_is_accepted_or_rejected_by_native_client(
    version: str,
) -> None:
    pytest.importorskip("mcp")
    baseline = mcp_baseline(
        _URL, tools=[], call_tool=lambda name, arguments: {}, server_protocol_version=version
    )

    async def initialize():
        with simulate_mcp(_tools(), "pii-request", baseline=baseline) as replay:
            async with open_session(_URL, {}, http_factory=replay.client) as session:
                assert (await session.list_tools()).tools == []

    if version == "unsupported-test-version":
        with pytest.raises(Exception) as caught:
            await initialize()
        pending = [caught.value]
        messages = []
        while pending:
            error = pending.pop()
            messages.append(str(error))
            pending.extend(getattr(error, "exceptions", ()))
        assert any("Unsupported protocol version" in message for message in messages)
    else:
        await initialize()


async def test_official_session_correlates_refusal() -> None:
    pytest.importorskip("mcp")
    import mcp.shared.exceptions as exceptions

    with simulate_mcp(
        _tools(), "schema-invalid-args", baseline=httpx.MockTransport(_protocol)
    ) as replay:
        async with open_session(_URL, {}, http_factory=replay.client) as session:
            assert (await session.list_tools()).tools[0].name == "calculate"
            error_type = getattr(exceptions, "McpError", None) or exceptions.MCPError
            with pytest.raises(error_type) as caught:
                await session.call_tool("calculate", {})
            assert caught.value.error.code == -32602
            assert (await session.call_tool("calculate", {})).content[0].text == "success"


@pytest.mark.parametrize("binding", ["langgraph", "llamaindex", "anthropic"])
@pytest.mark.parametrize("capture", ["pii-request", "pii-response-sse"])
async def test_native_binding_pii_and_subsequent_success(binding: str, capture: str) -> None:
    _require(BINDINGS[binding])
    tools = _tools()
    with simulate_mcp(tools, capture, baseline=httpx.MockTransport(_protocol)):

        async def exercise(bound: list) -> None:
            tool = bound[0]
            if binding == "langgraph":

                async def call():
                    return await tool.ainvoke({})
            elif binding == "llamaindex":

                async def call():
                    return await tool.acall()
            else:

                async def call():
                    return await tool.call({})

            with pytest.raises(PIIDetected) as caught:
                await call()
            assert caught.value.policy == "mcp-pii-detector"
            if binding != "anthropic":
                assert "success" in str(await call())

        if binding == "anthropic":
            from mcp.client import streamable_http

            # MCP 1.x repeats the HTTP failure on close; 2.x closes cleanly.
            repeated = False
            try:
                with typed_refusals():
                    async with tools.anthropic() as bound:
                        await exercise(bound)
            except PIIDetected:
                repeated = True
            assert repeated == (getattr(streamable_http, "httpx2", None) is None)
            async with tools.anthropic() as bound:
                assert "success" in str(await bound[0].call({}))
        else:
            await exercise(await getattr(tools, binding)())


@pytest.mark.parametrize("suffix", ["/sse", "/sse/", "/sse/tools", "/mcp?transport=sse"])
async def test_llamaindex_legacy_sse_fails_closed(suffix: str) -> None:
    _require(BINDINGS["llamaindex"])
    tools = _tools("https://gateway.example" + suffix)
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ConfigError, match="legacy SSE"):
            await tools.llamaindex()


@pytest.mark.parametrize("binding", ["langgraph", "llamaindex"])
async def test_retained_native_binding_expires(binding: str) -> None:
    _require(BINDINGS[binding])
    tools = _tools()
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        bound = await getattr(tools, binding)()
    with pytest.raises(Exception) as caught:
        if binding == "langgraph":
            await bound[0].ainvoke({})
        else:
            await bound[0].acall()
    pending = [caught.value]
    messages = []
    while pending:
        error = pending.pop()
        messages.append(str(error))
        pending.extend(getattr(error, "exceptions", ()))
    assert any("MCP replay scope is closed" in message for message in messages)


@pytest.mark.parametrize(
    "binding",
    ["langgraph", "llamaindex", "anthropic", "agent_framework", "openai_agents", "describe"],
)
async def test_suspended_binding_never_falls_back_to_live_transport(binding: str) -> None:
    _require(BINDINGS["langgraph" if binding == "describe" else binding])
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedAuth:
        async def headers(self):
            entered.set()
            await release.wait()
            return {}

        async def invalidate(self):
            pass

    tools = _tools()
    tools._auth = PausedAuth()

    async def bind():
        if binding == "anthropic":
            async with tools.anthropic():
                pass
        else:
            await getattr(tools, binding)()

    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        task = asyncio.create_task(bind())
        await entered.wait()
    release.set()
    with pytest.raises(Exception) as caught:
        await task
    pending = [caught.value]
    messages = []
    while pending:
        error = pending.pop()
        messages.append(str(error))
        pending.extend(getattr(error, "exceptions", ()))
        if error.__cause__ is not None:
            pending.append(error.__cause__)
    assert any("MCP replay scope is closed" in message for message in messages)


@pytest.mark.parametrize("capture", ["pii-request", "pii-response-sse"])
async def test_agent_framework_native_pii_and_reconnect(capture: str) -> None:
    _require(BINDINGS["agent_framework"])
    tools = _tools()
    with simulate_mcp(tools, capture, baseline=httpx.MockTransport(_protocol)):
        tool = (await tools.agent_framework())[0]
        with pytest.raises(PIIDetected) as caught:
            with typed_refusals():
                async with tool:
                    await tool.functions[0].invoke(arguments={})
        assert caught.value.policy == "mcp-pii-detector"
        async with tool:
            result = await tool.functions[0].invoke(arguments={})
            assert any(content.text == "success" for content in result)


@pytest.mark.parametrize("failure", ["none", "initialize", "cancel", "cancel-success"])
async def test_agent_framework_owns_each_connection_client(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require(BINDINGS["agent_framework"])
    from donkey_kit.simulator.mcp import McpReplay

    clients = []
    original = McpReplay.client

    def track(self, *args, **kwargs):
        client = original(self, *args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(McpReplay, "client", track)
    entered = asyncio.Event()
    release = asyncio.Event()
    fail = False

    async def backend(request):
        if fail and request.method == "POST":
            if json.loads(request.content).get("method") == "initialize":
                entered.set()
                if failure.startswith("cancel"):
                    await release.wait()
                if failure != "cancel-success":
                    return httpx.Response(500)
        return _protocol(request)

    class Baseline(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True

    baseline = Baseline(backend)
    tools = _tools()
    with simulate_mcp(tools, "pii-request", baseline=baseline):
        tool = (await tools.agent_framework())[0]
        assert all(client.is_closed for client in clients)  # descriptor discovery
        fail = failure != "none"
        if failure.startswith("cancel"):
            task = asyncio.create_task(tool.connect())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            if failure == "cancel-success":

                async def wait_for_cleanup():
                    while not all(client.is_closed for client in clients):
                        await asyncio.sleep(0)

                await asyncio.wait_for(wait_for_cleanup(), timeout=5)
                assert not tool.is_connected
            else:
                await tool.close()
        elif failure == "initialize":
            with pytest.raises(Exception, match="initialize|session"):
                await tool.connect()
        else:
            async with tool:
                assert not clients[-1].is_closed
            assert clients[-1].is_closed
            async with tool:
                assert not clients[-1].is_closed
        assert all(client.is_closed for client in clients)
    assert not baseline.closed


async def test_agent_framework_retained_function_and_binding_expire() -> None:
    _require(BINDINGS["agent_framework"])
    tools = _tools()
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        tool = (await tools.agent_framework())[0]
        await tool.connect()
        function = tool.functions[0]
    try:
        with pytest.raises(Exception, match="closed"):
            await function.invoke(arguments={})
    finally:
        await tool.close()
    with pytest.raises(Exception, match="closed"):
        await tool.connect()


@pytest.mark.parametrize(
    "package, installed",
    [("agent-framework-core", "1.13.0"), ("mcp", "1.24.0"), ("mcp", "2.3.0rc1")],
)
async def test_agent_framework_old_stacks_refuse_before_auth(
    package: str, installed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require(BINDINGS["agent_framework"])
    import importlib.metadata

    actual = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata, "version", lambda name: installed if name == package else actual(name)
    )
    tools = _tools()
    tools._auth = None
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ConfigError, match="requires stable"):
            await tools.agent_framework()


@pytest.mark.parametrize("capture", ["abac-denied", "global-access", "schema-invalid-args"])
async def test_agent_framework_rpc_refusals_keep_existing_typing(capture: str) -> None:
    _require(BINDINGS["agent_framework"])
    from agent_framework.exceptions import ToolExecutionException

    tools = _tools()
    with simulate_mcp(tools, capture, baseline=httpx.MockTransport(_protocol)):
        tool = (await tools.agent_framework())[0]
        async with tool:
            expected = (
                ToolExecutionException if capture == "schema-invalid-args" else ToolNotInProfile
            )
            with pytest.raises(expected):
                with typed_refusals():
                    await tool.functions[0].invoke(arguments={})
            result = await tool.functions[0].invoke(arguments={})
            assert any(content.text == "success" for content in result)


async def test_agent_framework_catalog_poisoning_remains_ambiguous() -> None:
    _require(BINDINGS["agent_framework"])
    from donkey_kit.core.errors import ToolInvocationError

    tools = _tools()
    with simulate_mcp(tools, "poisoning-block-list", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ToolInvocationError):
            await tools.agent_framework()


async def test_agent_framework_scope_keeps_whole_server_approval_guard() -> None:
    _require(BINDINGS["agent_framework"])
    tools = _tools().require_approval(tools=["calculate"])
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ConfigError, match="cannot gate"):
            await tools.agent_framework()


@pytest.mark.parametrize(
    "capture",
    ["pii-request", "pii-response-sse", "abac-denied", "global-access", "schema-invalid-args"],
)
async def test_openai_agents_native_refusal_and_reconnect(capture: str) -> None:
    _require(BINDINGS["openai_agents"])
    from mcp.shared.exceptions import MCPError

    tools = _tools()
    expected = (
        PIIDetected
        if capture.startswith("pii-")
        else MCPError
        if capture == "schema-invalid-args"
        else ToolNotInProfile
    )
    with simulate_mcp(tools, capture, baseline=httpx.MockTransport(_protocol)):
        server = (await tools.openai_agents())[0]
        assert server.max_retry_attempts == 0
        async with server:
            assert [tool.name for tool in await server.list_tools()] == ["calculate"]
            with pytest.raises(expected), typed_refusals():
                await server.call_tool("calculate", {})
            assert (await server.call_tool("calculate", {})).content[0].text == "success"
        async with server:
            assert (await server.call_tool("calculate", {})).content[0].text == "success"


async def test_openai_agents_active_call_and_reconnect_expire() -> None:
    _require(BINDINGS["openai_agents"])
    tools = _tools()
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        server = (await tools.openai_agents())[0]
        await server.connect()
    try:
        with pytest.raises(Exception, match="closed"):
            await server.call_tool("calculate", {})
    finally:
        await server.cleanup()
    with pytest.raises(Exception) as caught:
        await server.connect()
    pending = [caught.value]
    messages = []
    while pending:
        error = pending.pop()
        messages.append(str(error))
        pending.extend(getattr(error, "exceptions", ()))
    assert any("MCP replay scope is closed" in message for message in messages)


@pytest.mark.parametrize("failure", ["none", "initialize", "cancel"])
async def test_openai_agents_owns_connection_clients(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require(BINDINGS["openai_agents"])
    from donkey_kit.simulator.mcp import McpReplay

    entered = asyncio.Event()
    clients = []
    original = McpReplay.client

    def tracked(self, *args, **kwargs):
        client = original(self, *args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(McpReplay, "client", tracked)

    async def respond(request):
        if json.loads(request.content).get("method") == "initialize":
            if failure == "initialize":
                return httpx.Response(500, json={"error": "offline failure"})
            if failure == "cancel":
                entered.set()
                await asyncio.Event().wait()
        return _protocol(request)

    class Baseline(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True

    baseline = Baseline(respond)
    # Cached descriptors keep failure injection on the native connection.
    tools = _tools()
    tools = tools._with(
        _servers=[
            dataclasses.replace(
                tools.servers[0],
                tool_descriptors=({"name": "calculate", "inputSchema": {"type": "object"}},),
            )
        ]
    )
    with simulate_mcp(tools, "pii-request", baseline=baseline):
        server = (await tools.openai_agents())[0]
        if failure == "initialize":
            from agents.exceptions import UserError

            with pytest.raises(UserError, match="500"):
                await server.connect()
        elif failure == "cancel":
            task = asyncio.current_task()

            async def cancel():
                await entered.wait()
                task.cancel()

            canceller = asyncio.create_task(cancel())
            with pytest.raises(asyncio.CancelledError):
                await server.connect()
            await canceller
        else:
            async with server:
                assert not clients[-1].is_closed
            async with server:
                assert not clients[-1].is_closed
        assert clients and all(client.is_closed for client in clients)
    assert not baseline.closed


@pytest.mark.parametrize(
    "package, installed",
    [("openai-agents", "0.19.0"), ("mcp", "1.24.0"), ("mcp", "2.3.0rc1")],
)
async def test_openai_agents_old_stacks_refuse_before_auth(
    package: str, installed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require(BINDINGS["openai_agents"])
    import importlib.metadata

    actual = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata, "version", lambda name: installed if name == package else actual(name)
    )
    tools = _tools()
    tools._auth = None
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ConfigError, match="requires stable"):
            await tools.openai_agents()


async def test_openai_agents_catalog_poisoning_remains_ambiguous() -> None:
    _require(BINDINGS["openai_agents"])
    from donkey_kit.core.errors import ToolInvocationError

    tools = _tools()
    with simulate_mcp(tools, "poisoning-block-list", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ToolInvocationError):
            await tools.openai_agents()


async def test_openai_agents_scope_keeps_whole_server_approval_guard() -> None:
    _require(BINDINGS["openai_agents"])
    tools = _tools().require_approval(tools=["calculate"])
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ConfigError, match="cannot gate"):
            await tools.openai_agents()


async def test_openai_agents_filtered_owner_and_unrelated_binding() -> None:
    _require(BINDINGS["openai_agents"])
    from donkey_kit.core.errors import ToolInvocationError

    tools, unrelated = _tools(), _tools()
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        server = (await tools.filter(allow=["calculate"]).openai_agents())[0]
        async with server:
            assert [tool.name for tool in await server.list_tools()] == ["calculate"]
            with pytest.raises(PIIDetected), typed_refusals():
                await server.call_tool("calculate", {})
        assert unrelated._mcp_transport == {}
        # Socket-disabled production discovery proves the other owner did not replay.
        with pytest.raises(ToolInvocationError):
            await unrelated.openai_agents()


async def test_openai_agents_scope_keeps_collision_guard() -> None:
    _require(BINDINGS["openai_agents"])
    tools = _tools()
    descriptor = ({"name": "calculate", "inputSchema": {"type": "object"}},)
    first = dataclasses.replace(tools.servers[0], tool_descriptors=descriptor)
    second = dataclasses.replace(first, ref=AssetRef("example", "other", "1.0.0"))
    tools = tools._with(_servers=[first, second])
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        with pytest.raises(ConfigError, match="cannot rename"):
            await tools.openai_agents()


async def test_openai_agents_cached_catalog_suspended_auth_expires() -> None:
    _require(BINDINGS["openai_agents"])
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedAuth:
        async def headers(self):
            entered.set()
            await release.wait()
            return {}

    tools = _tools()
    tools = tools._with(
        _servers=[dataclasses.replace(tools.servers[0], tool_descriptors=({"name": "calculate"},))]
    )
    tools._auth = PausedAuth()
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(_protocol)):
        task = asyncio.create_task(tools.openai_agents())
        await entered.wait()
    release.set()
    server = (await task)[0]
    with pytest.raises(Exception) as caught:
        await server.connect()
    pending = [caught.value]
    messages = []
    while pending:
        error = pending.pop()
        messages.append(str(error))
        pending.extend(getattr(error, "exceptions", ()))
    assert any("MCP replay scope is closed" in message for message in messages)
