#!/usr/bin/env python3
"""Offline §9/§10 verification against real Microsoft Agent Framework packages.

No MCP session is opened. Missing/renamed symbols and wrong descriptor shapes
fail the probe; DDK's guarded binding/derivation implementations are not bypassed.
"""

from __future__ import annotations

import argparse
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
        "signature": str(inspect.signature(symbol)),
        "owning_distribution": f"{package}=={dist.version}",
    }


def lookup(city: str) -> str:
    """Look up a city."""
    return city


def check_binding() -> dict[str, Any]:
    from agent_framework import MCPStreamableHTTPTool

    kwargs = {
        "name": "offline-probe",
        "url": "https://placeholder.invalid/mcp",
        "description": "Offline endpoint configuration probe.",
        "static_headers": {"x-probe": "offline"},
    }
    inspect.signature(MCPStreamableHTTPTool).bind(**kwargs)
    binding = MCPStreamableHTTPTool(**kwargs)
    assert isinstance(binding, MCPStreamableHTTPTool)
    assert binding.name == kwargs["name"] and binding.url == kwargs["url"]
    assert binding.description == kwargs["description"]
    # This private read only checks that the supplied config was retained. It
    # does not establish header forwarding, which needs a real session.
    assert binding._static_headers == kwargs["static_headers"]
    return {
        "section": 9,
        "status": "VERIFIED (offline construction)",
        "public_path": "agent_framework.MCPStreamableHTTPTool",
        "symbol": describe(MCPStreamableHTTPTool, "agent-framework-core"),
        "kwargs": kwargs,
        "limit": "No connect(), context entry, discovery, tool call, or header forwarding checked.",
    }


def check_descriptor() -> dict[str, Any]:
    import agent_framework
    from agent_framework import FunctionTool, tool

    native = tool(lookup)
    assert isinstance(native, FunctionTool)
    assert isinstance(native.name, str) and native.name == "lookup"
    assert isinstance(native.description, str) and native.description == "Look up a city."
    schema = native.parameters()
    assert isinstance(schema, dict) and schema["type"] == "object"
    assert schema["properties"]["city"]["type"] == "string"
    assert "city" in schema["required"]
    specification = native.to_json_schema_spec()
    assert specification == {
        "type": "function",
        "function": {"name": native.name, "description": native.description, "parameters": schema},
    }
    # The old ledger named this public symbol. Record an actual negative lookup,
    # not an inferred rename or a claim about every historical package version.
    old_symbol_present = hasattr(agent_framework, "AIFunction")
    assert not old_symbol_present, "AIFunction is now present; revisit the recorded correction"
    return {
        "section": 10,
        "status": "VERIFIED (corrected offline attributes)",
        "public_path": "agent_framework.FunctionTool (created by agent_framework.tool)",
        "symbol": describe(FunctionTool, "agent-framework-core"),
        "old_public_symbol": {"agent_framework.AIFunction": old_symbol_present},
        "attributes": ["name", "description", "parameters()", "to_json_schema_spec()"],
        "descriptor": specification,
        "limit": "Reads the framework-computed schema; DDK descriptor derivation stays guarded.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", choices=["9", "10", "all"], default="all")
    parser.add_argument("--emit-verified", action="store_true")
    args = parser.parse_args()
    # Child-process hygiene only; telemetry variables are deliberately untouched.
    for key in tuple(os.environ):
        if key.lower().endswith("_proxy") or key.startswith(
            ("DONKEY_LLM_PROXY_", "ANYPOINT_", "MULESOFT_")
        ):
            os.environ.pop(key)
    sys.addaudithook(deny_network)
    checks = []
    if args.section in {"9", "all"}:
        checks.append(check_binding())
    if args.section in {"10", "all"}:
        checks.append(check_descriptor())
    versions = {p: version(p) for p in ["agent-framework", "agent-framework-core", "mcp"]}
    today = date.today().isoformat()
    print(
        json.dumps(
            {
                "date": today,
                "python": sys.version,
                "versions": versions,
                "network": "denied by audit hook",
                "checks": checks,
            },
            indent=2,
        )
    )
    if args.emit_verified:
        pins = "; ".join(f"{p}=={v}" for p, v in versions.items())
        print("\n# Candidate ledger rows; offline scope only; guards stay in place.")
        for result in checks:
            print(f"\n# Section {result['section']}")
            detail = result["public_path"]
            if result["section"] == 9:
                detail += "(name, url, description, static_headers)"
            else:
                detail += ": .name, .description, .parameters(), .to_json_schema_spec()"
            print(
                f"| MS Agent Framework | `{detail}` | {result['status']} | "
                f"{pins}; {today}; {result['limit']} |"
            )


if __name__ == "__main__":
    main()
