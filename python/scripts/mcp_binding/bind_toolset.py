"""Bind a ``ToolSet`` with every installed framework against local MCP servers (#221).

The probes next to this file verify each framework's own MCP class
(docs/verified-apis.md §9). This runs the SDK's binding methods on top of them:
``ToolSet.<framework>()`` against two copies of ``server.py`` (``inv-a`` and
``inv-b``, which expose the same tools), and checks, per framework:

- ``filter(allow=["add"])`` binds only ``add``, the call returns 5, and the
  server saw the ``ClientIdAuth`` pair;
- with both servers, a per-tool binding (LangGraph, LlamaIndex, Anthropic)
  exposes ``inv-a__add`` and ``inv-b__add``, and a whole-server binding (ADK,
  Strands, OpenAI Agents, Agent Framework, CrewAI) refuses with ``ConfigError`` until
  ``filter(deny=["inv-b__*"])`` removes the clash;
- ``require_approval(tools=["add"])`` makes a per-tool binding's ``add`` raise
  ``ApprovalRequired``, and a whole-server binding refuse;
- bound to a server that answers with a ``307`` to ``inv-b``, another origin,
  the binding sends the pair to the server it was bound to and never to
  ``inv-b`` (#1415). Whether it then follows the redirect is the framework's.

Nothing here touches Anypoint or a gateway. Run it in a throwaway venv with the
SDK and the frameworks installed (README.md), from python/:

    /tmp/<venv>/bin/python scripts/mcp_binding/bind_toolset.py [--only langgraph ...]

Exit code 1 if any installed framework fails a check; a framework that is not
installed is reported and skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from typing import Any

from _harness import (
    LocalServer,
    credentials_seen,
    cross_origin_redirect,
    headers_seen,
    local_mcp_server,
)

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
    "crewai": "crewai_tools",
}
#: Bindings entered as an async context manager rather than awaited.
ENTERED = ("anthropic", "crewai")


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


#: The tool each run calls, and its arguments: add(2, 3) on the local server.
Call = tuple[str, dict[str, Any]]
ADD: Call = ("add", {"a": 2, "b": 3})

# Per framework: bind, then return (exposed names, a call of the tool or None).
Run = Callable[[ToolSet, Call], Awaitable[tuple[list[str], Callable[[], Awaitable[Any]] | None]]]


async def run_langgraph(ts: ToolSet, call: Call = ADD) -> tuple[list[str], Any]:
    tools = await ts.langgraph()
    tool = next((t for t in tools if t.name.endswith(call[0])), None)
    return [t.name for t in tools], (lambda: tool.ainvoke(call[1])) if tool else None


async def run_llamaindex(ts: ToolSet, call: Call = ADD) -> tuple[list[str], Any]:
    tools = await ts.llamaindex()
    tool = next((t for t in tools if t.metadata.name.endswith(call[0])), None)
    return [t.metadata.name for t in tools], (lambda: tool.acall(**call[1])) if tool else None


async def run_adk(ts: ToolSet, _call: Call = ADD) -> tuple[list[str], Any]:
    names: list[str] = []
    for toolset_ in await ts.adk():
        names += [t.name for t in await toolset_.get_tools()]
        await toolset_.close()
    return names, None  # the call runs through an ADK Runner; probe_adk.py covers it


async def run_strands(ts: ToolSet, call: Call = ADD) -> tuple[list[str], Any]:
    names: list[str] = []
    result = None
    for client in await ts.strands():
        with client:
            names += [t.tool_name for t in client.list_tools_sync()]
            if call[0] in names:
                result = client.call_tool_sync("t1", call[0], call[1])

    async def call() -> Any:
        return result

    return names, call if result is not None else None


async def run_openai(ts: ToolSet, call: Call = ADD) -> tuple[list[str], Any]:
    names: list[str] = []
    result = None
    for server in await ts.openai():
        async with server:
            from agents import Agent
            from agents.run_context import RunContextWrapper

            listed = await server.list_tools(RunContextWrapper(None), Agent(name="probe"))
            names += [t.name for t in listed]
            if call[0] in names:
                result = await server.call_tool(call[0], call[1])

    async def call() -> Any:
        return result

    return names, call if result is not None else None


async def run_agent_framework(ts: ToolSet, call: Call = ADD) -> tuple[list[str], Any]:
    names: list[str] = []
    result = None
    for tool in await ts.agent_framework():
        async with tool:
            names += [f.name for f in tool.functions]
            fn = next((f for f in tool.functions if f.name == call[0]), None)
            if fn is not None:
                result = await fn.invoke(arguments=call[1])

    async def call() -> Any:
        return result

    return names, call if result is not None else None


async def check_entered(
    fw: str, ts: ToolSet, expect: list[str], approval: bool, call: Call = ADD, want: str = "5"
) -> list[str]:
    """Anthropic and CrewAI bind inside a context manager, so their checks run inside it."""
    problems: list[str] = []
    async with getattr(ts, fw)() as tools:
        names = sorted(t.name for t in tools)
        if names != expect:
            problems.append(f"bound {names}, expected {expect}")
        tool = next((t for t in tools if t.name.endswith(call[0])), None)
        if tool is not None:
            try:
                if fw == "crewai":  # BaseTool.run blocks on the adapter's own loop
                    out = await asyncio.to_thread(tool.run, **call[1])
                else:
                    out = await tool.call(call[1])
            except ApprovalRequired:
                if not approval:
                    problems.append(f"{call[0]} asked for approval without a rule")
            else:
                if approval:
                    problems.append(f"{call[0]} ran without the approval its rule requires")
                elif want not in text(out):
                    problems.append(f"{call[0]} returned {out!r}")
    return problems


RUNS: dict[str, Run] = {
    "langgraph": run_langgraph,
    "llamaindex": run_llamaindex,
    "adk": run_adk,
    "strands": run_strands,
    "openai": run_openai,
    "agent_framework": run_agent_framework,
}


async def scenario(
    fw: str,
    ts: ToolSet,
    expect: list[str],
    *,
    approval: bool = False,
    call: Call = ADD,
    want: str = "5",
) -> list[str]:
    """Bind ``ts`` with ``fw`` and run ``call``, which must return ``want``; the
    problems found, empty when it behaved."""
    if fw in ENTERED:
        return await check_entered(fw, ts, expect, approval, call, want)
    names, run = await RUNS[fw](ts, call)
    problems = [] if sorted(names) == expect else [f"bound {sorted(names)}, expected {expect}"]
    if run is not None:
        try:
            out = await run()
        except ApprovalRequired:
            if not approval:
                problems.append(f"{call[0]} asked for approval without a rule")
        else:
            if approval:
                problems.append(f"{call[0]} ran without the approval its rule requires")
            elif want not in text(out):
                problems.append(f"{call[0]} returned {out!r}")
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
    problems += [f"redirect: {p}" for p in await redirected(fw, b)]
    return problems


async def redirected(fw: str, target: LocalServer) -> list[str]:
    """Bind ``fw`` to a server that redirects every request to ``target``, on
    another origin (#1415). The descriptors are given, so the SDK's own listing
    does not run and the framework's client makes the first request."""
    before = len(credentials_seen(target.record))
    with cross_origin_redirect(target) as redirect:
        handle = McpServerHandle(
            ref=AssetRef("com.example", "inv-r", "1.0.0"),
            endpoint_url=redirect.url,
            governance=GOVERNED,
            tool_descriptors=({"name": "add"}, {"name": "lookup_sku"}),
        )
        # The bind fails when the redirect is not followed; only the headers matter.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(scenario(fw, ToolSet([handle], _auth=AUTH), []), 60)
    problems = []
    if not any(s["client_secret"] == "fake-client-secret" for s in redirect.seen):
        problems.append(f"the bound server never got the credential: {redirect.seen}")
    leaked = credentials_seen(target.record)[before:]
    if leaked:
        problems.append(f"another origin got the credential: {leaked}")
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
