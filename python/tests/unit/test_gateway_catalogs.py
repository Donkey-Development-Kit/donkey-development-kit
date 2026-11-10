"""Gateway-visible catalog semantics from live captures (BG §2.7, ledger §9).

Listing is replayed at the MCP seam; approval invokers are stand-ins, not live
framework or gateway calls. The gateway owns tool mapping and tool search.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from donkey_kit import ApprovalRequired, Approvals, ConfigError
from donkey_kit.registry.criteria import GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.filter import ToolDescriptor
from donkey_kit.tools.session import ToolSet

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_policies"


def _catalog(name: str) -> tuple[dict[str, Any], ...]:
    capture = json.loads((_FIXTURES / f"{name}-list.json").read_text("utf-8"))
    payload = capture["body"].removeprefix("event: message\ndata: ").strip()
    return tuple(json.loads(payload)["result"]["tools"])


def _tools(
    catalog: tuple[dict[str, Any], ...] | None, approvals: Approvals | None = None
) -> ToolSet:
    return ToolSet(
        [
            McpServerHandle(
                ref=AssetRef("com.example", "inventory", "1.0.0"),
                endpoint_url="https://gateway.example.test/mcp/",
                governance=GovernanceReport(governed=True, checks=[]),
                tool_descriptors=catalog,
            )
        ],
        _auth=ClientIdAuth("test-id", "test-secret"),
        _approval_registry=approvals,
    )


@pytest.mark.parametrize("catalog", ["mapping", "progressive"])
async def test_describe_uses_the_gateway_catalog_unchanged(
    catalog: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = _catalog(catalog)
    calls: list[str] = []

    async def replay(url: str, headers: object, *, server: str) -> tuple[dict[str, Any], ...]:
        calls.append(url)
        return listed

    monkeypatch.setattr(session_module, "list_tools", replay)
    tools = _tools(None)
    assert tools.servers[0].tool_descriptors is None
    assert calls == []
    described = await tools.describe()
    assert described.servers[0].tool_descriptors == listed
    assert described.name_map == {tool["name"]: tool["name"] for tool in listed}
    assert await described.describe() is described
    assert len(calls) == 1


def test_mapping_filters_use_gateway_names_and_descriptions() -> None:
    tools = _tools(_catalog("mapping"))
    narrowed = tools.filter(
        allow=["inventory__arithmetic"],
        predicate=lambda tool: tool.description == "Synthetic capture arithmetic tool.",
    )
    assert narrowed.name_map == {"arithmetic": "arithmetic"}
    assert narrowed.descriptor_tokens() > 0
    with pytest.raises(ConfigError, match="calculate"):
        tools.filter(deny=["calculate"])


@pytest.mark.parametrize(
    ("catalog", "visible", "hidden", "arguments"),
    [
        ("mapping", "arithmetic", "calculate", {"expression": "6*7"}),
        ("progressive", "invoke_tool", "calculate", {"name": "calculate", "arguments": {}}),
    ],
)
async def test_approval_gates_the_visible_tool_not_an_upstream_name(
    catalog: str, visible: str, hidden: str, arguments: dict[str, Any]
) -> None:
    listed = _catalog(catalog)
    registry = Approvals()
    tools = _tools(listed, registry)
    with pytest.raises(ConfigError, match=hidden):
        await tools.require_approval(tools=[hidden]).describe()
    gated = await tools.require_approval(tools=[visible]).describe()
    descriptor = next(tool for tool in listed if tool["name"] == visible)
    calls: list[dict[str, Any]] = []

    def invoke(**kw: Any) -> str:
        calls.append(kw)
        return "called"

    call = gated._bind_tool(
        visible,
        ToolDescriptor("inventory", visible),
        invoke,
        schema=descriptor["inputSchema"],
    )
    with pytest.raises(ApprovalRequired) as caught:
        call(**arguments)
    assert caught.value.approval.tool == visible
    assert dict(caught.value.approval.arguments) == arguments
    assert calls == []
    assert registry.resolve(caught.value.approval.id, approved_by="tester") == "called"
    assert calls == [arguments]


@pytest.mark.parametrize("hot_tool", [False, True])
async def test_progressive_catalog_is_reported_before_filters(
    hot_tool: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    listed = _catalog("progressive")
    if hot_tool:
        listed += (_catalog("mapping")[1],)
    tools = _tools(listed).filter(deny=["search_tools"])
    with caplog.at_level(logging.INFO, logger=session_module.__name__):
        described = await tools.describe()
    assert described.name_map == (
        {"invoke_tool": "invoke_tool", "arithmetic": "arithmetic"}
        if hot_tool
        else {"invoke_tool": "invoke_tool"}
    )
    assert "search_tools / invoke_tool" in caplog.text
    assert "gateway-visible" in caplog.text
    assert "test-secret" not in caplog.text


@pytest.mark.parametrize("name", ["mapping", "progressive"])
async def test_ordinary_or_partial_catalog_is_not_reported_as_progressive(
    name: str, caplog: pytest.LogCaptureFixture
) -> None:
    listed = _catalog(name)
    if name == "progressive":
        listed = listed[:1]
    with caplog.at_level(logging.INFO, logger=session_module.__name__):
        await _tools(listed).describe()
    assert "search_tools / invoke_tool" not in caplog.text
