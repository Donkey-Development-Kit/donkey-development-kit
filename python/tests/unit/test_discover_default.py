"""``donkey.tools.discover()`` is governed by default (#1424, ADR 0017).

A mixed catalog, one governed and one ungoverned MCP server, through the public
``ToolsFacade`` and its defaults. The default set holds only the governed server
and binds; ``governed=None`` lists both, and binding still refuses the set before
any listing (ADR 0013's guard, kept by ADR 0017). The registry is a fake that
answers ``search``/``explain``/``resolve_mcp``, and ``list_tools`` is replaced so
no MCP server is needed (``test_discover.py`` covers ``Discovery`` itself).
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import Any

import pytest

from donkey_kit import DonkeyConfig, ToolsFacade, UngovernedMcpServer
from donkey_kit.registry.criteria import Check, GovernanceCriteria, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import discovery as discovery_module
from donkey_kit.tools import session as session_module

_CFG = DonkeyConfig(telemetry=False, llm_proxy_client_id="cid", llm_proxy_client_secret="secret")
_VERDICTS = {
    "hr": GovernanceReport(governed=True, checks=[Check("deployed", True, "gateway gw-1")]),
    "fastmcp-crm": GovernanceReport(
        governed=False, checks=[Check("api_instance_exists", False, "no instance in Sandbox")]
    ),
}


class _Registry:
    """The mixed catalog: ``hr`` is governed, ``fastmcp-crm`` is not."""

    def __init__(self) -> None:
        self.searches = 0

    def refresh(self) -> None:
        pass

    async def search(self, **_: Any) -> list[AssetRef]:
        self.searches += 1
        return [AssetRef("com.acme", asset_id, "1.0.0") for asset_id in _VERDICTS]

    async def explain(self, ref: AssetRef, *, criteria: GovernanceCriteria) -> GovernanceReport:
        return _VERDICTS[ref.asset_id]

    async def resolve_mcp(self, ref: AssetRef) -> McpServerHandle:
        return McpServerHandle(ref=ref, endpoint_url=f"https://gw.example.test/{ref.asset_id}/")


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The URL of every server a binding listed tools from."""
    urls: list[str] = []

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        urls.append(url)
        return ({"name": "lookup", "inputSchema": {"type": "object"}},)

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    return urls


def _tools(registry: _Registry | None = None) -> ToolsFacade:
    return ToolsFacade(registry or _Registry(), _CFG, None)  # type: ignore[arg-type]


async def test_the_default_set_holds_only_the_governed_server_and_binds(
    listed: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=discovery_module.__name__):
        ts = await _tools().discover()

    assert [s.ref.asset_id for s in ts.servers] == ["hr"]
    described = await ts.describe()
    assert listed == ["https://gw.example.test/hr/"]
    assert described.name_map == {"lookup": "lookup"}
    (line,) = [r.getMessage() for r in caplog.records if "left out" in r.getMessage()]
    assert "com.acme/fastmcp-crm/1.0.0" in line
    assert "api_instance_exists: no instance in Sandbox" in line
    assert not any("is unfiltered" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("governed", [None, False])
async def test_an_unfiltered_set_lists_both_and_binding_still_refuses(
    listed: list[str], governed: bool | None
) -> None:
    ts = await _tools().discover(governed=governed)

    assert [s.ref.asset_id for s in ts.servers] == ["hr", "fastmcp-crm"]
    with pytest.raises(UngovernedMcpServer) as caught:
        await ts.describe()
    assert caught.value.servers == ("com.acme/fastmcp-crm/1.0.0",)
    # Each binding method refuses before importing its framework or listing.
    with pytest.raises(UngovernedMcpServer):
        await ts.langgraph()
    assert listed == []


async def test_a_default_warm_fills_the_entry_a_default_discover_reads() -> None:
    registry = _Registry()
    tools = _tools(registry)

    assert await tools.warm() == 1
    ts = await tools.discover()

    assert registry.searches == 1
    assert [s.ref.asset_id for s in ts.servers] == ["hr"]


def test_discover_and_warm_default_to_governed() -> None:
    for method in (ToolsFacade.discover, ToolsFacade.warm):
        assert inspect.signature(method).parameters["governed"].default is True
