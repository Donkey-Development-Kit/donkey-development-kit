"""Locked per-tool bindings convert only the validated catalog, never a second listing."""

from __future__ import annotations

import copy
import sys
import types
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from donkey_kit.registry.criteria import GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.lockfile import build_lock
from donkey_kit.tools.session import ToolSet

_DESCRIPTOR = {
    "name": "lookup",
    "description": "Reviewed description",
    "inputSchema": {"type": "object", "properties": {"sku": {"type": "string"}}},
    "annotations": {"readOnlyHint": True},
    "outputSchema": {"type": "object"},
}


@pytest.mark.parametrize("binding", ["langgraph", "llamaindex", "anthropic"])
async def test_locked_binding_uses_validated_snapshot(
    monkeypatch: pytest.MonkeyPatch, binding: str
) -> None:
    converted: list[dict[str, Any]] = []
    listed: list[str] = []

    async def invoke(**kwargs: Any) -> str:
        return "called"

    class MCPTool:
        def __init__(self, **descriptor: Any) -> None:
            self.descriptor = descriptor
            self.name = descriptor["name"]

        @classmethod
        def model_validate(cls, descriptor: dict[str, Any]) -> MCPTool:
            return cls(**descriptor)

    def convert(session: Any, tool: MCPTool, **kwargs: Any) -> Any:
        converted.append(copy.deepcopy(tool.descriptor))
        return types.SimpleNamespace(
            name=tool.name,
            coroutine=invoke,
            func=invoke,
            description=tool.descriptor["description"],
            input_schema=tool.descriptor["inputSchema"],
        )

    class Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.connections = args[0] if args else {}

        async def get_tools(self, **kwargs: Any) -> list[Any]:
            listed.append("second catalog")
            return [convert(None, MCPTool(**{**_DESCRIPTOR, "description": "UNREVIEWED"}))]

    class Spec(Client):
        async def fetch_tools(self) -> list[MCPTool]:
            listed.append("second catalog")
            return [MCPTool(**{**_DESCRIPTOR, "description": "UNREVIEWED"})]

        async def to_tool_list_async(self) -> list[Any]:
            tools = []
            for tool in await self.fetch_tools():
                converted.append(
                    copy.deepcopy(tool.descriptor)
                    if isinstance(tool, MCPTool)
                    else {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    }
                )
                tools.append(
                    types.SimpleNamespace(
                        metadata=types.SimpleNamespace(name=tool.name), async_fn=invoke
                    )
                )
            return tools

    class FunctionTool:
        @classmethod
        def from_defaults(cls, **kwargs: Any) -> Any:
            return kwargs

    class BetaTool:
        def __init__(self, func: Any, **kwargs: Any) -> None:
            self.func = func

    @asynccontextmanager
    async def session(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        class Session:
            async def list_tools(self) -> Any:
                listed.append("second catalog")
                return types.SimpleNamespace(
                    tools=[MCPTool(**{**_DESCRIPTOR, "description": "UNREVIEWED"})]
                )

        yield Session()

    modules = {
        "mcp.types": {"Tool": MCPTool},
        "langchain_mcp_adapters.client": {"MultiServerMCPClient": Client},
        "langchain_mcp_adapters.tools": {"convert_mcp_tool_to_langchain_tool": convert},
        "llama_index.core.tools": {"FunctionTool": FunctionTool},
        "llama_index.tools.mcp": {"BasicMCPClient": Client, "McpToolSpec": Spec},
        "anthropic.lib.tools": {"BetaAsyncFunctionTool": BetaTool},
        "anthropic.lib.tools.mcp": {"async_mcp_tool": lambda tool, session: convert(session, tool)},
    }
    for dotted, attrs in modules.items():
        parts = dotted.split(".")
        for i in range(1, len(parts) + 1):
            name = ".".join(parts[:i])
            if name not in modules:
                monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        module = types.ModuleType(dotted)
        for attr, value in attrs.items():
            setattr(module, attr, value)
        monkeypatch.setitem(sys.modules, dotted, module)
    monkeypatch.setattr(session_module, "open_session", session)
    monkeypatch.setattr(ToolSet, "_client_factory", lambda *args: lambda **kwargs: object())
    handle = McpServerHandle(
        ref=AssetRef("example", "inventory", "1.0.0"),
        endpoint_url="https://gateway.example.test/mcp/",
        governance=GovernanceReport(governed=True, checks=[]),
        tool_descriptors=(copy.deepcopy(_DESCRIPTOR),),
    )
    tools = ToolSet([handle], _auth=ClientIdAuth("id", "secret"), _lock=build_lock([handle]))
    if binding == "anthropic":
        async with tools.anthropic() as bound:
            assert len(bound) == 1
    else:
        assert len(await getattr(tools, binding)()) == 1
    expected = (
        {k: v for k, v in _DESCRIPTOR.items() if k in {"name", "description", "inputSchema"}}
        if binding == "llamaindex"
        else _DESCRIPTOR
    )
    assert converted == [expected]
    assert listed == []
