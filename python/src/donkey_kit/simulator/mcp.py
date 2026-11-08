"""Explicit offline MCP capture injection (BG sections 1.4 / 1.5, #1391)."""

from __future__ import annotations

import contextlib
import copy
import json
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import httpx

from .._testing import mcp_discovery_scope, mcp_factory_scope
from ..registry.criteria import Check, GovernanceReport
from ..registry.models import AssetRef, McpServerHandle
from ..tools.auth import ClientIdAuth
from ..tools.session import ToolSet
from .fixtures import Fixture, fixture_bytes, parse_headers, parse_status, replay_headers

if TYPE_CHECKING:
    from ..donkey import Donkey

__all__ = ["McpReplay", "mcp_baseline", "simulate_declared", "simulate_mcp"]

#: Where a declared server "lives": the reserved ``.invalid`` TLD never resolves,
#: so a request that escaped the replay could not reach a host (RFC 2606).
_DECLARED_HOST = "https://simulated.donkey-kit.invalid"
_EMPTY_SCHEMA: dict[str, object] = {"type": "object", "properties": {}}

_METHODS = {
    "pii-request": "tools/call",
    "pii-response-sse": "tools/call",
    "schema-invalid-args": "tools/call",
    "poisoning-block-list": "tools/list",
    "abac-denied": "tools/call",
    "global-access": "tools/call",
}


def mcp_baseline(
    url: str,
    *,
    tools: Sequence[Mapping[str, Any]],
    call_tool: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
    server_protocol_version: str = "2025-06-18",
) -> httpx.MockTransport:
    """Build a stateless offline MCP baseline for :func:`simulate_mcp`.

    ``tools`` supplies JSON-serializable MCP descriptors; ``call_tool`` returns
    a JSON-serializable CallToolResult for a known name and its arguments. The
    synchronous handler runs in the caller's thread; exceptions propagate.
    Descriptors are copied when constructing the baseline.

    ``server_protocol_version`` is the initialize response's version, not an
    override of the client's request. The native MCP SDK accepts or rejects it;
    even unsupported strings are allowed for negative tests. Captures are not
    translated. No schema validation, policy enforcement, session state, SSE,
    resources, prompts or network fallback is provided.

    The transport accepts only the exact ``url`` and remains caller-owned.
    See https://docs.donkey-kit.dev/simulator for an example.
    """
    if not isinstance(server_protocol_version, str) or not server_protocol_version.strip():
        raise ValueError("server_protocol_version must be a non-empty version string")
    endpoint = httpx.URL(url)
    descriptors = copy.deepcopy([dict(tool) for tool in tools])
    names = set()
    for tool in descriptors:
        name = tool.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Baseline tools require unique non-empty names")
        if not isinstance(tool.get("inputSchema"), dict):
            raise ValueError("Baseline tools require an inputSchema object")
        names.add(name)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url != endpoint:
            raise ValueError("MCP baseline request escaped its endpoint")
        headers = {"x-donkey-simulator": "true"}
        if request.method != "POST":
            return httpx.Response(405, headers={**headers, "allow": "POST"})
        request_id = None

        def error(code: int, message: str) -> httpx.Response:
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": code, "message": message},
                },
            )

        try:
            rpc = json.loads(request.content)
        except (ValueError, UnicodeDecodeError):
            return error(-32700, "Parse error")
        if not isinstance(rpc, dict) or rpc.get("jsonrpc") != "2.0":
            return error(-32600, "Invalid Request")
        method = rpc.get("method")
        if not isinstance(method, str):
            return error(-32600, "Invalid Request")
        if "id" not in rpc and method.startswith("notifications/"):
            return httpx.Response(202, headers=headers)
        if type(rpc.get("id")) not in (str, int):
            return error(-32600, "Invalid Request")
        request_id = rpc["id"]
        if "params" in rpc and not isinstance(rpc["params"], dict):
            return error(-32602, "Invalid params")
        result: Mapping[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": server_protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "offline", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": descriptors}
        elif method == "tools/call":
            params = rpc.get("params")
            if not isinstance(params, dict):
                return error(-32602, "Invalid params")
            name, arguments = params.get("name"), params.get("arguments", {})
            if not isinstance(name, str) or name not in names or not isinstance(arguments, dict):
                return error(-32602, "Invalid params")
            result = call_tool(name, arguments)
        elif method == "ping":
            result = {}
        elif method == "discover":
            # MCP 2.3 falls back to initialize on this HTTP response.
            return httpx.Response(404, headers=headers)
        else:
            return error(-32601, "Method not found")
        return httpx.Response(
            200, headers=headers, json={"jsonrpc": "2.0", "id": request_id, "result": result}
        )

    return httpx.MockTransport(respond)


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

    Bind/connect inside the context. LangGraph, LlamaIndex, Anthropic, Agent
    Framework, OpenAI Agents SDK and Strands bindings are supported. Agent
    Framework and OpenAI Agents SDK need
    stable mcp>=2.3.0 plus agent-framework-core>=1.24.0 or openai-agents>=0.20.0,
    respectively. Strands needs stable strands-agents>=1.64.0 and mcp>=2.3.0.
    These floors apply only to simulation. Strands returns generic error tool
    results, not typed refusals; catalog errors raise MCPError. Its stop after
    failed/cancelled/expired sessions can warn about an unawaited coroutine,
    even though connection clients close. ADK and CrewAI are unsupported and
    fail before connecting (docs/verified-apis.md §9, #1643).
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


def _default_result(name: str, _arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    return {"content": [{"type": "text", "text": f"simulated result of {name}"}]}


def _declared(
    servers: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[tuple[McpServerHandle, list[dict[str, Any]]]]:
    if not isinstance(servers, Mapping) or not servers:
        raise ValueError(
            "servers must map at least one 'group_id/asset_id/version' ref to its tools"
        )
    declared = []
    for ref, tools in servers.items():
        asset = AssetRef.parse(ref)
        if isinstance(tools, (str, bytes)) or not isinstance(tools, Sequence) or not tools:
            raise ValueError(f"servers[{ref!r}] must be a non-empty list of tool descriptors")
        descriptors = []
        for tool in tools:
            if not isinstance(tool, Mapping):
                raise ValueError(f"servers[{ref!r}] tools must be MCP descriptor mappings")
            descriptor = copy.deepcopy(dict(tool))
            descriptor.setdefault("inputSchema", dict(_EMPTY_SCHEMA))
            descriptors.append(descriptor)
        handle = McpServerHandle(
            ref=asset,
            endpoint_url=f"{_DECLARED_HOST}/{asset.group_id}/{asset.asset_id}/{asset.version}/mcp",
            governance=GovernanceReport(
                governed=True,
                checks=[Check("simulated", True, "declared in donkey.simulate_mcp()")],
            ),
        )
        declared.append((handle, descriptors))
    return declared


@contextlib.contextmanager
def simulate_declared(
    donkey: Donkey,
    capture: str,
    servers: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    times: int = 1,
    call_tool: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> Iterator[None]:
    """The engine behind ``Donkey.simulate_mcp()`` (#1391, ADR 0023).

    ``donkey.tools.discover()`` (and the resolve behind ``live_tools()``) answer from the declared
    ``servers`` (marked governed by a ``simulated`` check) instead of Exchange,
    and every set they return binds through one :class:`McpReplay` whose
    baseline serves the declared tools. ``call_tool`` answers an unmatched
    ``tools/call``; the default returns one text item. After exit those sets
    fail closed: their clients refuse every request.
    """
    if capture not in _METHODS:
        raise ValueError(f"Unknown MCP capture {capture!r}; choose from {', '.join(_METHODS)}")
    if type(times) is not int or times < 1:
        raise ValueError("times must be a positive integer")
    declared = _declared(servers)
    answer = call_tool or _default_result
    baselines = {
        handle.endpoint_url: mcp_baseline(handle.endpoint_url, tools=tools, call_tool=answer)
        for handle, tools in declared
    }

    def route(request: httpx.Request) -> httpx.Response:
        baseline = baselines.get(str(request.url))
        if baseline is None:
            raise ValueError("MCP baseline request escaped the declared servers")
        return baseline.handle_request(request)

    handles = [handle for handle, _ in declared]
    replay = McpReplay(ToolSet(handles), capture, httpx.MockTransport(route), times)
    try:
        with mcp_discovery_scope(
            donkey,
            handles,
            auth=ClientIdAuth("simulated", "simulated"),
            transport={"factory": replay.client},
        ):
            yield
    finally:
        replay.close()
