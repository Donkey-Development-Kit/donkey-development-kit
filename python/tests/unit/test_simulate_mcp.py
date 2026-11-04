"""ToolSet-owned offline MCP fault replay (BG sections 1.4 / 1.5, #1391)."""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from donkey_kit.core.errors import PIIDetected, classify
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.simulator.mcp import simulate_mcp
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet

_URL = "https://gateway.example/mcp/"


@pytest.fixture(autouse=True)
def http_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise replay logic without an optional MCP installation."""
    client = ModuleType("mcp.client")
    client.streamable_http = SimpleNamespace(httpx=httpx)
    monkeypatch.setitem(sys.modules, "mcp", ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.client", client)


def toolset() -> ToolSet:
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("example", "tools", "1.0.0"),
                endpoint_url=_URL,
                governance=GovernanceReport(
                    governed=True, checks=[Check("deployed", True, "offline")]
                ),
            )
        ],
        _auth=ClientIdAuth("offline-id", "offline-secret"),
    )


def baseline(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"baseline": True})


def protocol(request: httpx.Request) -> httpx.Response:
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


@pytest.mark.disable_socket
async def test_matching_rpc_only_and_native_id_adaptation() -> None:
    tools = toolset()
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(baseline)) as replay:
        async with replay.client(_URL) as client:
            response = await client.post(
                _URL, json={"jsonrpc": "2.0", "id": "call-99", "method": "tools/call"}
            )
            assert response.status_code == 403
            assert response.json()["id"] == "call-99"
            assert type(classify(response)) is PIIDetected
            assert response.headers["x-donkey-simulator"] == "true"
            assert (await client.post(_URL, json={"id": 2, "method": "tools/call"})).json() == {
                "baseline": True
            }


@pytest.mark.disable_socket
async def test_nonmatching_requests_do_not_consume_count() -> None:
    with simulate_mcp(
        toolset(), "schema-invalid-args", baseline=httpx.MockTransport(baseline)
    ) as replay:
        async with replay.client(_URL) as client:
            for method in ("initialize", "tools/list", "notifications/initialized"):
                assert (await client.post(_URL, json={"id": 0, "method": method})).json() == {
                    "baseline": True
                }
            response = await client.post(_URL, json={"id": 27, "method": "tools/call"})
            assert response.json()["id"] == 27
            assert response.json()["error"]["code"] == -32602


@pytest.mark.disable_socket
async def test_sse_envelope_id_adaptation() -> None:
    with simulate_mcp(
        toolset(), "poisoning-block-list", baseline=httpx.MockTransport(baseline)
    ) as replay:
        async with replay.client(_URL) as client:
            response = await client.post(_URL, json={"id": 88, "method": "tools/list"})
            assert response.headers["content-type"] == "text/event-stream"
            envelope = json.loads(response.text.split("data: ", 1)[1])
            assert envelope["id"] == 88
            assert envelope["error"]["code"] == -32603


@pytest.mark.disable_socket
async def test_client_cannot_escape_endpoint_or_context() -> None:
    with simulate_mcp(toolset(), "pii-request", baseline=httpx.MockTransport(baseline)) as replay:
        client = replay.client(_URL)
        with pytest.raises(ValueError, match="endpoint"):
            await client.get("https://other.example/mcp/")
    with pytest.raises(RuntimeError, match="closed"):
        await client.post(_URL, json={"id": 1, "method": "tools/call"})
    await client.aclose()


def test_requires_offline_transport_and_known_capture() -> None:
    with pytest.raises(TypeError, match="MockTransport"):
        with simulate_mcp(toolset(), "pii-request", baseline=httpx.AsyncHTTPTransport()):
            pass


@pytest.mark.disable_socket
async def test_scope_is_shared_by_derived_sets_and_restored_after_nesting() -> None:
    tools = toolset()
    derived = tools.filter(allow=["*"])
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(baseline)) as outer:
        assert derived._mcp_transport is tools._mcp_transport
        with simulate_mcp(derived, "schema-invalid-args", baseline=httpx.MockTransport(baseline)):
            assert tools._mcp_transport["factory"] != outer.client
        assert tools._mcp_transport["factory"] == outer.client
        assert toolset()._mcp_transport == {}
    assert tools._mcp_transport == derived._mcp_transport == {}


async def test_unsupported_bindings_fail_before_optional_import_or_connection() -> None:
    tools = toolset()
    # The plan guard is checked without importing or constructing native bindings.
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(baseline)):
        from donkey_kit.core.errors import ConfigError

        with pytest.raises(ConfigError, match="only LangGraph"):
            await tools._plan("adk()", per_tool=False)
    with pytest.raises(ValueError, match="capture"):
        with simulate_mcp(toolset(), "made-up", baseline=httpx.MockTransport(baseline)):
            pass


@pytest.mark.disable_socket
async def test_counts_shared_across_clients_under_concurrency() -> None:
    with simulate_mcp(
        toolset(), "pii-request", baseline=httpx.MockTransport(baseline), times=2
    ) as replay:
        async with replay.client(_URL) as one, replay.client(_URL) as two:
            responses = await asyncio.gather(
                *[
                    (one if i % 2 else two).post(_URL, json={"id": i, "method": "tools/call"})
                    for i in range(8)
                ]
            )
            assert sorted(response.status_code for response in responses) == [200] * 6 + [403] * 2


@pytest.mark.disable_socket
async def test_cancellation_restores_owner_and_closes_retained_client() -> None:
    tools = toolset()
    with pytest.raises(asyncio.CancelledError):
        with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(baseline)) as replay:
            client = replay.client(_URL)
            raise asyncio.CancelledError
    assert tools._mcp_transport == {}
    with pytest.raises(RuntimeError, match="closed"):
        await client.get(_URL)
    await client.aclose()


@pytest.mark.disable_socket
@pytest.mark.parametrize(
    "capture", ["schema-invalid-args", "poisoning-block-list", "abac-denied", "global-access"]
)
async def test_captured_error_payload_is_unchanged_except_id(capture: str) -> None:
    with simulate_mcp(toolset(), capture, baseline=httpx.MockTransport(baseline)) as replay:
        original = replay._fixture.body
        sse = replay._fixture.content_type == "text/event-stream"
        expected = json.loads(original.split(b"data: ", 1)[1] if sse else original)
        async with replay.client(_URL) as client:
            response = await client.post(_URL, json={"id": "new-id", "method": replay._method})
        actual = json.loads(response.content.split(b"data: ", 1)[1] if sse else response.content)
        expected["id"] = "new-id"
        assert actual == expected
        assert replay._fixture.body == original


@pytest.mark.disable_socket
async def test_compressed_baseline_is_not_decoded_twice() -> None:
    def compressed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-encoding": "gzip"}, content=gzip.compress(b'{"ok":true}')
        )

    with simulate_mcp(toolset(), "pii-request", baseline=httpx.MockTransport(compressed)) as replay:
        async with replay.client(_URL) as client:
            response = await client.post(_URL, json={"id": 1, "method": "tools/list"})
            assert response.json() == {"ok": True}
            assert "content-encoding" not in response.headers
