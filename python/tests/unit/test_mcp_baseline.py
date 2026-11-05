"""Stateless offline MCP baseline contracts (BG sections 1.4 / 1.5, #1391)."""

from __future__ import annotations

import httpx
import pytest

from donkey_kit.simulator.mcp import mcp_baseline

_URL = "https://gateway.example/mcp/"
_TOOLS = [{"name": "calculate", "inputSchema": {"type": "object", "properties": {}}}]
pytestmark = pytest.mark.disable_socket


def _call(name, arguments):
    return {"content": [{"type": "text", "text": f"{name}: {arguments}"}]}


@pytest.mark.parametrize("version", ["2025-06-18", "unsupported-test-version"])
async def test_server_response_version_does_not_rewrite_client_request(version: str) -> None:
    baseline = mcp_baseline(_URL, tools=_TOOLS, call_tool=_call, server_protocol_version=version)
    async with httpx.AsyncClient(transport=baseline) as client:
        response = await client.post(
            _URL,
            json={
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {"protocolVersion": "client-version"},
            },
        )
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "init-1",
        "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "offline", "version": "1"},
        },
    }
    assert response.headers["x-donkey-simulator"] == "true"


async def test_lists_supplied_tools_and_dispatches_only_known_names() -> None:
    calls = []

    def call(name, arguments):
        calls.append((name, arguments))
        return _call(name, arguments)

    tools = [{"name": "calculate", "inputSchema": {"type": "object"}}]
    baseline = mcp_baseline(_URL, tools=tools, call_tool=call)
    tools[0]["name"] = "changed-after-construction"
    async with httpx.AsyncClient(transport=baseline) as client:
        listed = await client.post(_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert listed.json()["result"]["tools"][0]["name"] == "calculate"
        for name in ("calculate", "missing"):
            response = await client.post(
                _URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": {"x": 3}},
                },
            )
            if name == "calculate":
                assert response.json()["result"] == _call(name, {"x": 3})
            else:
                assert response.json()["error"]["code"] == -32602
    assert calls == [("calculate", {"x": 3})]


async def test_transport_is_endpoint_scoped_and_handles_native_session_traffic() -> None:
    async with httpx.AsyncClient(
        transport=mcp_baseline(_URL, tools=_TOOLS, call_tool=_call)
    ) as client:
        with pytest.raises(ValueError, match="endpoint"):
            await client.get("https://other.example/mcp/")
        assert (await client.get(_URL)).status_code == 405
        assert (await client.delete(_URL)).status_code == 405
        initialized = await client.post(
            _URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert initialized.status_code == 202
        ping = await client.post(_URL, json={"jsonrpc": "2.0", "id": 3, "method": "ping"})
        assert ping.json()["result"] == {}
        discover = await client.post(_URL, json={"jsonrpc": "2.0", "id": 4, "method": "discover"})
        assert discover.status_code == 404


async def test_normalizes_configured_endpoint_without_allowing_other_paths_or_queries() -> None:
    async with httpx.AsyncClient(
        transport=mcp_baseline("https://GATEWAY.example:443/mcp/", tools=_TOOLS, call_tool=_call)
    ) as client:
        assert (await client.get(_URL)).status_code == 405
        for escaped in (_URL + "?other=1", _URL + "other", "https://gateway.example/mcp"):
            with pytest.raises(ValueError, match="endpoint"):
                await client.get(escaped)


@pytest.mark.parametrize(
    "payload, code",
    [
        (b"not-json", -32700),
        (b"[]", -32600),
        (b'{"jsonrpc":"2.0","id":true,"method":"ping"}', -32600),
        (b'{"jsonrpc":"2.0","id":1,"method":"unknown"}', -32601),
        (b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":[]}', -32602),
        (b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":[]}', -32602),
        (b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":42}', -32602),
        (b'{"jsonrpc":"2.0","id":1,"method":"ping","params":null}', -32602),
        (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call",'
            b'"params":{"name":"calculate","arguments":[]}}',
            -32602,
        ),
    ],
)
async def test_invalid_requests_do_not_become_success(payload: bytes, code: int) -> None:
    async with httpx.AsyncClient(
        transport=mcp_baseline(_URL, tools=_TOOLS, call_tool=_call)
    ) as client:
        response = await client.post(_URL, content=payload)
    assert response.json()["error"]["code"] == code


def test_rejects_ambiguous_configuration() -> None:
    with pytest.raises(ValueError, match="version"):
        mcp_baseline(_URL, tools=_TOOLS, call_tool=_call, server_protocol_version="")
    with pytest.raises(ValueError, match="unique"):
        mcp_baseline(_URL, tools=_TOOLS * 2, call_tool=_call)
    with pytest.raises(ValueError, match="inputSchema"):
        mcp_baseline(_URL, tools=[{"name": "missing-schema"}], call_tool=_call)
