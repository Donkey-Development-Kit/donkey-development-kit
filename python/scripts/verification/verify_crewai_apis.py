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
    from crewai.tools import tool as make_tool
    from crewai_tools import MCPServerAdapter

    # __init__ eagerly calls start(); do not pretend an offline bind is construction.
    kwargs = {
        "serverparams": {"url": URL, "transport": "streamable-http", "headers": HEADERS},
        "connect_timeout": 1,
    }
    inspect.signature(MCPServerAdapter).bind(**kwargs)
    tool = make_tool(lookup)
    assert tool.name == "lookup" and "Look up a city." in tool.description
    schema = tool.args_schema.model_json_schema()
    assert schema["properties"]["city"]["type"] == "string"
    assert "city" in schema["required"]
    emit(
        ["crewai", "crewai-tools", "mcp"],
        [describe(MCPServerAdapter)],
        {"kwargs": kwargs, "scope": "import and signature binding only; constructor opens network"},
        {"name": tool.name, "description": tool.description, "args_schema": schema},
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
