#!/usr/bin/env python3
"""CrewAI probe: ``crewai_tools.MCPServerAdapter`` against the local reference server.

Binds ``MCPServerAdapter({"url": ..., "transport": "streamable-http", "headers": ...})``
(``mcpadapt`` builds the streamable-HTTP transport from that dict), lists the tools,
asserts they are CrewAI ``BaseTool`` objects, calls ``add`` through ``BaseTool.run``
(no model turn) and confirms from the server's record that ``client_id`` and
``client_secret`` arrived. Also checks that the ``*tool_names`` allow-list leaves out
the other tool.

Usage (throwaway venv with ``crewai``, ``crewai-tools[mcp]`` and ``mcp<2``), from python/:
    python scripts/mcp_binding/probe_crewai.py [--write]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any

from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from crewai.tools import BaseTool
from crewai_tools import MCPServerAdapter

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}


def params(srv: LocalServer) -> dict[str, Any]:
    return {"url": srv.url, "transport": "streamable-http", "headers": HEADERS}


def run(srv: LocalServer) -> dict[str, Any]:
    """List the tools and call ``add`` through CrewAI's own MCP binding."""
    with MCPServerAdapter(params(srv)) as tools:
        listed = sorted(t.name for t in tools)
        native = sorted({f"{type(t).__module__}.{type(t).__qualname__}" for t in tools})
        assert all(isinstance(t, BaseTool) for t in tools), native
        add = next(t for t in tools if t.name == "add")
        text = str(add.run(a=2, b=3))
    with MCPServerAdapter(params(srv), "add") as only:
        allowed = sorted(t.name for t in only)
    return {"listed": listed, "native": native, "text": text, "allowed": allowed}


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = run(srv)
        seen = headers_seen(srv.record)
    assert out["listed"] == ["add", "lookup_sku"], out["listed"]
    assert out["allowed"] == ["add"], out["allowed"]
    assert out["text"] == "5", out["text"]
    assert seen == HEADERS, seen
    fixture = {
        "framework": "crewai",
        "distributions": distributions("crewai", "crewai-tools", "mcpadapt"),
        "binding": {
            "class": "MCPServerAdapter",
            "module": "crewai_tools",
            "call": (
                'with MCPServerAdapter({"url": url, "transport": "streamable-http", '
                '"headers": {...}}, *tool_names) as tools: add.run(a=2, b=3)'
            ),
        },
        "url_shape": "http://127.0.0.1:<port>/mcp/",
        "tools_listed": out["listed"],
        "tool_names_allow_list": {"passed": ["add"], "listed": out["allowed"]},
        "native_tool_types": out["native"],
        "tool_call": {"name": "add", "args": {"a": 2, "b": 3}, "result": out["text"]},
        "headers_seen": {k: seen[k] for k in ("client_id", "client_secret")},
        "date": dt.date.today().isoformat(),
    }
    print(json.dumps(fixture, indent=2, sort_keys=True))
    if args.write:
        print("wrote", write_fixture("crewai", fixture))


if __name__ == "__main__":
    main()
