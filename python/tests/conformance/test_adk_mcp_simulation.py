"""Evidence that ADK cannot back ``simulate_mcp`` (BG sections 1.4 / 1.5, #1643).

Offline and socket-free. ``ToolSet.adk()`` stays refused under ``simulate_mcp``
(see ``tests/unit/test_simulate_mcp.py``) because ADK's session manager can
replace the injected ``httpx_client_factory`` with Google's mTLS transport, so
replay is not fail-closed. The lifecycle test shows the replay itself works on a
resolver-compatible stack; the bypass test is a canary that fails once ADK stops
substituting the transport, which is the cue to revisit the guard.
"""

from __future__ import annotations

import functools
import json
from importlib.metadata import PackageNotFoundError, version

import httpx
import pytest
from test_strands_mcp_simulation import _DESCRIPTOR, _URL, _tools
from test_tool_binding import BINDINGS, _require

from donkey_kit.simulator.mcp import McpReplay, mcp_baseline, simulate_mcp


def _adk() -> tuple[type, type, type]:
    """The ADK classes, skipping where ADK cannot import against the installed ``mcp``."""
    _require(BINDINGS["adk"])
    try:
        mcp_release = tuple(int(part) for part in version("mcp").split(".")[:2])
    except (PackageNotFoundError, ValueError):  # pragma: no cover
        pytest.skip("mcp version unreadable")
    if mcp_release < (2, 3):
        pytest.skip("simulate_mcp needs mcp>=2.3.0")
    try:
        from google.adk.tools.mcp_tool import (
            McpToolset,
            StreamableHTTPConnectionParams,
            mcp_session_manager,
        )
    except ImportError as exc:  # ADK 2.4.0-2.8.0 import mcp.shared.session (#1643)
        pytest.skip(f"google-adk does not import on this mcp: {exc}")
    return McpToolset, StreamableHTTPConnectionParams, mcp_session_manager.MCPSessionManager


def _baseline(seen: list[str]) -> httpx.MockTransport:
    delegate = mcp_baseline(
        _URL,
        tools=[_DESCRIPTOR],
        call_tool=lambda name, arguments: {"content": [{"type": "text", "text": "success"}]},
    )

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content).get("method"))
        return delegate.handle_request(request)

    return httpx.MockTransport(respond)


@pytest.mark.disable_socket
async def test_adk_replay_lifecycle_when_mtls_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    toolset_cls, params_cls, _ = _adk()
    monkeypatch.setenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
    seen: list[str] = []
    clients: list[httpx.AsyncClient] = []
    original = McpReplay.client

    def track(self: McpReplay, *args: object, **kwargs: object) -> httpx.AsyncClient:
        client = original(self, *args, **kwargs)  # type: ignore[arg-type]
        clients.append(client)
        return client

    monkeypatch.setattr(McpReplay, "client", track)
    tools = _tools()
    with simulate_mcp(tools, "pii-request", baseline=_baseline(seen)) as replay:
        toolset = toolset_cls(
            connection_params=params_cls(
                url=_URL, headers={}, httpx_client_factory=functools.partial(replay.client, _URL)
            ),
            tool_filter=["calculate"],
        )
        try:
            tool = (await toolset.get_tools())[0]
            first = await tool.run_async(args={}, tool_context=None)
            # ADK returns the captured refusal as data: typed_refusals() cannot see it.
            assert "error" in first
            second = await tool.run_async(args={}, tool_context=None)
            assert second["content"][0]["text"] == "success"
        finally:
            await toolset.close()
    assert seen.count("tools/call") == 1
    assert clients and all(client.is_closed for client in clients)
    assert tools._mcp_transport == {}


@pytest.mark.disable_socket
async def test_adk_mtls_transport_bypasses_the_injected_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toolset_cls, params_cls, manager_cls = _adk()
    monkeypatch.delenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", raising=False)
    escaped: list[str] = []

    class Escape(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            escaped.append(str(request.url))
            return httpx.Response(500, request=request)

    async def mtls(self: object) -> Escape:
        return Escape()

    monkeypatch.setattr(manager_cls, "_get_mtls_transport", mtls)
    clients: list[httpx.AsyncClient] = []
    original = McpReplay.client

    def track(self: McpReplay, *args: object, **kwargs: object) -> httpx.AsyncClient:
        client = original(self, *args, **kwargs)  # type: ignore[arg-type]
        clients.append(client)
        return client

    monkeypatch.setattr(McpReplay, "client", track)
    with simulate_mcp(_tools(), "pii-request", baseline=_baseline([])) as replay:
        toolset = toolset_cls(
            connection_params=params_cls(
                url=_URL, headers={}, httpx_client_factory=functools.partial(replay.client, _URL)
            )
        )
        try:
            with pytest.raises(Exception):  # noqa: B017 -- ADK wraps the failure
                await toolset.get_tools()
        finally:
            await toolset.close()
    # Not fail-closed: the request left through the substituted transport and the
    # scoped replay client was never built. If this starts failing, ADK closed the
    # gap: re-evaluate the ``adk()`` guard (docs/verified-apis.md section 9).
    assert escaped == [_URL]
    assert clients == []
