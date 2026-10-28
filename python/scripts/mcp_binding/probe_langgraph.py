#!/usr/bin/env python3
"""LangGraph probe: ``langchain_mcp_adapters`` ``MultiServerMCPClient`` vs the local server.

Builds the client with a ``streamable_http`` connection carrying the gateway's
Client ID Enforcement headers, lists the tools through ``get_tools()`` (LangChain
``BaseTool`` objects) and calls ``add`` through the tool's own ``ainvoke``.

Usage (throwaway venv with langchain-mcp-adapters and mcp installed), from python/:
    python scripts/mcp_binding/probe_langgraph.py [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from typing import Any

from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}


def _client(url: str) -> MultiServerMCPClient:
    return MultiServerMCPClient(
        {"local": {"transport": "streamable_http", "url": url, "headers": dict(HEADERS)}}
    )


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON; drop the per-call random ``id`` of each content block.

    The adapter stamps every returned text block with ``lc_<uuid4>``, which would
    make the fixture differ on every run. Everything else is recorded as returned.
    """
    blocks = json.loads(json.dumps(value, default=repr))
    if isinstance(blocks, list):
        return [
            {k: v for k, v in b.items() if k != "id"} if isinstance(b, dict) else b for b in blocks
        ]
    return blocks


async def run(srv: LocalServer) -> dict[str, Any]:
    """List the tools and call ``add`` through the LangChain tool objects."""
    tools = await _client(srv.url).get_tools()
    assert all(isinstance(t, BaseTool) for t in tools), [type(t) for t in tools]
    by_name = {t.name: t for t in tools}
    result = await by_name["add"].ainvoke({"a": 2, "b": 3})
    return {
        "listed": sorted(by_name),
        "native": sorted({f"{type(t).__module__}.{type(t).__qualname__}" for t in tools}),
        "result": _jsonable(result),
    }


async def no_slash(srv: LocalServer) -> str:
    """Informational: does the URL without the trailing slash work on this server?"""
    try:
        tools = await _client(srv.url.rstrip("/")).get_tools()
    except BaseException as exc:  # noqa: BLE001 - informational, report whatever happened
        return f"failed: {type(exc).__name__}: {exc}"
    return f"ok: {sorted(t.name for t in tools)}"


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = asyncio.run(run(srv))
        seen = headers_seen(srv.record)
        slashless = asyncio.run(no_slash(srv))
    assert out["listed"] == ["add", "lookup_sku"], out["listed"]
    assert seen == {"client_id": "probe-id", "client_secret": "probe-secret"}, seen
    assert "5" in json.dumps(out["result"]), out["result"]
    fixture = {
        "framework": "langgraph",
        "distributions": distributions(
            "langchain-mcp-adapters", "langchain-core", "langgraph", "httpx"
        ),
        "binding": {
            "class": "MultiServerMCPClient",
            "module": "langchain_mcp_adapters.client",
            "call": (
                'MultiServerMCPClient({name: {"transport": "streamable_http", "url": url, '
                '"headers": {...}}}).get_tools() -> BaseTool.ainvoke({"a": 2, "b": 3})'
            ),
        },
        "url_shape": "http://127.0.0.1:<port>/mcp/",
        "tools_listed": out["listed"],
        "native_tool_types": out["native"],
        "tool_call": {"name": "add", "args": {"a": 2, "b": 3}, "result": out["result"]},
        "headers_seen": {k: seen[k] for k in ("client_id", "client_secret")},
        "date": dt.date.today().isoformat(),
    }
    print(json.dumps(fixture, indent=2, sort_keys=True))
    print("no-trailing-slash URL (informational):", slashless, file=sys.stderr)
    if args.write:
        print("wrote", write_fixture("langgraph", fixture))


if __name__ == "__main__":
    main()
