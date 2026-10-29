"""The tool-binding scenarios: each ``ToolSet`` binding, driven through its real
framework against two local MCP servers (#991).

``single_tool_call``, ``multi_tool_multi_server`` and ``tool_filtering`` run once
per ``ToolSet`` binding (``langgraph()`` ... ``crewai()``), one per adapter in
``ADAPTERS``. Each binding is the framework's own MCP class, as the local
round-trip in ``scripts/mcp_binding`` verified it (docs/verified-apis.md §9). The
servers are two copies of ``scripts/mcp_binding/server.py`` (``inv-a`` and
``inv-b``, the same two tools, ``add`` and ``lookup_sku``) on 127.0.0.1, so
nothing here touches Anypoint or a gateway.

A call goes through the framework's own tool object or MCP client, with no model
turn, except ADK: its toolset is called through a ``Runner`` driven by a
scripted model (``probe_adk.py``). That is the depth §9 verified.

The whole-server bindings (ADK, Strands, OpenAI Agents, Agent Framework, CrewAI)
take an allow-list of tool names and cannot rename one tool, so
``multi_tool_multi_server`` is an asserted exemption for them in
``KNOWN_LIMITATIONS``: their test checks the ``ConfigError`` refusal and the
``filter(deny=...)`` remedy rather than skipping.

A binding skips when its packages are not installed, except in the
``adapter-contract`` CI leg for its framework when that leg's lock pins them:
there ``DONKEY_CONTRACT_EXTRA`` turns the skip into a failure, as in
``test_adapter_contract.py``. Every leg's lock pins its binding: ADK, Strands,
OpenAI Agents and Agent Framework through their extras, LangGraph, LlamaIndex,
Anthropic and CrewAI through the leg's ``contract-<extra>`` dependency group
(#1318). ``test_ci_requires_the_bindings_its_locks_pin`` keeps that honest.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from suite import CONFORMANCE_SCENARIOS, KNOWN_LIMITATIONS, SCENARIO_BODIES

from donkey_kit.core.errors import ConfigError
from donkey_kit.core.tool_access import ToolNotInProfile, tool_access_denial
from donkey_kit.integrations import ADAPTERS
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_HARNESS_PATH = _PYTHON_ROOT / "scripts" / "mcp_binding" / "_harness.py"
_CONSTRAINTS = _PYTHON_ROOT / "constraints"

_AUTH = ClientIdAuth("binding-client-id", "binding-client-secret")
_PAIR = {"client_id": "binding-client-id", "client_secret": "binding-client-secret"}
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "local")])
_TOOLS = ["add", "lookup_sku"]
_SERVER_MODULES = ("mcp", "uvicorn", "starlette")
_MULTI = "multi_tool_multi_server"
_DENIED = "tool_access_denied_typed"


@dataclass(frozen=True)
class Binding:
    """One ``ToolSet`` binding: the adapter it belongs to and what it needs."""

    adapter: str
    method: str
    per_tool: bool
    modules: tuple[str, ...]  # imported by the binding (or this test, for ADK's Runner)
    dists: tuple[str, ...]  # the distributions a CI lock must pin to require it


BINDINGS: dict[str, Binding] = {
    b.adapter: b
    for b in (
        Binding(
            "langgraph", "langgraph", True, ("langchain_mcp_adapters",), ("langchain-mcp-adapters",)
        ),
        Binding(
            "llamaindex", "llamaindex", True, ("llama_index.tools.mcp",), ("llama-index-tools-mcp",)
        ),
        Binding("anthropic", "anthropic", True, ("anthropic.lib.tools.mcp",), ("anthropic",)),
        Binding("adk", "adk", False, ("google.adk.tools.mcp_tool",), ("google-adk",)),
        Binding("strands", "strands", False, ("strands.tools.mcp",), ("strands-agents",)),
        Binding("openai_agents", "openai", False, ("agents.mcp",), ("openai-agents",)),
        Binding(
            "agent_framework", "agent_framework", False, ("agent_framework",), ("agent-framework",)
        ),
        Binding("crewai", "crewai", False, ("crewai_tools",), ("crewai-tools",)),
    )
}


def _lock_pins(extra: str, dists: tuple[str, ...]) -> bool:
    """Whether the ``adapter-contract`` lock for ``extra`` pins every one of ``dists``."""
    lock = _CONSTRAINTS / f"adapter-contract-{extra}-py3.12.txt"
    if not lock.exists():
        return False
    pinned = {
        re.sub(r"[-_.]+", "-", line.split("==", 1)[0]).lower()
        for line in lock.read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.startswith("#")
    }
    return all(d in pinned for d in dists)


def _ci_requires(binding: Binding) -> bool:
    """Whether this binding's CI leg must run it: the leg's lock pins its packages."""
    return _lock_pins(ADAPTERS[binding.adapter].extra, (*binding.dists, "mcp", "uvicorn"))


def _require(binding: Binding) -> None:
    modules = (*binding.modules, *_SERVER_MODULES)
    if os.environ.get("DONKEY_CONTRACT_EXTRA") == ADAPTERS[binding.adapter].extra and _ci_requires(
        binding
    ):
        for module in modules:  # this framework's CI leg pins them: never skip
            importlib.import_module(module)
    else:
        # Not pytest.importorskip: the conftest makes that fail for any module
        # under this leg's framework (#748), and the Anthropic leg installs
        # anthropic without the mcp that anthropic.lib.tools.mcp needs.
        for module in modules:
            try:
                importlib.import_module(module)
            except ImportError as exc:
                pytest.skip(f"{binding.method}() binding not installed: {exc}")


def _harness() -> ModuleType:
    """``scripts/mcp_binding/_harness.py``, loaded by path (``scripts`` is not a
    package). Registered in ``sys.modules`` first: its dataclasses resolve their
    annotations through their module."""
    name = "_mcp_binding_harness"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Servers:
    """The two local MCP servers, started on first use and stopped with the module."""

    def __init__(self) -> None:
        self._stack = ExitStack()
        self._pair: tuple[Any, Any] | None = None
        self._denying: Any = None
        self.harness: ModuleType | None = None

    def pair(self) -> tuple[Any, Any]:
        if self._pair is None:
            self.harness = _harness()
            self._pair = (
                self._stack.enter_context(self.harness.local_mcp_server()),
                self._stack.enter_context(self.harness.local_mcp_server()),
            )
        return self._pair

    def denying(self) -> Any:
        """A third server whose ``add`` gets the gateway's tool-access denial (#312)."""
        if self._denying is None:
            self.harness = _harness()
            self._denying = self._stack.enter_context(self.harness.local_mcp_server(deny="add"))
        return self._denying

    def requests(self, server: Any) -> int:
        assert self.harness is not None
        return len(self.harness.read_requests(server.record))

    def headers(self, server: Any) -> dict[str, str | None]:
        assert self.harness is not None
        seen: dict[str, str | None] = self.harness.headers_seen(server.record)
        return seen

    def close(self) -> None:
        self._stack.close()


@pytest.fixture(scope="module")
def servers() -> Iterator[_Servers]:
    pool = _Servers()
    try:
        yield pool
    finally:
        pool.close()


@pytest.fixture(params=list(BINDINGS))
def binding(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Binding:
    b = BINDINGS[request.param]
    _require(b)
    if b.method == "adk":
        # ADK's MCP session manager tries mTLS first by default: it calls
        # google.auth.default(), which probes the GCE metadata server
        # (169.254.169.254). Off here, so the run stays on loopback.
        monkeypatch.setenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", "false")
    return b


def _toolset(*named: tuple[str, Any]) -> ToolSet:
    """A governed ``ToolSet`` over local servers, as discovery builds it."""
    handles = [
        McpServerHandle(
            ref=AssetRef("com.example", name, "1.0.0"), endpoint_url=srv.url, governance=_GOVERNED
        )
        for name, srv in named
    ]
    return ToolSet(handles, _auth=_AUTH)


def _text(result: Any) -> str:
    """Any framework's tool result as text (each wraps the MCP result its own way)."""
    if isinstance(result, (list, tuple)):
        return " ".join(_text(r) for r in result)
    if isinstance(result, dict):
        return json.dumps(result)
    for attr in ("text", "content", "raw_output"):
        value = getattr(result, attr, None)
        if value is not None and value is not result:
            return _text(value)
    return str(result)


Call = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Bound:
    """What a binding exposes, as the framework lists it, and a way to call one tool."""

    names: list[str]
    call: Call


def _find(tools: list[Any], name: str, attr: Callable[[Any], str]) -> Any:
    tool = next((t for t in tools if attr(t) == name), None)
    assert tool is not None, f"{name!r} is not bound: {[attr(t) for t in tools]}"
    return tool


@asynccontextmanager
async def _bound(binding: Binding, ts: ToolSet) -> AsyncIterator[Bound]:
    """Bind ``ts`` with ``binding`` and expose its names and a tool call, the way
    the verified round-trip does (docs/verified-apis.md §9)."""
    async with AsyncExitStack() as stack:
        if binding.method == "langgraph":
            tools = await ts.langgraph()

            async def call(name: str, args: dict[str, Any]) -> str:
                return _text(await _find(tools, name, lambda t: t.name).ainvoke(args))

            yield Bound([t.name for t in tools], call)
        elif binding.method == "llamaindex":
            tools = await ts.llamaindex()

            async def call(name: str, args: dict[str, Any]) -> str:
                return _text(await _find(tools, name, lambda t: t.metadata.name).acall(**args))

            yield Bound([t.metadata.name for t in tools], call)
        elif binding.method == "anthropic":
            tools = await stack.enter_async_context(ts.anthropic())

            async def call(name: str, args: dict[str, Any]) -> str:
                return _text(await _find(tools, name, lambda t: t.name).call(args))

            yield Bound([t.name for t in tools], call)
        elif binding.method == "crewai":
            tools = await stack.enter_async_context(ts.crewai())

            async def call(name: str, args: dict[str, Any]) -> str:
                tool = _find(tools, name, lambda t: t.name)
                # BaseTool.run blocks on the adapter's own loop.
                return _text(await asyncio.to_thread(tool.run, **args))

            yield Bound([t.name for t in tools], call)
        elif binding.method == "adk":
            toolsets = await ts.adk()
            for toolset in toolsets:
                stack.push_async_callback(toolset.close)
            names = [t.name for toolset in toolsets for t in await toolset.get_tools()]

            async def call(name: str, args: dict[str, Any]) -> str:
                return await _adk_run(toolsets, name, args)

            yield Bound(names, call)
        elif binding.method == "strands":
            owner: dict[str, Any] = {}
            for client in await ts.strands():
                stack.enter_context(client)
                for tool in client.list_tools_sync():
                    owner[tool.tool_name] = client

            async def call(name: str, args: dict[str, Any]) -> str:
                assert name in owner, f"{name!r} is not bound: {sorted(owner)}"
                return _text(owner[name].call_tool_sync("t1", name, args))

            yield Bound(list(owner), call)
        elif binding.method == "openai":
            from agents import Agent
            from agents.run_context import RunContextWrapper

            owner = {}
            for server in await ts.openai():
                await stack.enter_async_context(server)
                for tool in await server.list_tools(RunContextWrapper(None), Agent(name="probe")):
                    owner[tool.name] = server

            async def call(name: str, args: dict[str, Any]) -> str:
                assert name in owner, f"{name!r} is not bound: {sorted(owner)}"
                return _text(await owner[name].call_tool(name, args))

            yield Bound(list(owner), call)
        elif binding.method == "agent_framework":
            functions: list[Any] = []
            for tool in await ts.agent_framework():
                await stack.enter_async_context(tool)
                functions += list(tool.functions)

            async def call(name: str, args: dict[str, Any]) -> str:
                fn = _find(functions, name, lambda f: f.name)
                return _text(await fn.invoke(arguments=args))

            yield Bound([f.name for f in functions], call)
        else:  # pragma: no cover - BINDINGS lists every method
            raise AssertionError(binding.method)


async def _adk_run(toolsets: list[Any], name: str, args: dict[str, Any]) -> str:
    """Call ``name`` through an ADK ``Runner``, its model scripted to ask for it once."""
    from google.adk.agents import LlmAgent
    from google.adk.models import BaseLlm, LlmRequest, LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    class Scripted(BaseLlm):
        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncIterator[LlmResponse]:
            answered = any(
                part.function_response is not None
                for content in llm_request.contents
                for part in content.parts or ()
            )
            part = (
                types.Part(text="done")
                if answered
                else types.Part(function_call=types.FunctionCall(id="fc-1", name=name, args=args))
            )
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    agent = LlmAgent(name="binding", model=Scripted(model="scripted"), tools=list(toolsets))
    sessions = InMemorySessionService()
    runner = Runner(app_name="binding", agent=agent, session_service=sessions)
    session = await sessions.create_session(app_name="binding", user_id="u")
    message = types.Content(role="user", parts=[types.Part(text=f"call {name}")])
    responses: list[Any] = []
    async for event in runner.run_async(user_id="u", session_id=session.id, new_message=message):
        responses.extend(r.response for r in event.get_function_responses())
    assert len(responses) == 1, responses
    return _text(responses[0])


# --- single_tool_call -------------------------------------------------------------


async def test_single_tool_call(binding: Binding, servers: _Servers) -> None:
    """One server: the framework lists both tools under their own names, a call of
    ``add`` returns its result, and every request carried the credential pair."""
    a, _ = servers.pair()
    async with _bound(binding, _toolset(("inv-a", a))) as bound:
        assert sorted(bound.names) == _TOOLS
        assert "42" in await bound.call("add", {"a": 20, "b": 22})
    assert servers.headers(a) == _PAIR


# --- tool_filtering ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("narrow", "kept"),
    [
        ({"deny": ["lookup_sku"]}, ["add"]),
        ({"allow": ["lookup_*"]}, ["lookup_sku"]),
        ({"allow": ["*"], "deny": ["inv-a__add"]}, ["lookup_sku"]),
    ],
    ids=["deny", "allow-glob", "deny-qualified"],
)
async def test_tool_filtering(
    binding: Binding, servers: _Servers, narrow: dict[str, list[str]], kept: list[str]
) -> None:
    """``filter()`` decides what the framework itself lists: a tool it removes is
    not bound at all, not merely hidden from the SDK's own view."""
    a, _ = servers.pair()
    async with _bound(binding, _toolset(("inv-a", a)).filter(**narrow)) as bound:
        assert sorted(bound.names) == kept
        if "add" in kept:
            assert "5" in await bound.call("add", {"a": 2, "b": 3})


# --- multi_tool_multi_server ------------------------------------------------------


async def test_multi_tool_multi_server(binding: Binding, servers: _Servers) -> None:
    """Two servers exposing the same tools. A per-tool binding renames each clash
    to ``<server>__<tool>`` and routes each call to its own server. A whole-server
    binding is exempt (``KNOWN_LIMITATIONS``): it refuses the clash with
    ``ConfigError`` and binds both servers once ``filter(deny=...)`` removes it."""
    a, b = servers.pair()
    both = _toolset(("inv-a", a), ("inv-b", b))
    if _MULTI in KNOWN_LIMITATIONS.get(binding.adapter, {}):
        with pytest.raises(ConfigError, match="cannot rename a single tool"):
            async with _bound(binding, both):
                pass
        narrowed = both.filter(deny=["inv-a__lookup_sku", "inv-b__add"])
        # ADK's Runner lists every toolset on each model turn, so the other server
        # sees requests during an ADK call; only the call's own server is checked.
        alone = binding.method != "adk"
        async with _bound(binding, narrowed) as bound:
            assert sorted(bound.names) == _TOOLS
            await _assert_routed(
                servers, bound, "add", {"a": 20, "b": 22}, "42", to=a, not_to=b, alone=alone
            )
            await _assert_routed(
                servers, bound, "lookup_sku", {"sku": "SKU-7"}, "SKU-7", to=b, not_to=a, alone=alone
            )
        return
    async with _bound(binding, both) as bound:
        assert sorted(bound.names) == sorted(
            f"{s}__{t}" for s in ("inv-a", "inv-b") for t in _TOOLS
        )
        await _assert_routed(servers, bound, "inv-b__add", {"a": 20, "b": 22}, "42", to=b, not_to=a)
        await _assert_routed(servers, bound, "inv-a__add", {"a": 2, "b": 3}, "5", to=a, not_to=b)
    assert servers.headers(a) == _PAIR
    assert servers.headers(b) == _PAIR


async def _assert_routed(
    servers: _Servers,
    bound: Bound,
    name: str,
    args: dict[str, Any],
    want: str,
    *,
    to: Any,
    not_to: Any,
    alone: bool = True,
) -> None:
    """Call ``name`` and check it reached ``to`` and, when ``alone``, sent nothing
    to ``not_to``."""
    before_to, before_other = servers.requests(to), servers.requests(not_to)
    assert want in await bound.call(name, args)
    assert servers.requests(to) > before_to, f"{name} never reached its server"
    if alone:
        assert servers.requests(not_to) == before_other, f"{name} reached the other server"


# --- tool_access_denied_typed ------------------------------------------------------


async def test_tool_access_denied_typed(binding: Binding, servers: _Servers) -> None:
    """The gateway's tool-access denial (JSON-RPC ``-32008``, docs/verified-apis.md
    §9, #303) on a tool the set still lists. A per-tool binding raises
    ``ToolNotInProfile`` naming the tool and server, and the other tool still
    works. A whole-server binding is exempt (``KNOWN_LIMITATIONS``): the framework
    makes the call, and what it does with the denial is asserted as recorded."""
    denying = servers.denying()
    async with _bound(binding, _toolset(("inv-d", denying))) as bound:
        assert sorted(bound.names) == _TOOLS
        if _DENIED not in KNOWN_LIMITATIONS.get(binding.adapter, {}):
            with pytest.raises(ToolNotInProfile) as caught:
                await bound.call("add", {"a": 2, "b": 3})
            assert caught.value.tool_name == "add"
            assert caught.value.server == "com.example/inv-d/1.0.0"
            assert caught.value.profile is None
            assert "API Manager" in caught.value.remediation
            assert "SKU-9" in await bound.call("lookup_sku", {"sku": "SKU-9"})
            return
        try:
            text = await bound.call("add", {"a": 2, "b": 3})
        except Exception as exc:  # noqa: BLE001 - recording what the framework raises
            assert binding.method in _RAISES_THE_MCP_ERROR, f"{binding.method} raised {exc!r}"
            denied = tool_access_denial(exc, tool="add")
            assert denied is not None, f"{binding.method} lost the denial: {exc!r}"
            assert denied.tool_name == "add"
        else:
            assert binding.method in _RETURNS_AN_ERROR_RESULT, f"{binding.method} returned {text!r}"
            assert "Unable to access" in text, text


#: What each whole-server binding's framework does with the denial, recorded with
#: scripts/mcp_binding/probe_denial.py (docs/verified-apis.md §9, #312).
_RAISES_THE_MCP_ERROR = {"openai", "agent_framework", "crewai"}
_RETURNS_AN_ERROR_RESULT = {"adk", "strands"}


# --- the matrix itself (no framework needed) --------------------------------------


def test_every_adapter_has_a_binding() -> None:
    assert set(BINDINGS) == set(ADAPTERS)
    for scenario in ("single_tool_call", _MULTI, "tool_filtering", _DENIED):
        assert scenario in CONFORMANCE_SCENARIOS
        assert any("test_tool_binding.py" in node for node in SCENARIO_BODIES[scenario])


def test_the_whole_server_bindings_are_exactly_the_exempt_ones() -> None:
    """``multi_tool_multi_server`` is exempt for exactly the bindings that bind a
    whole server as one object, and the reason says why and how to recover."""
    exempt = {adapter for adapter, limits in KNOWN_LIMITATIONS.items() if _MULTI in limits}
    assert exempt == {b.adapter for b in BINDINGS.values() if not b.per_tool}
    for adapter in exempt:
        reason = KNOWN_LIMITATIONS[adapter][_MULTI]
        assert "cannot rename" in reason and "filter(deny=" in reason, reason


def test_the_denial_exemptions_are_exactly_the_whole_server_bindings() -> None:
    """``tool_access_denied_typed`` is exempt for exactly the whole-server
    bindings, each one's recorded behaviour is asserted above, and its reason
    names the helper that converts the denial."""
    exempt = {adapter for adapter, limits in KNOWN_LIMITATIONS.items() if _DENIED in limits}
    whole = {b.adapter for b in BINDINGS.values() if not b.per_tool}
    assert exempt == whole
    assert _RAISES_THE_MCP_ERROR | _RETURNS_AN_ERROR_RESULT == {BINDINGS[a].method for a in whole}
    assert not _RAISES_THE_MCP_ERROR & _RETURNS_AN_ERROR_RESULT
    for adapter in exempt:
        assert "tool_access_denial" in KNOWN_LIMITATIONS[adapter][_DENIED] or (
            "Unable to access" in KNOWN_LIMITATIONS[adapter][_DENIED]
        )


def test_no_other_tool_binding_scenario_is_exempt() -> None:
    for scenario in ("single_tool_call", "tool_filtering"):
        assert not [a for a, limits in KNOWN_LIMITATIONS.items() if scenario in limits]


def test_ci_requires_the_bindings_its_locks_pin() -> None:
    """Every CI leg fails rather than skips its binding, because every leg's lock
    pins its packages (#1318). A lock that stops pinning one fails here."""
    required = {b.adapter for b in BINDINGS.values() if _ci_requires(b)}
    assert required == set(ADAPTERS)
