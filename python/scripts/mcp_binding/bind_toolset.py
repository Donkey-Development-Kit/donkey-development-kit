"""Bind a ``ToolSet`` with every installed framework against local MCP servers (#221).

The probes next to this file verify each framework's own MCP class
(docs/verified-apis.md §9). This runs the SDK's binding methods on top of them:
``ToolSet.<framework>()`` against two copies of ``server.py`` (``inv-a`` and
``inv-b``, which expose the same tools), and checks, per framework:

- ``filter(allow=["add"])`` binds only ``add``, the call returns 5, and the
  server saw the ``ClientIdAuth`` pair;
- with both servers, a per-tool binding (LangGraph, LlamaIndex, Anthropic)
  exposes ``inv-a__add`` and ``inv-b__add``, and a whole-server binding (ADK,
  Strands, OpenAI Agents, Agent Framework) refuses with ``ConfigError`` until
  ``filter(deny=["inv-b__*"])`` removes the clash;
- ``require_approval(tools=["add"])`` makes a per-tool binding's ``add`` raise
  ``ApprovalRequired``, and a whole-server binding refuse.

Nothing here touches Anypoint or a gateway. Run it in a throwaway venv with the
SDK and the frameworks installed (README.md), from python/:

    /tmp/<venv>/bin/python scripts/mcp_binding/bind_toolset.py [--only langgraph ...]

Exit code 1 if any installed framework fails a check; a framework that is not
installed is reported and skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from typing import Any

from _harness import LocalServer, headers_seen, local_mcp_server

from donkey_kit.core.approvals import ApprovalRequired
from donkey_kit.core.errors import ConfigError
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet

AUTH = ClientIdAuth("fake-client-id", "fake-client-secret")
GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "local")])
PER_TOOL = ("langgraph", "llamaindex", "anthropic")
MODULES = {
    "langgraph": "langchain_mcp_adapters",
    "llamaindex": "llama_index.tools.mcp",
    "anthropic": "anthropic",
    "adk": "google.adk",
    "strands": "strands",
    "openai": "agents",
    "agent_framework": "agent_framework",
}


def toolset(*servers: tuple[str, LocalServer]) -> ToolSet:
    """A governed ``ToolSet`` over local servers, as discovery would build it."""
    handles = [
        McpServerHandle(
            ref=AssetRef("com.example", name, "1.0.0"), endpoint_url=srv.url, governance=GOVERNED
        )
        for name, srv in servers
    ]
    return ToolSet(handles, _auth=AUTH)


def text(result: Any) -> str:
    """Any framework's tool result as text, to look for the 5 in it."""
    if isinstance(result, (list, tuple)):
        return " ".join(text(r) for r in result)
    if isinstance(result, dict):
        return json.dumps(result)
    for attr in ("text", "content", "raw_output"):
        value = getattr(result, attr, None)
        if value is not None and value is not result:
            return text(value)
    return str(result)


# Per framework: bind, then return (exposed names, a call of add(2, 3) or None).
Run = Callable[[ToolSet], Awaitable[tuple[list[str], Callable[[], Awaitable[Any]] | None]]]


async def run_langgraph(ts: ToolSet) -> tuple[list[str], Any]:
    tools = await ts.langgraph()
    add = next((t for t in tools if t.name.endswith("add")), None)
    return [t.name for t in tools], (lambda: add.ainvoke({"a": 2, "b": 3})) if add else None


async def run_llamaindex(ts: ToolSet) -> tuple[list[str], Any]:
    tools = await ts.llamaindex()
    add = next((t for t in tools if t.metadata.name.endswith("add")), None)
    return [t.metadata.name for t in tools], (lambda: add.acall(a=2, b=3)) if add else None


async def run_adk(ts: ToolSet) -> tuple[list[str], Any]:
    names: list[str] = []
    for toolset_ in await ts.adk():
        names += [t.name for t in await toolset_.get_tools()]
        await toolset_.close()
    return names, None  # the call runs through an ADK Runner; probe_adk.py covers it


async def run_strands(ts: ToolSet) -> tuple[list[str], Any]:
    names: list[str] = []
    result = None
    for client in await ts.strands():
        with client:
            names += [t.tool_name for t in client.list_tools_sync()]
            if "add" in names:
                result = client.call_tool_sync("t1", "add", {"a": 2, "b": 3})

    async def call() -> Any:
        return result

    return names, call if result is not None else None


async def run_openai(ts: ToolSet) -> tuple[list[str], Any]:
    names: list[str] = []
    result = None
    for server in await ts.openai():
        async with server:
            from agents import Agent
            from agents.run_context import RunContextWrapper

            listed = await server.list_tools(RunContextWrapper(None), Agent(name="probe"))
            names += [t.name for t in listed]
            if "add" in names:
                result = await server.call_tool("add", {"a": 2, "b": 3})

    async def call() -> Any:
        return result

    return names, call if result is not None else None


async def run_agent_framework(ts: ToolSet) -> tuple[list[str], Any]:
    names: list[str] = []
    result = None
    for tool in await ts.agent_framework():
        async with tool:
            names += [f.name for f in tool.functions]
            add = next((f for f in tool.functions if f.name == "add"), None)
            if add is not None:
                result = await add.invoke(arguments={"a": 2, "b": 3})

    async def call() -> Any:
        return result

    return names, call if result is not None else None


async def check_anthropic(ts: ToolSet, expect: list[str], approval: bool) -> list[str]:
    """Anthropic binds inside a context manager, so its checks run inside it."""
    problems: list[str] = []
    async with ts.anthropic() as tools:
        names = sorted(t.name for t in tools)
        if names != expect:
            problems.append(f"bound {names}, expected {expect}")
        add = next((t for t in tools if t.name.endswith("add")), None)
        if add is not None:
            try:
                out = await add.call({"a": 2, "b": 3})
            except ApprovalRequired:
                if not approval:
                    problems.append("add asked for approval without a rule")
            else:
                if approval:
                    problems.append("add ran without the approval its rule requires")
                elif "5" not in text(out):
                    problems.append(f"add returned {out!r}")
    return problems


RUNS: dict[str, Run] = {
    "langgraph": run_langgraph,
    "llamaindex": run_llamaindex,
    "adk": run_adk,
    "strands": run_strands,
    "openai": run_openai,
    "agent_framework": run_agent_framework,
}


async def scenario(fw: str, ts: ToolSet, expect: list[str], *, approval: bool = False) -> list[str]:
    """Bind ``ts`` with ``fw``; the problems found, empty when it behaved."""
    if fw == "anthropic":
        return await check_anthropic(ts, expect, approval)
    names, call = await RUNS[fw](ts)
    problems = [] if sorted(names) == expect else [f"bound {sorted(names)}, expected {expect}"]
    if call is not None:
        try:
            out = await call()
        except ApprovalRequired:
            if not approval:
                problems.append("add asked for approval without a rule")
        else:
            if approval:
                problems.append("add ran without the approval its rule requires")
            elif "5" not in text(out):
                problems.append(f"add returned {out!r}")
    return problems


async def check(fw: str, a: LocalServer, b: LocalServer) -> list[str]:
    """Every scenario for ``fw``."""
    problems = [
        f"allow: {p}"
        for p in await scenario(fw, toolset(("inv-a", a)).filter(allow=["add"]), ["add"])
    ]
    seen = headers_seen(a.record)
    if seen != {"client_id": "fake-client-id", "client_secret": "fake-client-secret"}:
        problems.append(f"server saw {seen}")
    both = toolset(("inv-a", a), ("inv-b", b))
    if fw in PER_TOOL:
        expect = sorted(f"{s}__{t}" for s in ("inv-a", "inv-b") for t in ("add", "lookup_sku"))
        problems += [f"collision: {p}" for p in await scenario(fw, both, expect)]
        gated = toolset(("inv-a", a)).require_approval(tools=["add"], risk="test")
        problems += [
            f"approval: {p}"
            for p in await scenario(fw, gated, ["add", "lookup_sku"], approval=True)
        ]
    else:
        try:
            await scenario(fw, both, [])
            problems.append("collision: bound a clash it cannot rename")
        except ConfigError:
            pass
        narrowed = both.filter(deny=["inv-b__*"])
        problems += [f"deny: {p}" for p in await scenario(fw, narrowed, ["add", "lookup_sku"])]
        try:
            await scenario(fw, toolset(("inv-a", a)).require_approval(tools=["add"]), [])
            problems.append("approval: bound a gate it cannot apply")
        except NotImplementedError:
            pass
    return problems


def main() -> int:
    """Run the checks for every installed framework (or ``--only``)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", choices=sorted(MODULES))
    args = parser.parse_args()
    failed = False
    with ExitStack() as stack:
        a = stack.enter_context(local_mcp_server())
        b = stack.enter_context(local_mcp_server())
        for fw in args.only or sorted(MODULES):
            if importlib.util.find_spec(MODULES[fw].split(".")[0]) is None or (
                "." in MODULES[fw] and importlib.util.find_spec(MODULES[fw]) is None
            ):
                print(f"{fw:16} NOT INSTALLED")
                continue
            problems = asyncio.run(check(fw, a, b))
            failed |= bool(problems)
            print(f"{fw:16} {'FAIL' if problems else 'OK'}")
            for problem in problems:
                print(f"    {problem}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
