#!/usr/bin/env python3
"""LlamaIndex probe: ``BasicMCPClient`` + ``McpToolSpec`` against the local server.

Binds ``llama_index.tools.mcp.BasicMCPClient`` to the reference server with the
Client ID Enforcement headers, lists the tools through ``McpToolSpec`` and calls
``add`` through the resulting ``FunctionTool`` (``acall``), not a raw MCP session.

Usage (throwaway venv with ``llama-index-tools-mcp`` and ``mcp``), from python/:
    python scripts/mcp_binding/probe_llamaindex.py [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from typing import Any

from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from llama_index.core.tools import BaseTool
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}


def qualified(obj: Any) -> str:
    """Return ``module.QualName`` of an object's type."""
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


async def bind(url: str) -> tuple[list[Any], Any]:
    """List tools via ``McpToolSpec`` and call ``add`` through the FunctionTool."""
    client = BasicMCPClient(url, headers=HEADERS)
    tools = await McpToolSpec(client).to_tool_list_async()
    by_name = {t.metadata.name: t for t in tools}
    out = await by_name["add"].acall(a=2, b=3)
    return tools, out


async def run(srv: LocalServer) -> dict[str, Any]:
    """Run the trailing-slash round trip and the informational no-slash try."""
    tools, out = await bind(srv.url)
    assert all(isinstance(t, BaseTool) for t in tools), [qualified(t) for t in tools]
    no_slash: dict[str, Any]
    try:
        bare, _ = await bind(srv.url.rstrip("/"))
        no_slash = {"works": True, "tools": sorted(t.metadata.name for t in bare)}
    except Exception as exc:  # noqa: BLE001 - informational only, record whatever failed
        no_slash = {"works": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {
        "listed": sorted(t.metadata.name for t in tools),
        "native": sorted({qualified(t) for t in tools}),
        "result": out.raw_output.content[0].text,
        "raw_output_type": qualified(out.raw_output),
        "tool_output_content": out.content,
        "no_slash": no_slash,
    }


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = asyncio.run(run(srv))
        seen = headers_seen(srv.record)
    assert out["listed"] == ["add", "lookup_sku"], out["listed"]
    assert out["result"] == "5", out["result"]
    assert seen == {"client_id": "probe-id", "client_secret": "probe-secret"}, seen
    print("no-trailing-slash (informational):", json.dumps(out["no_slash"]))
    print("ToolOutput.raw_output type:", out["raw_output_type"])
    print("ToolOutput.content:", out["tool_output_content"])
    fixture = {
        "framework": "llamaindex",
        "distributions": distributions("llama-index-tools-mcp", "llama-index-core"),
        "binding": {
            "class": "BasicMCPClient + McpToolSpec",
            "module": "llama_index.tools.mcp",
            "call": (
                "McpToolSpec(BasicMCPClient(url, headers=...)).to_tool_list_async() "
                "-> FunctionTool.acall(a=2, b=3)"
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
    if args.write:
        print("wrote", write_fixture("llamaindex", fixture))


if __name__ == "__main__":
    main()
