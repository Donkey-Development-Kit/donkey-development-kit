"""Explicit offline MCP capture injection (BG sections 1.4 / 1.5, #1391)."""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator, Mapping
from typing import Any

import httpx

from .._testing import mcp_factory_scope
from ..tools.session import ToolSet
from .fixtures import Fixture, fixture_bytes, parse_headers, parse_status, replay_headers

__all__ = ["McpReplay", "simulate_mcp"]

_METHODS = {
    "pii-request": "tools/call",
    "pii-response-sse": "tools/call",
    "schema-invalid-args": "tools/call",
    "poisoning-block-list": "tools/list",
    "abac-denied": "tools/call",
    "global-access": "tools/call",
}


class McpReplay:
    """An offline capture scope; clients and derived bindings expire with the scope.

    The baseline is a caller-owned MockTransport, never a network fallback.
    Response IDs are adapted for native MCP session correlation; packaged bytes
    remain unchanged. Construct through :func:`simulate_mcp`.
    """

    def __init__(
        self, tools: ToolSet, capture: str, baseline: httpx.MockTransport, times: int
    ) -> None:
        self._urls = frozenset(server.endpoint_url for server in tools.servers)
        self._method = _METHODS[capture]
        self._baseline = baseline
        self._remaining = times
        self._active = True
        self._lock = threading.Lock()
        if capture == "global-access":
            directory = "anypoint/mcp_access"
            prefix = "tools-call.global-access-denied"
            raw = fixture_bytes(directory, prefix + ".headers.txt").decode()
            status = parse_status(raw)
            if status is None:
                raise ValueError("Capture has no HTTP status")
            headers = parse_headers(raw)
            body = fixture_bytes(directory, prefix + ".body.json")
        else:
            envelope = json.loads(fixture_bytes("anypoint/mcp_policies", capture + ".json"))
            status, headers, body = (
                envelope["status"],
                envelope["headers"],
                envelope["body"].encode(),
            )
        self._fixture = Fixture(capture, status, headers, body, headers["content-type"])

    def client(
        self,
        url: str,
        credentials: Mapping[str, str] | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: Any = None,  # noqa: ANN401 -- HTTP dialect seam
        auth: Any = None,  # noqa: ANN401 -- HTTP dialect seam
    ) -> Any:  # noqa: ANN401 -- optional MCP httpx/httpx2 seam
        """Build a scoped client in the installed MCP SDK's HTTP dialect."""
        if url not in self._urls:
            raise ValueError("MCP replay endpoint is not in this ToolSet")
        from mcp.client import streamable_http

        http_lib = getattr(streamable_http, "httpx2", None) or streamable_http.httpx

        async def mark(request: Any) -> None:  # noqa: ANN401 -- HTTP dialect seam
            if str(request.url) == url:
                request.extensions["donkey_mcp_binding"] = True

        async def read_error(response: Any) -> None:  # noqa: ANN401 -- HTTP dialect seam
            if response.status_code >= 400:
                await response.aread()

        async def respond(request: Any) -> Any:  # noqa: ANN401 -- HTTP dialect seam
            content = await request.aread()
            with self._lock:
                if not self._active:
                    raise RuntimeError("MCP replay scope is closed")
                if str(request.url) != url:
                    raise ValueError("MCP replay request escaped its endpoint")
                try:
                    rpc = json.loads(content)
                except (ValueError, UnicodeDecodeError):
                    rpc = None
                matches = (
                    request.method == "POST"
                    and isinstance(rpc, dict)
                    and rpc.get("method") == self._method
                    and type(rpc.get("id")) in (str, int)
                )
                inject = matches and self._remaining > 0
                if inject:
                    self._remaining -= 1
            if inject:
                assert isinstance(rpc, dict)
                fixture = self._fixture
                body = fixture.body
                sse = fixture.content_type == "text/event-stream"
                payload = json.loads(body.split(b"data: ", 1)[1] if sse else body)
                payload["id"] = rpc["id"]
                body = json.dumps(payload, separators=(",", ":")).encode()
                if sse:
                    body = b"event: message\ndata: " + body + b"\n\n"
                response_headers = {
                    **replay_headers(fixture),
                    "content-type": fixture.content_type or "application/json",
                    "x-donkey-simulator": "true",
                }
                return http_lib.Response(fixture.status, headers=response_headers, content=body)
            original = httpx.Request(
                request.method, str(request.url), headers=dict(request.headers), content=content
            )
            response = await self._baseline.handle_async_request(original)
            try:
                body = await response.aread()
                return http_lib.Response(
                    response.status_code,
                    headers={
                        key: value
                        for key, value in response.headers.items()
                        if key not in {"content-encoding", "content-length", "transfer-encoding"}
                    },
                    content=body,
                )
            finally:
                await response.aclose()

        return http_lib.AsyncClient(
            headers=dict(headers or credentials or {}),
            timeout=timeout or 30,
            auth=auth,
            trust_env=False,
            follow_redirects=False,
            transport=http_lib.MockTransport(respond),
            event_hooks={"request": [mark], "response": [read_error]},
        )

    def close(self) -> None:
        """Disable every client made by this scope, including background-task clients."""
        with self._lock:
            self._active = False


@contextlib.contextmanager
def simulate_mcp(
    tools: ToolSet, capture: str, *, baseline: httpx.MockTransport, times: int = 1
) -> Iterator[McpReplay]:
    """Inject a named MCP capture while binding and calling this ToolSet offline.

    Bind/connect inside the context. Only LangGraph, LlamaIndex and Anthropic
    bindings are initially supported; other bindings fail before connecting.
    Unmatched traffic uses the required offline baseline. No registry or LLM
    transport is changed. Captured schema/poisoning errors stay ambiguous.
    """
    if not isinstance(baseline, httpx.MockTransport):
        raise TypeError("baseline must be an offline httpx.MockTransport")
    if capture not in _METHODS:
        raise ValueError(f"Unknown MCP capture {capture!r}; choose from {', '.join(_METHODS)}")
    if type(times) is not int or times < 1:
        raise ValueError("times must be a positive integer")
    replay = McpReplay(tools, capture, baseline, times)
    try:
        with mcp_factory_scope(tools, replay.client):
            yield replay
    finally:
        replay.close()
