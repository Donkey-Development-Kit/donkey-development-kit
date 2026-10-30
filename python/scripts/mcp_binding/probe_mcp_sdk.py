#!/usr/bin/env python3
"""Baseline probe: the official ``mcp`` client against the local reference server.

Connects with ``mcp.client.streamable_http`` + ``ClientSession``, lists the tools
and calls ``add``. Proves the harness itself before any framework binds to it.

Usage (throwaway venv with ``mcp`` installed), from python/:
    python scripts/mcp_binding/probe_mcp_sdk.py [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from typing import Any

import mcp.client.streamable_http as streamable
from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from mcp import ClientSession

FAKE_HEADERS = {
    "client_id": "fake-client-id",
    "client_secret": "fake-client-secret",
    "x-correlation-id": "fake-correlation-id",
}
# mcp 2.x types its http_client as httpx2.AsyncClient; mcp 1.x uses httpx.
HTTPX_MODULE = "httpx2" if hasattr(streamable, "httpx2") else "httpx"


async def run(srv: LocalServer) -> dict[str, Any]:
    """List the tools and call ``add`` through a real MCP client session."""
    http_lib = getattr(streamable, HTTPX_MODULE)
    async with (
        http_lib.AsyncClient(headers=FAKE_HEADERS) as http,
        streamable.streamable_http_client(srv.url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        result = await session.call_tool("add", {"a": 2, "b": 3})
    first = result.content[0]
    return {
        "listed": sorted(t.name for t in listed.tools),
        "native": sorted({type(t).__name__ for t in listed.tools}),
        "text": getattr(first, "text", None),
    }


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = asyncio.run(run(srv))
        seen = headers_seen(srv.record)
    fixture = {
        "framework": "mcp_sdk",
        "distributions": distributions(HTTPX_MODULE),
        "binding": {
            "class": "ClientSession",
            "module": "mcp",
            "call": (
                "streamable_http_client(url, http_client=AsyncClient(headers=...)) "
                "-> ClientSession(read, write).initialize()/list_tools()/call_tool()"
            ),
        },
        "url_shape": "http://127.0.0.1:<port>/mcp/",
        "tools_listed": out["listed"],
        "native_tool_types": out["native"],
        "tool_call": {"name": "add", "args": {"a": 2, "b": 3}, "result": out["text"]},
        "headers_seen": {k: seen[k] for k in ("client_id", "client_secret")},
        "date": dt.date.today().isoformat(),
    }
    print(json.dumps(fixture, indent=2, sort_keys=True))
    if args.write:
        print("wrote", write_fixture("mcp_sdk", fixture))


if __name__ == "__main__":
    main()
