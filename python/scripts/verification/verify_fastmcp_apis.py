#!/usr/bin/env python3
"""Offline §10: distinguish FastMCP native schemas from MCP protocol schemas.

Exercises both mcp.server.fastmcp (distribution mcp) and standalone fastmcp.
No server is started and no MCP session or tool invocation occurs.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from datetime import date
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Any


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError(f"Offline probe attempted network access: {event}")


def describe(symbol: Any, package: str) -> dict[str, str]:
    source = Path(inspect.getfile(symbol)).resolve()
    dist = distribution(package)
    assert any(Path(dist.locate_file(f)).resolve() == source for f in dist.files or ())
    return {
        "class": f"{symbol.__module__}.{symbol.__name__}",
        "owning_distribution": f"{package}=={dist.version}",
    }


def lookup(city: str) -> str:
    """Look up a city."""
    return city


def assert_descriptor(native: Any, protocol: Any) -> dict[str, Any]:
    from mcp.types import Tool

    assert isinstance(native.name, str) and native.name == "lookup"
    assert isinstance(native.description, str) and native.description == "Look up a city."
    assert isinstance(native.parameters, dict)
    assert native.parameters["type"] == "object"
    assert native.parameters["properties"]["city"]["type"] == "string"
    assert "city" in native.parameters["required"]
    has_protocol_attribute = hasattr(native, "inputSchema")
    assert not has_protocol_attribute, "Native inputSchema now exists; revisit the correction"
    assert isinstance(protocol, Tool)
    assert protocol.name == native.name and protocol.description == native.description
    assert isinstance(protocol.inputSchema, dict) and protocol.inputSchema == native.parameters
    return {
        "name": native.name,
        "description": native.description,
        "native.parameters": native.parameters,
        "native.has_inputSchema": has_protocol_attribute,
        "protocol.inputSchema": protocol.inputSchema,
        "protocol_symbol": describe(Tool, "mcp"),
    }


async def check_descriptors() -> list[dict[str, Any]]:
    from fastmcp import FastMCP as StandaloneFastMCP
    from fastmcp.tools import FunctionTool
    from mcp.server.fastmcp import FastMCP as SDKFastMCP
    from mcp.server.fastmcp.tools.base import Tool as SDKTool

    sdk_native = SDKTool.from_function(lookup)
    sdk_server = SDKFastMCP("offline-probe")
    sdk_server.add_tool(lookup)
    # This is an in-process server method, not the MCP client's tools/list RPC.
    (sdk_protocol,) = await sdk_server.list_tools()
    sdk = assert_descriptor(sdk_native, sdk_protocol)
    sdk.update(
        {
            "implementation": "MCP Python SDK FastMCP",
            "native_symbol": describe(SDKTool, "mcp"),
            "conversion": "mcp.server.fastmcp.FastMCP.list_tools() (in-process)",
        }
    )

    standalone_native = FunctionTool.from_function(lookup)
    standalone_server = StandaloneFastMCP("offline-probe")
    standalone_server.add_tool(standalone_native)
    (listed_native,) = await standalone_server.list_tools()
    assert isinstance(listed_native, FunctionTool)
    assert listed_native.parameters == standalone_native.parameters
    standalone = assert_descriptor(listed_native, listed_native.to_mcp_tool())
    standalone.update(
        {
            "implementation": "standalone FastMCP",
            "native_symbol": describe(FunctionTool, "fastmcp-slim"),
            "conversion": "fastmcp.tools.FunctionTool.to_mcp_tool()",
        }
    )
    return [sdk, standalone]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=["10"], default="10")
    parser.add_argument("--emit-verified", action="store_true")
    args = parser.parse_args()
    for key in tuple(os.environ):
        if key.lower().endswith("_proxy") or key.startswith(
            ("DONKEY_LLM_PROXY_", "ANYPOINT_", "MULESOFT_")
        ):
            os.environ.pop(key)
    sys.addaudithook(deny_network)
    results = asyncio.run(check_descriptors())
    versions = {p: version(p) for p in ["fastmcp", "fastmcp-slim", "mcp"]}
    today = date.today().isoformat()
    print(
        json.dumps(
            {
                "date": today,
                "python": sys.version,
                "section": 10,
                "status": "VERIFIED (corrected offline attributes)",
                "versions": versions,
                "network": "denied by audit hook",
                "checks": results,
            },
            indent=2,
        )
    )
    if args.emit_verified:
        pins = "; ".join(f"{p}=={v}" for p, v in versions.items())
        print("\n# Candidate §10 row; no live session or descriptor implementation verified.")
        print(
            "| FastMCP | Native tools: `.name`, `.description`, `.parameters`; "
            "protocol `mcp.types.Tool`: `.name`, `.description`, `.inputSchema` | "
            f"VERIFIED (corrected offline attributes) | {pins}; {today}; "
            "both FastMCP implementations probed; descriptor guard retained |"
        )


if __name__ == "__main__":
    main()
