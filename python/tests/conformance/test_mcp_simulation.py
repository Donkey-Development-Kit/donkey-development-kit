"""Offline native MCP injection contracts (BG sections 1.4 / 1.5, #1391)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from test_tool_binding import BINDINGS, _require

from donkey_kit import typed_refusals
from donkey_kit.core.errors import ConfigError, PIIDetected
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.simulator.mcp import simulate_mcp
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


def _protocol(request: httpx.Request) -> httpx.Response:
    if request.method != "POST":
        return httpx.Response(405)
    rpc = json.loads(request.content)
    if "id" not in rpc:
        return httpx.Response(202)
    if rpc["method"] == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "serverInfo": {"name": "offline", "version": "1"},
        }
    elif rpc["method"] == "tools/list":
        result = {
            "tools": [
                {
                    "name": "calculate",
                    "description": "Offline tool",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }
    else:
        result = {"content": [{"type": "text", "text": "success"}]}
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": rpc["id"], "result": result})


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


@pytest.mark.parametrize("binding", ["langgraph", "llamaindex", "anthropic", "describe"])
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
