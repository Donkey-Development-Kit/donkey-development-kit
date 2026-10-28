#!/usr/bin/env python3
"""MS Agent Framework probe: ``MCPStreamableHTTPTool`` against the local reference server.

Binds ``agent_framework.MCPStreamableHTTPTool(name, url, static_headers=...)``, lists the
tools through the framework (``tool.functions`` -> ``FunctionTool``), invokes ``add``
through ``FunctionTool.invoke`` and confirms from the server's record that both Client
ID Enforcement headers arrived. Also notes, informationally, whether the URL without
the trailing slash works against this server.

Usage (throwaway venv with ``agent-framework`` and ``mcp`` installed), from python/:
    python scripts/mcp_binding/probe_agent_framework.py [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from typing import Any

from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from agent_framework import FunctionTool, MCPStreamableHTTPTool

HEADERS = {"client_id": "probe-id", "client_secret": "probe-secret"}


def qualname(obj: object) -> str:
    """Return ``module.QualName`` of the type of ``obj``."""
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


async def run(srv: LocalServer) -> dict[str, Any]:
    """Bind, list, and call ``add`` through the framework's own tool path."""
    async with MCPStreamableHTTPTool(
        "ddk_probe", srv.url, static_headers=HEADERS, load_prompts=False
    ) as tool:
        functions = list(tool.functions)
        types = sorted({qualname(f) for f in functions})
        assert all(isinstance(f, FunctionTool) for f in functions), types
        listed = sorted(f.name for f in functions)
        assert listed == ["add", "lookup_sku"], listed
        add = next(f for f in functions if f.name == "add")
        contents = await add.invoke(arguments={"a": 2, "b": 3})
    text = "".join(getattr(c, "text", None) or "" for c in contents)
    assert json.loads(text) == {"result": 5}, (text, contents)
    return {"listed": listed, "native": types, "text": text}


async def no_slash(srv: LocalServer) -> str:
    """Informational: does the URL without the trailing slash connect?"""
    try:
        async with MCPStreamableHTTPTool(
            "ddk_probe_noslash",
            srv.url.rstrip("/"),
            static_headers=HEADERS,
            load_prompts=False,
        ) as tool:
            return f"works ({sorted(f.name for f in tool.functions)})"
    except Exception as exc:  # noqa: BLE001 - informational only, any failure is the answer
        return f"failed: {type(exc).__name__}: {exc}"


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = asyncio.run(run(srv))
        seen = headers_seen(srv.record)
        slashless = asyncio.run(no_slash(srv))
    expected = {"client_id": HEADERS["client_id"], "client_secret": HEADERS["client_secret"]}
    assert seen == expected, seen
    fixture = {
        "framework": "agent_framework",
        "distributions": distributions("agent-framework", "agent-framework-core"),
        "binding": {
            "class": "MCPStreamableHTTPTool",
            "module": "agent_framework",
            "call": (
                "async with MCPStreamableHTTPTool(name, url, static_headers={...}) as tool: "
                "tool.functions -> FunctionTool.invoke(arguments={...})"
            ),
        },
        "url_shape": "http://127.0.0.1:<port>/mcp/",
        "tools_listed": out["listed"],
        "native_tool_types": out["native"],
        "tool_call": {"name": "add", "args": {"a": 2, "b": 3}, "result": out["text"]},
        "headers_seen": seen,
        "date": dt.date.today().isoformat(),
    }
    print(json.dumps(fixture, indent=2, sort_keys=True))
    print("no-trailing-slash (informational):", slashless)
    if args.write:
        print("wrote", write_fixture("agent_framework", fixture))


if __name__ == "__main__":
    main()
