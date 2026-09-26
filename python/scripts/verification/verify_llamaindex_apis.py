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
    import asyncio

    from llama_index.core.tools import FunctionTool
    from llama_index.tools.mcp import BasicMCPClient, McpToolSpec

    client = BasicMCPClient(command_or_url=URL, headers=HEADERS)
    binding = McpToolSpec(client=client, allowed_tools=["lookup"])
    assert binding.client is client
    assert client.command_or_url == URL and client.headers == HEADERS
    tool = FunctionTool.from_defaults(fn=lookup)
    metadata = tool.metadata
    assert metadata.name == "lookup" and "Look up a city." in metadata.description
    schema = metadata.fn_schema.model_json_schema()
    assert schema["properties"]["city"]["type"] == "string"
    assert "city" in schema["required"]
    asyncio.run(client.http_client.aclose())
    emit(
        ["llama-index-llms-openai-like", "llama-index-core", "llama-index-tools-mcp", "mcp"],
        [describe(BasicMCPClient), describe(McpToolSpec)],
        {"command_or_url": URL, "headers": HEADERS, "allowed_tools": ["lookup"]},
        {
            "metadata.name": metadata.name,
            "metadata.description": metadata.description,
            "metadata.fn_schema": schema,
        },
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
