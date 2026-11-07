"""``Donkey.simulate_mcp()``: declared servers, discovered and bound offline (#1391, ADR 0023)."""

from __future__ import annotations

import pytest
from test_tool_binding import BINDINGS, _require

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import PIIDetected, RegistryError
from donkey_kit.core.tool_access import ToolNotInProfile

pytestmark = [pytest.mark.disable_socket, pytest.mark.timeout(15)]

_REF = "acme/crm-tools/1.0.0"
_SERVERS = {_REF: [{"name": "lookup_customer", "description": "Find a customer"}]}


def _donkey() -> Donkey:
    return Donkey(DonkeyConfig(telemetry=False))


def _messages(error: BaseException) -> list[str]:
    pending, seen = [error], []
    while pending:
        each = pending.pop()
        seen.append(str(each))
        pending.extend(getattr(each, "exceptions", ()))
    return seen


async def test_discover_inside_block_binds_declared_server_and_injects() -> None:
    _require(BINDINGS["langgraph"])
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        tools = await donkey.tools.discover()
        assert [s.ref.coordinates for s in tools.servers] == [_REF]
        (tool,) = await tools.langgraph()
        assert tool.name == "lookup_customer"
        with pytest.raises(PIIDetected) as caught:
            await tool.ainvoke({})
        assert caught.value.policy == "mcp-pii-detector"
        assert "simulated result of lookup_customer" in str(await tool.ainvoke({}))


async def test_times_counts_injected_calls_and_call_tool_answers_the_rest() -> None:
    _require(BINDINGS["langgraph"])
    donkey = _donkey()
    answered = []

    def call_tool(name, arguments):
        answered.append(name)
        return {"content": [{"type": "text", "text": "custom"}]}

    with donkey.simulate_mcp("pii-request", servers=_SERVERS, times=2, call_tool=call_tool):
        (tool,) = await (await donkey.tools.discover()).langgraph()
        for _ in range(2):
            with pytest.raises(PIIDetected):
                await tool.ainvoke({})
        assert "custom" in str(await tool.ainvoke({}))
    assert answered == ["lookup_customer"]


async def test_two_declared_servers_route_to_their_own_tools() -> None:
    _require(BINDINGS["langgraph"])
    donkey = _donkey()
    servers = {**_SERVERS, "acme/billing/2.0.0": [{"name": "refund"}]}
    with donkey.simulate_mcp("global-access", servers=servers):
        bound = {t.name: t for t in await (await donkey.tools.discover()).langgraph()}
        assert set(bound) == {"lookup_customer", "refund"}
        with pytest.raises(ToolNotInProfile):
            await bound["refund"].ainvoke({})
        assert "simulated result of lookup_customer" in str(
            await bound["lookup_customer"].ainvoke({})
        )


async def test_binding_fails_closed_after_the_block() -> None:
    _require(BINDINGS["langgraph"])
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        (tool,) = await (await donkey.tools.discover()).langgraph()
    with pytest.raises(Exception) as caught:
        await tool.ainvoke({})
    assert any("MCP replay scope is closed" in m for m in _messages(caught.value))


async def test_live_tools_answers_from_a_declared_server_only() -> None:
    _require(BINDINGS["langgraph"])
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        (tool,) = await donkey.tools.live_tools(_REF)
        assert tool["name"] == "lookup_customer"
        with pytest.raises(RegistryError, match="not one of the servers"):
            await donkey.tools.live_tools("acme/other/1.0.0")
