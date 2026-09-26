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
    from google.adk.tools import FunctionTool
    from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

    params = StreamableHTTPConnectionParams(url=URL, headers=HEADERS)
    binding = McpToolset(connection_params=params)
    assert isinstance(binding, McpToolset)
    assert params.url == URL and params.headers == HEADERS
    tool = FunctionTool(func=lookup)
    declaration = tool._get_declaration()
    assert declaration is not None
    assert declaration.name == "lookup" and declaration.description == "Look up a city."
    # Read the framework's already-computed declaration, not Python annotations.
    schema = declaration.parameters_json_schema
    if schema is None:
        assert declaration.parameters is not None
        schema = declaration.parameters.model_dump(mode="json", exclude_none=True)
    assert "city" in schema["properties"] and "city" in schema["required"]
    assert schema["properties"]["city"]["type"].lower() == "string"
    emit(
        ["google-adk", "litellm", "google-genai", "mcp"],
        [describe(McpToolset), describe(StreamableHTTPConnectionParams)],
        {"connection_params": {"url": params.url, "headers": params.headers}},
        {"name": declaration.name, "description": declaration.description, "schema": schema},
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
