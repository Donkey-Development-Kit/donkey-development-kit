#!/usr/bin/env python3
"""Capture the descriptor-derivation attribute reads (docs/verified-apis.md §10).

The executable form of the verification step for ``registry/introspect.py``
(BG §2.5, #1024). For each framework it builds one real bound tool from the same
reference function, reads the attributes §10 names straight off the object, and
writes what it saw to ``tests/fixtures/descriptor/<key>.json``: the installed
version, the object's class, every raw read, and the descriptor those reads
make. It never calls ``derive_descriptor()``, so the fixture is an independent
second read that ``tests/unit/test_descriptor_derivation.py`` holds the
derivation to.

The attributes are semi-public and break on upstream releases. That test runs
in every adapter-contract leg (pinned) and every nightly-matrix leg (latest),
so a rename fails loudly instead of publishing a wrong descriptor.

Usage (one framework per environment; the adapter-contract locks pin them):
    python scripts/capture_descriptor_fixtures.py            # print every installed capture
    python scripts/capture_descriptor_fixtures.py --write    # write the fixtures
    python scripts/capture_descriptor_fixtures.py --only langchain mcp

After ``--write``, relock: ``python -m donkey_kit.simulator.fixtures --relock``.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import importlib.metadata
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "descriptor"


def lookup_employee(employee_id: str, include_manager: bool = False) -> str:
    """Look up an employee record by ID.

    Args:
        employee_id: The employee's ID.
        include_manager: Whether to include the manager.
    """
    return employee_id


# --- builders: zero-argument factories, so each is also a derive_descriptor() entrypoint


def build_mcp() -> Any:
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server
    except ImportError:  # mcp 2.x renamed FastMCP to MCPServer
        from mcp.server.mcpserver import MCPServer as Server
    server = Server("hr-tools")
    server.tool()(lookup_employee)
    return server


def build_langchain() -> Any:
    from langchain_core.tools import tool

    return tool(lookup_employee)


def build_strands() -> Any:
    from strands import tool

    return tool(lookup_employee)


def build_adk() -> Any:
    from google.adk.tools import FunctionTool

    return FunctionTool(lookup_employee)


def build_llamaindex() -> Any:
    from llama_index.core.tools import FunctionTool

    return FunctionTool.from_defaults(fn=lookup_employee)


def build_openai_agents() -> Any:
    from agents import function_tool

    return function_tool(lookup_employee)


def build_anthropic() -> Any:
    from anthropic import beta_tool

    return beta_tool(lookup_employee)


def build_crewai() -> Any:
    from crewai.tools import tool

    return tool(lookup_employee)


def build_agent_framework() -> Any:
    from agent_framework import tool

    return tool(lookup_employee)


# --- the §10 reads, one per framework: {expression: value} ---------------------


def _schema(obj: Any) -> Any:
    return obj if isinstance(obj, dict) else obj.model_json_schema()


async def _read_mcp(server: Any) -> dict[str, Any]:
    wire = (await server.list_tools())[0].model_dump(by_alias=True, mode="json")
    return {
        "(await list_tools())[0].name": wire["name"],
        "(await list_tools())[0].description": wire["description"],
        "(await list_tools())[0].inputSchema": wire["inputSchema"],
    }


def _read_langchain(t: Any) -> dict[str, Any]:
    return {
        ".name": t.name,
        ".description": t.description,
        ".tool_call_schema.model_json_schema()": _schema(t.tool_call_schema),
    }


def _read_strands(t: Any) -> dict[str, Any]:
    return {
        ".tool_spec['name']": t.tool_spec["name"],
        ".tool_spec['description']": t.tool_spec["description"],
        ".tool_spec['inputSchema']['json']": t.tool_spec["inputSchema"]["json"],
    }


def _read_adk(t: Any) -> dict[str, Any]:
    d = t._get_declaration()
    return {
        "._get_declaration().name": d.name,
        "._get_declaration().description": d.description,
        "._get_declaration().parameters_json_schema": d.parameters_json_schema,
    }


def _read_llamaindex(t: Any) -> dict[str, Any]:
    return {
        ".metadata.name": t.metadata.name,
        ".metadata.description": t.metadata.description,
        ".metadata.get_parameters_dict()": t.metadata.get_parameters_dict(),
    }


def _read_openai_agents(t: Any) -> dict[str, Any]:
    return {
        ".name": t.name,
        ".description": t.description,
        ".params_json_schema": t.params_json_schema,
    }


def _read_anthropic(t: Any) -> dict[str, Any]:
    p = t.to_dict()
    return {
        ".to_dict()['name']": p["name"],
        ".to_dict()['description']": p["description"],
        ".to_dict()['input_schema']": p["input_schema"],
    }


def _read_crewai(t: Any) -> dict[str, Any]:
    return {
        ".name": t.name,
        ".description": t.description,
        ".args_schema.model_json_schema()": t.args_schema.model_json_schema(),
    }


def _read_agent_framework(t: Any) -> dict[str, Any]:
    return {".name": t.name, ".description": t.description, ".parameters()": t.parameters()}


# key -> (distribution, import probe, builder, reader)
FRAMEWORKS: dict[str, tuple[str, str, Callable[[], Any], Callable[[Any], Any]]] = {
    "mcp": ("mcp", "mcp.server", build_mcp, _read_mcp),
    "langchain": ("langchain-core", "langchain_core", build_langchain, _read_langchain),
    "strands": ("strands-agents", "strands", build_strands, _read_strands),
    "adk": ("google-adk", "google.adk", build_adk, _read_adk),
    "llamaindex": ("llama-index-core", "llama_index.core", build_llamaindex, _read_llamaindex),
    "openai_agents": ("openai-agents", "agents", build_openai_agents, _read_openai_agents),
    "anthropic": ("anthropic", "anthropic", build_anthropic, _read_anthropic),
    "crewai": ("crewai", "crewai", build_crewai, _read_crewai),
    "agent_framework": (
        "agent-framework-core",
        "agent_framework",
        build_agent_framework,
        _read_agent_framework,
    ),
}


def installed_version(key: str) -> str | None:
    """The installed version of ``key``'s distribution, or None when absent."""
    distribution, probe, _, _ = FRAMEWORKS[key]
    try:
        if importlib.util.find_spec(probe.partition(".")[0]) is None:
            return None
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def fixture_name(key: str, version: str) -> str:
    """mcp has two incompatible majors in the supported range, so one fixture each."""
    return f"mcp-{version.partition('.')[0]}" if key == "mcp" else key


async def capture(key: str) -> dict[str, Any]:
    """Build ``key``'s reference tool, read its §10 attributes, return the capture."""
    distribution, _, builder, reader = FRAMEWORKS[key]
    version = installed_version(key)
    if version is None:
        raise LookupError(f"{distribution} is not installed")
    obj = builder()
    reads = reader(obj)
    if asyncio.iscoroutine(reads):
        reads = await reads
    name, description, schema = reads.values()
    cls = type(obj)
    return {
        "framework": key,
        "distribution": distribution,
        "version": version,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "captured": dt.date.today().isoformat(),
        "capture": "python scripts/capture_descriptor_fixtures.py --write",
        "object": f"{cls.__module__}.{cls.__qualname__}",
        "reads": reads,
        "descriptor": {
            "tools": [{"name": name, "description": description, "inputSchema": schema}]
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", choices=sorted(FRAMEWORKS), metavar="KEY")
    parser.add_argument("--write", action="store_true", help="write tests/fixtures/descriptor/")
    args = parser.parse_args(argv)
    found = 0
    for key in args.only or FRAMEWORKS:
        version = installed_version(key)
        if version is None:
            print(f"{key}: NOT INSTALLED", file=sys.stderr)
            continue
        found += 1
        record = asyncio.run(capture(key))
        text = json.dumps(record, indent=2, sort_keys=False) + "\n"
        if args.write:
            FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
            path = FIXTURE_DIR / f"{fixture_name(key, version)}.json"
            path.write_text(text, encoding="utf-8")
            print(f"{key}: wrote {path.relative_to(FIXTURE_DIR.parents[2])}", file=sys.stderr)
        else:
            print(text)
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
