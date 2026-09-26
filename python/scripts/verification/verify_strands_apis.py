#!/usr/bin/env python3
"""Offline installed-package evidence for docs/verified-apis.md §§9–10.

This probe does not implement DDK's guarded MCP/descriptor surfaces or establish
remote transport behavior. Run from python/ with this framework and its MCP
integration installed. Any assertion/import error is a failed verification.
"""

from __future__ import annotations

import inspect
import json
import sys
from importlib.metadata import version


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError(f"Offline probe attempted network access: {event}")


def describe(symbol: object) -> dict[str, str]:
    return {
        "symbol": f"{symbol.__module__}.{symbol.__name__}",
        "signature": str(inspect.signature(symbol)),
    }


def lookup(city: str) -> str:
    """Look up a city."""
    return city


def main() -> None:
    from mcp.client.streamable_http import streamablehttp_client
    from strands import tool as make_tool
    from strands.tools.mcp import MCPClient

    inspect.signature(streamablehttp_client).bind(url=URL, headers=HEADERS)
    binding = MCPClient(lambda: streamablehttp_client(url=URL, headers=HEADERS))
    assert isinstance(binding, MCPClient)
    tool = make_tool(lookup)
    spec = tool.tool_spec
    assert spec["name"] == "lookup" and spec["description"] == "Look up a city."
    schema = spec["inputSchema"]["json"]
    assert schema["properties"]["city"]["type"] == "string"
    assert "city" in schema["required"]
    emit(
        ["strands-agents", "mcp", "openai"],
        [describe(MCPClient), describe(streamablehttp_client)],
        {"transport_callable": "lambda: streamablehttp_client(url=URL, headers=HEADERS)"},
        spec,
    )


URL = "https://placeholder.invalid/mcp"
HEADERS = {"x-probe": "offline"}


def emit(
    packages: list[str],
    symbols: list[dict[str, str]],
    binding: dict[str, object],
    descriptor: dict[str, object],
) -> None:
    print(
        json.dumps(
            {
                "python": sys.version,
                "versions": {package: version(package) for package in packages},
                "binding_symbols": symbols,
                "binding": binding,
                "descriptor": descriptor,
                "network": "denied by audit hook; no gateway or live MCP verification",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    sys.addaudithook(deny_network)
    main()
