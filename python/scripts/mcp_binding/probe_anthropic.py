#!/usr/bin/env python3
"""Anthropic SDK probe: ``anthropic.lib.tools.mcp`` against the local reference server.

The client-side helper ``async_mcp_tool(tool, session)`` turns an ``mcp``
``ClientSession`` tool into a ``BetaAsyncFunctionTool``; the transport and its
headers still come from the ``mcp`` client. The Messages API ``mcp_servers``
connector is server-side (Anthropic's servers dial the URL), so it cannot reach
127.0.0.1 and is only inspected statically here (its TypedDict has no header
field beyond ``authorization_token``).

Usage (throwaway venv with ``anthropic`` and ``mcp``), from python/:
    python scripts/mcp_binding/probe_anthropic.py [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import typing
from typing import Any

import anyio.to_thread
import mcp.client.streamable_http as streamable
from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from anthropic.lib.tools.mcp import async_mcp_tool, mcp_tool
from anthropic.types.beta import BetaRequestMCPServerURLDefinitionParam
from mcp import ClientSession

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}
# mcp 2.x types its http_client as httpx2.AsyncClient; mcp 1.x uses httpx.
HTTPX_MODULE = "httpx2" if hasattr(streamable, "httpx2") else "httpx"


def qualname(obj: object) -> str:
    """Return ``module.qualname`` of an object's type."""
    return f"{type(obj).__module__}.{type(obj).__qualname__}"


async def run(srv: LocalServer) -> dict[str, Any]:
    """Bind via ``async_mcp_tool``, list the tools and call ``add`` through them."""
    http_lib = getattr(streamable, HTTPX_MODULE)
    async with (
        http_lib.AsyncClient(headers=HEADERS) as http,
        streamable.streamable_http_client(srv.url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        tools = [async_mcp_tool(t, session) for t in listed.tools]
        by_name = {t.name: t for t in tools}
        result = await by_name["add"].call({"a": 2, "b": 3})
        wire = by_name["add"].to_dict()
        # The sync twin needs a worker thread (it uses anyio.from_thread.run).
        add_def = next(t for t in listed.tools if t.name == "add")
        sync_tool = mcp_tool(add_def, session)
        sync_result = await anyio.to_thread.run_sync(sync_tool.call, {"a": 2, "b": 3})
    return {
        "names": sorted(by_name),
        "native": sorted({qualname(t) for t in tools}),
        "result": result,
        "wire_keys": sorted(wire),
        "sync_type": qualname(sync_tool),
        "sync_result": sync_result,
    }


async def probe_no_slash(srv: LocalServer) -> str:
    """Informational: does the slash-less URL work against this server?"""
    http_lib = getattr(streamable, HTTPX_MODULE)
    url = srv.url.rstrip("/")
    try:
        async with (
            http_lib.AsyncClient(headers=HEADERS) as http,
            streamable.streamable_http_client(url, http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            n = len((await session.list_tools()).tools)
    except Exception as exc:  # noqa: BLE001 - informational only
        return f"failed: {type(exc).__name__}"
    return f"worked ({n} tools, redirects followed)"


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = asyncio.run(run(srv))
        seen = headers_seen(srv.record)
        no_slash = asyncio.run(probe_no_slash(srv))

    assert out["names"] == ["add", "lookup_sku"], out["names"]
    native = "anthropic.lib.tools._beta_functions.BetaAsyncFunctionTool"
    assert out["native"] == [native], out["native"]
    assert seen == HEADERS, seen
    connector_fields = sorted(typing.get_type_hints(BetaRequestMCPServerURLDefinitionParam))
    print("mcp_servers connector fields:", connector_fields)
    print("sync mcp_tool type:", out["sync_type"], "result:", out["sync_result"])
    print("wire keys:", out["wire_keys"])
    print("no-trailing-slash URL:", no_slash)

    fixture = {
        "framework": "anthropic",
        "distributions": distributions("anthropic", HTTPX_MODULE),
        "binding": {
            "class": "BetaAsyncFunctionTool",
            "module": "anthropic.lib.tools.mcp",
            "call": (
                "async_mcp_tool(tool, session) for tool in (await session.list_tools()).tools, "
                "session = mcp ClientSession over streamable_http_client(url, http_client="
                "AsyncClient(headers=...)); then await tool.call(args)"
            ),
        },
        "url_shape": "http://127.0.0.1:<port>/mcp/",
        "tools_listed": out["names"],
        "native_tool_types": out["native"],
        "tool_call": {"name": "add", "args": {"a": 2, "b": 3}, "result": out["result"]},
        "headers_seen": {k: seen[k] for k in ("client_id", "client_secret")},
        "date": dt.date.today().isoformat(),
    }
    print(json.dumps(fixture, indent=2, sort_keys=True))
    if args.write:
        print("wrote", write_fixture("anthropic", fixture))


if __name__ == "__main__":
    main()
