"""Bounded native Strands capture replay (BG sections 1.4 / 1.5, #1391)."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.metadata
import json
import subprocess
import sys
import threading
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from test_tool_binding import BINDINGS, _require

from donkey_kit import typed_refusals
from donkey_kit.core.errors import ConfigError
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.simulator.mcp import McpReplay, mcp_baseline, simulate_mcp
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet

_URL = "https://gateway.example/mcp/"
_DESCRIPTOR = {
    "name": "calculate",
    "description": "Offline tool",
    "inputSchema": {"type": "object", "properties": {}},
}
_CAPTURES = [
    "pii-request",
    "pii-response-sse",
    "schema-invalid-args",
    "poisoning-block-list",
    "abac-denied",
    "global-access",
]


def _tools(*, cached: bool = True) -> ToolSet:
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("example", "tools", "1.0.0"),
                endpoint_url=_URL,
                governance=GovernanceReport(
                    governed=True, checks=[Check("deployed", True, "offline")]
                ),
                tool_descriptors=(_DESCRIPTOR,) if cached else None,
            )
        ],
        _auth=ClientIdAuth("offline-id", "offline-secret"),
    )


@pytest.mark.parametrize(
    "package, installed",
    [
        ("strands-agents", "1.57.1"),
        ("strands-agents", "1.63.0"),
        ("strands-agents", "1.64.0rc1"),
        ("mcp", "1.30.0"),
        ("mcp", "2.2.0"),
        ("mcp", "2.3.0rc1"),
    ],
)
async def test_strands_replay_versions_refuse_before_auth(
    package: str, installed: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require(BINDINGS["strands"])
    actual = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata, "version", lambda name: installed if name == package else actual(name)
    )
    tools = _tools(cached=False)
    tools._auth = None
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(lambda r: None)):
        with pytest.raises(
            ConfigError, match="requires stable strands-agents>=1.64.0 and mcp>=2.3.0"
        ):
            await tools.strands()


async def test_strands_production_binding_does_not_check_replay_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require(BINDINGS["strands"])

    def unexpected(name):
        raise AssertionError("Production binding must not check replay versions")

    monkeypatch.setattr(importlib.metadata, "version", unexpected)
    assert len(await _tools().strands()) == 1


@pytest.mark.parametrize("guard", ["approval", "collision", "empty"])
async def test_strands_scope_keeps_whole_server_guards(guard: str) -> None:
    _require(BINDINGS["strands"])
    tools = _tools()
    if guard == "approval":
        tools = tools.require_approval(tools=["calculate"])
    elif guard == "collision":
        tools = tools._with(
            _servers=[
                tools.servers[0],
                dataclasses.replace(tools.servers[0], ref=AssetRef("example", "other", "1.0.0")),
            ]
        )
    else:
        tools = tools.filter(deny=["calculate"])
    with simulate_mcp(tools, "pii-request", baseline=httpx.MockTransport(lambda r: None)):
        if guard == "empty":
            assert await tools.strands() == []
            assert [entry.name for entry in tools.excluded] == ["calculate"]
        else:
            with pytest.raises(
                ConfigError, match="cannot gate" if guard == "approval" else "cannot rename"
            ):
                await tools.strands()


@pytest.mark.parametrize(
    "case",
    [
        *_CAPTURES,
        "discovery",
        "expired",
        "initialize",
        "cancel",
        "suspended",
        "suspended-discovery",
    ],
)
def test_strands_native_replay_is_bounded(case: str) -> None:
    _require(BINDINGS["strands"])
    # A native stop joins its background thread without a deadline. Isolate it
    # so an upstream regression cannot hang the whole adapter-contract session.
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), case],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STRANDS_REPLAY_OK" in result.stdout
    # Leave upstream warnings visible; do not patch Strands' runtime cleanup.
    if result.stderr:
        print(result.stderr)


async def _native(case: str) -> None:
    from mcp.shared.exceptions import MCPError
    from strands.types.exceptions import MCPClientInitializationError

    initial_threads = set(threading.enumerate())
    tools = _tools(cached=case not in {"discovery", "suspended-discovery"})
    if tools.servers[0].tool_descriptors is not None:
        tools = tools._with(
            _servers=[
                dataclasses.replace(
                    tools.servers[0],
                    tool_descriptors=(_DESCRIPTOR, {**_DESCRIPTOR, "name": "excluded"}),
                )
            ]
        )
    clients, requests, closes, wire = [], [], [], []
    state_lock = threading.Lock()
    cancel_fired, request_cancelled = threading.Event(), threading.Event()
    original = McpReplay.client

    def track(self, *args, **kwargs):
        client = original(self, *args, **kwargs)
        clients.append(client)

        async def observe(response):
            await response.aread()
            rpc = json.loads(response.request.content)
            if rpc.get("method") == (
                "tools/list" if case == "poisoning-block-list" else "tools/call"
            ):
                wire.append(
                    (
                        rpc["id"],
                        response.status_code,
                        response.headers.get("content-type"),
                        response.text,
                    )
                )

        if case == "cancel":

            async def cancel(request):
                if json.loads(request.content).get("method") == "initialize":
                    task = asyncio.current_task()
                    assert task is not None

                    def cancel_task():
                        cancel_fired.set()
                        task.cancel("strands replay cancellation")

                    asyncio.get_running_loop().call_later(0.05, cancel_task)
                    try:
                        await asyncio.sleep(10)
                    except asyncio.CancelledError as error:
                        assert error.args == ("strands replay cancellation",)
                        request_cancelled.set()
                        raise

            client.event_hooks["request"].append(cancel)
        client.event_hooks["response"].append(observe)
        return client

    McpReplay.client = track
    delegate = mcp_baseline(
        _URL,
        tools=[_DESCRIPTOR, {**_DESCRIPTOR, "name": "excluded"}],
        call_tool=lambda name, arguments: {"content": [{"type": "text", "text": "success"}]},
        server_protocol_version="unsupported-test-version"
        if case == "initialize"
        else "2025-06-18",
    )

    def respond(request):
        with state_lock:
            requests.append(json.loads(request.content).get("method"))
        return delegate.handle_request(request)

    class Baseline(httpx.MockTransport):
        async def aclose(self):
            closes.append("async")

        def close(self):
            closes.append("sync")

    baseline = Baseline(respond)
    scope = simulate_mcp(tools, case if case in _CAPTURES else "pii-request", baseline=baseline)
    replay = scope.__enter__()
    try:
        if case in {"suspended", "suspended-discovery"}:
            entered, release = asyncio.Event(), asyncio.Event()

            class PausedAuth:
                async def headers(self):
                    entered.set()
                    await release.wait()
                    return {}

            tools._auth = PausedAuth()
            task = asyncio.create_task(tools.strands())
            await entered.wait()
            scope.__exit__(None, None, None)
            release.set()
            if case == "suspended-discovery":
                with pytest.raises(Exception) as caught:
                    await task
                pending, messages = [caught.value], []
                while pending:
                    error = pending.pop()
                    messages.append(str(error))
                    pending.extend(getattr(error, "exceptions", ()))
                    if error.__cause__ is not None:
                        pending.append(error.__cause__)
                assert any("MCP replay scope is closed" in message for message in messages)
                assert clients and all(client.is_closed for client in clients)
                assert requests == closes == []
                return
            native = (await task)[0]
        else:
            derived = tools.filter(allow=["calculate"])
            native = (await derived.strands())[0]
            if case != "discovery":
                assert [entry.name for entry in derived.excluded] == ["excluded"]
            assert _tools()._mcp_transport == {}
        native._startup_timeout = 1

        def call():
            return native.call_tool_sync(
                "call", "calculate", {}, read_timeout_seconds=timedelta(seconds=2)
            )

        if case in {"initialize", "cancel", "suspended"}:
            with pytest.raises(MCPClientInitializationError) as caught:
                native.start()
            if case == "cancel":
                # Only the callback's cancellation token can mark the hook interrupted.
                assert cancel_fired.is_set(), "scheduled cancellation callback did not fire"
                assert request_cancelled.is_set(), "cancellation did not interrupt the request hook"
                # Strands still waits for startup timeout after the request task is cancelled.
                assert isinstance(caught.value.__cause__, TimeoutError)
                assert "initialize" not in requests, "cancelled initialize reached the baseline"
            native.stop(None, None, None)
            assert requests == [] if case == "suspended" else "tools/call" not in requests
        elif case == "expired":
            native.start()
            retained = native.list_tools_sync()[0]
            assert call()["status"] == "error"
            scope.__exit__(None, None, None)
            count = len(requests)
            events = [
                event
                async for event in retained.stream(
                    {"toolUseId": "retained", "name": "calculate", "input": {}}, {}
                )
            ]
            assert events[-1]["tool_result"]["status"] == "error"
            with pytest.raises(MCPClientInitializationError):
                call()
            with pytest.raises(MCPClientInitializationError):
                native.list_tools_sync()
            with pytest.raises(RuntimeError, match="closed"):
                native.stop(None, None, None)
            with pytest.raises(MCPClientInitializationError):
                native.start()
            native.stop(None, None, None)
            assert len(requests) == count
        else:
            for attempt in range(2):
                with native:
                    if case == "poisoning-block-list" and attempt == 0:
                        with pytest.raises(MCPError) as caught:
                            native.list_tools_sync()
                        assert caught.value.error.code == -32603
                    assert [tool.tool_name for tool in native.list_tools_sync()] == ["calculate"]
                    if case != "poisoning-block-list" and attempt == 0:
                        with typed_refusals():
                            result = call()
                        assert result["status"] == "error"
                        assert set(result) == {"status", "toolUseId", "content"}
                        assert result["content"][0]["text"].startswith("Tool execution failed:")
                    assert call()["status"] == "success"
                    assert (
                        await native.call_tool_async(
                            "async", "calculate", {}, read_timeout_seconds=timedelta(seconds=2)
                        )
                    )["status"] == "success"
                assert all(client.is_closed for client in clients)
            with pytest.raises(MCPClientInitializationError):
                call()
            if case in _CAPTURES:
                request_id, status, content_type, text = wire[0]
                payload = json.loads(
                    text.split("data: ", 1)[1] if content_type == "text/event-stream" else text
                )
                expected = replay._fixture
                original_payload = json.loads(
                    expected.body.split(b"data: ", 1)[1]
                    if expected.content_type == "text/event-stream"
                    else expected.body
                )
                assert payload["id"] == request_id
                assert payload["error"] == original_payload["error"]
                assert status == expected.status
                assert content_type == expected.content_type
        assert clients and all(client.is_closed for client in clients)
        assert closes == []
        assert (
            baseline.handle_request(
                httpx.Request(
                    "POST", _URL, json={"jsonrpc": "2.0", "id": 90, "method": "tools/list"}
                )
            ).status_code
            == 200
        )
        assert not [thread for thread in threading.enumerate() if thread not in initial_threads]
    finally:
        scope.__exit__(None, None, None)
        McpReplay.client = original
    assert tools._mcp_transport == {}


if __name__ == "__main__":
    import pytest_socket

    pytest_socket.disable_socket(allow_unix_socket=True)
    asyncio.run(asyncio.wait_for(_native(sys.argv[1]), timeout=20))
    print("STRANDS_REPLAY_OK")
