#!/usr/bin/env python3
"""How each framework's MCP class surfaces the gateway's tool-access denial (#312).

Starts the local server with ``--deny add``, so ``tools/call`` of ``add`` gets the
gateway's captured refusal (HTTP 200, JSON-RPC ``-32008`` "Unable to access",
ledger §9, #303), then calls ``add`` through each framework installed in this
interpreter, the way that framework calls a tool. Records, per framework, whether
the call raised (the exception chain's types, and the JSON-RPC ``code`` where an
``McpError`` / ``MCPError`` carries one) or returned (the value's type and text).

Usage (throwaway venv with the frameworks and mcp installed), from python/:
    python scripts/mcp_binding/probe_denial.py [--write] [--only NAME ...]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib.util
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from _harness import distributions, local_mcp_server, write_fixture

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}
ARGS = {"a": 2, "b": 3}


def _chain(exc: BaseException) -> list[dict[str, Any]]:
    """Each exception in ``exc``'s group/cause chain: type, message, JSON-RPC code."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    stack = [exc]
    while stack:
        cur = stack.pop(0)
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        error = getattr(cur, "error", None)
        entry = {
            "type": f"{type(cur).__module__}.{type(cur).__qualname__}",
            "message": str(cur)[:300],
            "code": getattr(error, "code", None),
            "data": getattr(error, "data", None),
        }
        status = getattr(getattr(cur, "response", None), "status_code", None)
        if isinstance(status, int):  # an HTTP error response rode along (#214)
            entry["http_status"] = status
        out.append(entry)
        stack.extend(getattr(cur, "exceptions", ()) or ())
        for nxt in (cur.__cause__, cur.__context__):
            if nxt is not None:
                stack.append(nxt)
    return out


def _value(value: Any) -> dict[str, Any]:
    return {
        "returned": f"{type(value).__module__}.{type(value).__qualname__}",
        "text": repr(value)[:400],
    }


async def _observe(call: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    try:
        return _value(await call())
    except BaseException as exc:  # noqa: BLE001 - recording whatever the framework does
        return {"raised": _chain(exc)}


async def mcp_sdk(url: str) -> dict[str, Any]:
    import mcp.client.streamable_http as streamable
    from mcp import ClientSession

    async def call() -> Any:
        http_lib = getattr(streamable, "httpx2", None) or streamable.httpx
        async with (
            http_lib.AsyncClient(headers=HEADERS) as http,
            streamable.streamable_http_client(url, http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            return await session.call_tool("add", ARGS)

    return await _observe(call)


async def langgraph(url: str) -> dict[str, Any]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {"local": {"transport": "streamable_http", "url": url, "headers": dict(HEADERS)}}
    )
    tools = {t.name: t for t in await client.get_tools()}
    tool = tools["add"]
    out = {"coroutine": await _observe(lambda: tool.coroutine(**ARGS))}
    out["ainvoke"] = await _observe(lambda: tool.ainvoke(ARGS))
    # The ToolNode path: a tool call, as the agent loop sends it.
    call = {"name": "add", "args": ARGS, "id": "call-1", "type": "tool_call"}
    out["ainvoke_tool_call"] = await _observe(lambda: tool.ainvoke(call))
    return out


async def llamaindex(url: str) -> dict[str, Any]:
    from llama_index.tools.mcp import BasicMCPClient, McpToolSpec

    spec = McpToolSpec(BasicMCPClient(url, headers=dict(HEADERS)), allowed_tools=["add"])
    tool = (await spec.to_tool_list_async())[0]
    return {
        "async_fn": await _observe(lambda: tool.async_fn(**ARGS)),
        "acall": await _observe(lambda: tool.acall(**ARGS)),
    }


async def anthropic(url: str) -> dict[str, Any]:
    import mcp.client.streamable_http as streamable
    from anthropic.lib.tools.mcp import async_mcp_tool
    from mcp import ClientSession

    http_lib = getattr(streamable, "httpx2", None) or streamable.httpx
    async with (
        http_lib.AsyncClient(headers=HEADERS) as http,
        streamable.streamable_http_client(url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        listed = {t.name: t for t in (await session.list_tools()).tools}
        tool = async_mcp_tool(listed["add"], session)
        return {
            "func": await _observe(lambda: tool.func(**ARGS)),
            "call": await _observe(lambda: tool.call(ARGS)),
        }


async def adk(url: str) -> dict[str, Any]:
    from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams

    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=url, headers=dict(HEADERS)),
        tool_filter=["add"],
    )
    try:
        tool = (await toolset.get_tools())[0]

        async def call() -> Any:
            from google.adk.tools.tool_context import ToolContext

            ctx: Any = object.__new__(ToolContext)
            return await tool.run_async(args=ARGS, tool_context=ctx)

        return {"run_async": await _observe(call)}
    finally:
        await toolset.close()


async def strands(url: str) -> dict[str, Any]:
    from strands.tools.mcp import MCPClient

    def run() -> Any:
        client = MCPClient(url=url, headers=dict(HEADERS))
        with client:
            return client.call_tool_sync("probe-1", "add", ARGS)

    return {"call_tool_sync": await _observe(lambda: asyncio.to_thread(run))}


async def openai_agents(url: str) -> dict[str, Any]:
    from agents.mcp import MCPServerStreamableHttp

    async def call() -> Any:
        async with MCPServerStreamableHttp(
            params={"url": url, "headers": dict(HEADERS)}, name="local"
        ) as server:
            return await server.call_tool("add", ARGS)

    out = {"call_tool": await _observe(call)}

    async def via_util() -> Any:
        from agents.mcp.util import MCPUtil

        async with MCPServerStreamableHttp(
            params={"url": url, "headers": dict(HEADERS)}, name="local"
        ) as server:
            tools = await server.list_tools()
            add = next(t for t in tools if t.name == "add")
            import json

            return await MCPUtil.invoke_mcp_tool(server, add, None, json.dumps(ARGS))

    out["invoke_mcp_tool"] = await _observe(via_util)
    return out


async def agent_framework(url: str) -> dict[str, Any]:
    from agent_framework import MCPStreamableHTTPTool

    async def call() -> Any:
        async with MCPStreamableHTTPTool(
            "local", url, static_headers=dict(HEADERS), allowed_tools=["add"]
        ) as tool:
            return await tool.call_tool("add", **ARGS)

    return {"call_tool": await _observe(call)}


async def crewai(url: str) -> dict[str, Any]:
    def run() -> Any:
        from crewai_tools import MCPServerAdapter

        params = {"url": url, "transport": "streamable-http", "headers": dict(HEADERS)}
        adapter = MCPServerAdapter(params, "add")
        try:
            return adapter.tools[0].run(**ARGS)
        finally:
            adapter.stop()

    return {"run": await _observe(lambda: asyncio.to_thread(run))}


PROBES: dict[str, tuple[str, Callable[[str], Awaitable[dict[str, Any]]], tuple[str, ...]]] = {
    "mcp_sdk": ("mcp", mcp_sdk, ()),
    "langgraph": ("langchain_mcp_adapters", langgraph, ("langchain-mcp-adapters",)),
    "llamaindex": ("llama_index.tools.mcp", llamaindex, ("llama-index-tools-mcp",)),
    "anthropic": ("anthropic", anthropic, ("anthropic",)),
    "adk": ("google.adk", adk, ("google-adk",)),
    "strands": ("strands", strands, ("strands-agents",)),
    "openai_agents": ("agents", openai_agents, ("openai-agents",)),
    "agent_framework": ("agent_framework", agent_framework, ("agent-framework",)),
    "crewai": ("crewai_tools", crewai, ("crewai-tools",)),
}


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def main() -> None:
    """Run every installed framework's probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--name", default="denial", help="fixture name (denial-<name>.json)")
    args = parser.parse_args()
    names = [n for n in PROBES if (args.only is None or n in args.only)]
    observed: dict[str, Any] = {}
    dists: tuple[str, ...] = ()
    with local_mcp_server(deny="add") as srv:
        for name in names:
            module, probe, needs = PROBES[name]
            if not _installed(module):
                continue
            observed[name] = asyncio.run(probe(srv.url))
            dists += needs
    fixture = {
        "probe": "probe_denial.py",
        "recorded": dt.date.today().isoformat(),
        "distributions": distributions(*dists),
        "python": sys.version.split()[0],
        "denial": "HTTP 200, JSON-RPC -32008 'Unable to access' (ledger §9, #303)",
        "observed": observed,
    }
    import json

    print(json.dumps(fixture, indent=2, sort_keys=True, default=repr))
    if args.write:
        print(write_fixture(f"denial-{args.name}", fixture), file=sys.stderr)


if __name__ == "__main__":
    main()
