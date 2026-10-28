#!/usr/bin/env python3
"""Strands probe: ``strands.tools.mcp.MCPClient`` against the local reference server.

Binds ``MCPClient(url=..., headers=...)`` (strands-agents builds the streamable-HTTP
transport itself), lists the tools through ``list_tools_sync()``, asserts they are
Strands' own ``MCPAgentTool``, calls ``add`` through the Agent's direct tool-call path
(``agent.tool.add(...)``, no model turn) and confirms from the server's record that
``client_id`` and ``client_secret`` arrived. Also reports, informationally, whether the
same URL without the trailing slash works and whether the old ``streamablehttp_client``
name the hint used still exists.

Usage (throwaway venv with ``strands-agents`` and ``mcp``), from python/:
    python scripts/mcp_binding/probe_strands.py [--write]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any

from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from strands import Agent
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}


def run(srv: LocalServer) -> dict[str, Any]:
    """List the tools and call ``add`` through Strands' own MCP binding."""
    client = MCPClient(url=srv.url, headers=HEADERS)
    with client:
        tools = list(client.list_tools_sync())
        native = sorted({f"{type(t).__module__}.{type(t).__qualname__}" for t in tools})
        assert all(isinstance(t, MCPAgentTool) for t in tools), native
        agent = Agent(tools=tools, callback_handler=None)
        result = agent.tool.add(a=2, b=3)
    assert result["status"] == "success", result
    return {
        "listed": sorted(t.tool_name for t in tools),
        "native": native,
        "text": result["content"][0]["text"],
    }


def no_slash_outcome(srv: LocalServer) -> str:
    """Informational: does the same URL without the trailing slash list tools?"""
    try:
        with MCPClient(url=srv.url.rstrip("/"), headers=HEADERS) as client:
            names = sorted(t.tool_name for t in client.list_tools_sync())
    except Exception as exc:  # noqa: BLE001 - informational probe, any failure is the answer
        return f"failed: {type(exc).__name__}: {str(exc)[:200]}"
    return f"listed {names}"


def legacy_name_present() -> bool:
    """Informational: does ``mcp.client.streamable_http.streamablehttp_client`` exist?"""
    import mcp.client.streamable_http as streamable

    return hasattr(streamable, "streamablehttp_client")


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = run(srv)
        seen = headers_seen(srv.record)
        slash = no_slash_outcome(srv)
    assert out["listed"] == ["add", "lookup_sku"], out["listed"]
    assert out["text"] == "5", out["text"]
    assert seen == HEADERS, seen
    print(f"no-trailing-slash URL: {slash}", file=sys.stderr)
    print(f"legacy streamablehttp_client present: {legacy_name_present()}", file=sys.stderr)
    fixture = {
        "framework": "strands",
        "distributions": distributions("strands-agents"),
        "binding": {
            "class": "MCPClient",
            "module": "strands.tools.mcp",
            "call": (
                "with MCPClient(url=url, headers={...}) as c: tools = c.list_tools_sync(); "
                "Agent(tools=tools).tool.add(a=2, b=3)"
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
        print("wrote", write_fixture("strands", fixture))


if __name__ == "__main__":
    main()
