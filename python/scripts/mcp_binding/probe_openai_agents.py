#!/usr/bin/env python3
"""OpenAI Agents SDK probe: ``agents.mcp.MCPServerStreamableHttp`` against the local server.

Binds the server to an ``Agent(mcp_servers=[...])``, lists the tools through
``Agent.get_all_tools`` (the framework path), checks they are ``agents.FunctionTool``,
calls ``add`` through ``FunctionTool.on_invoke_tool`` and confirms from the record
file that the server saw both Client ID Enforcement headers.

Usage (throwaway venv with ``openai-agents`` and ``mcp``), from python/:
    python scripts/mcp_binding/probe_openai_agents.py [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from typing import Any

from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from agents import Agent, FunctionTool, RunContextWrapper
from agents.mcp import MCPServerStreamableHttp
from agents.tool_context import ToolContext

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}
ARGS = {"a": 2, "b": 3}


def bind(url: str) -> MCPServerStreamableHttp:
    """The framework binding pointed at ``url`` with the gateway's enforcement headers."""
    return MCPServerStreamableHttp(params={"url": url, "headers": dict(HEADERS)})


async def run(srv: LocalServer) -> dict[str, Any]:
    """List tools via the Agent and call ``add`` through ``FunctionTool.on_invoke_tool``."""
    async with bind(srv.url) as server:
        agent = Agent(name="probe", mcp_servers=[server])
        tools = await agent.get_all_tools(RunContextWrapper(None))
        assert tools, "no tools listed"
        for tool in tools:
            assert isinstance(tool, FunctionTool), type(tool)
        by_name = {t.name: t for t in tools if isinstance(t, FunctionTool)}
        payload = json.dumps(ARGS)
        ctx = ToolContext(
            context=None, tool_name="add", tool_call_id="probe-call", tool_arguments=payload
        )
        result = await by_name["add"].on_invoke_tool(ctx, payload)
    return {
        "listed": sorted(by_name),
        "native": sorted({f"{type(t).__module__}.{type(t).__qualname__}" for t in tools}),
        "result": result,
    }


async def slashless(srv: LocalServer) -> str:
    """Informational: does the URL without the trailing slash work against this server?"""
    try:
        async with bind(srv.url.rstrip("/")) as server:
            tools = await server.list_tools()
        return "ok: " + ",".join(sorted(t.name for t in tools))
    except Exception as exc:  # noqa: BLE001 - informational only; record whatever happened
        return "; ".join(f"{type(e).__name__}: {str(e)[:200]}" for e in leaves(exc))


def leaves(exc: BaseException) -> list[BaseException]:
    """The innermost exceptions of a (possibly nested) ExceptionGroup."""
    subs: tuple[BaseException, ...] = getattr(exc, "exceptions", ())
    if subs:
        return [leaf for sub in subs for leaf in leaves(sub)]
    return [exc]


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = asyncio.run(run(srv))
        seen = headers_seen(srv.record)
        no_slash = asyncio.run(slashless(srv))
    assert out["listed"] == ["add", "lookup_sku"], out["listed"]
    assert out["native"] == ["agents.tool.FunctionTool"], out["native"]
    assert out["result"] == {"type": "text", "text": "5"}, out["result"]
    assert seen == HEADERS, seen
    fixture = {
        "framework": "openai_agents",
        "distributions": distributions("openai-agents"),
        "binding": {
            "class": "MCPServerStreamableHttp",
            "module": "agents.mcp",
            "call": (
                "async with MCPServerStreamableHttp(params={'url': url, 'headers': {...}}) "
                "as server: Agent(name=..., mcp_servers=[server]).get_all_tools("
                "RunContextWrapper(None)) -> FunctionTool.on_invoke_tool(ToolContext, json)"
            ),
        },
        "url_shape": "http://127.0.0.1:<port>/mcp/",
        "tools_listed": out["listed"],
        "native_tool_types": out["native"],
        "tool_call": {"name": "add", "args": ARGS, "result": out["result"]},
        "headers_seen": {k: seen[k] for k in ("client_id", "client_secret")},
        "date": dt.date.today().isoformat(),
    }
    print(json.dumps(fixture, indent=2, sort_keys=True))
    print("without trailing slash (informational):", no_slash)
    if args.write:
        print("wrote", write_fixture("openai_agents", fixture))


if __name__ == "__main__":
    main()
