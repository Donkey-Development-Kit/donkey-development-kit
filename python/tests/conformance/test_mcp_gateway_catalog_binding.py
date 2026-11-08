"""Native binding of captured gateway catalogs (#1389, BG §2.7, ledger §9).

The Tool Mapping and Progressive Disclosure ``tools/list`` captures are declared
through ``donkey.simulate_mcp()`` and bound by the real LangGraph adapter: the
binding exposes the gateway-visible names, nothing is translated or searched
client-side, and the gateway owns mapping and tool search.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_tool_binding import BINDINGS, _require

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig

pytestmark = [pytest.mark.disable_socket, pytest.mark.timeout(15)]

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_policies"
_REF = "acme/inventory/1.0.0"


def _catalog(name: str) -> list[dict[str, Any]]:
    capture = json.loads((_FIXTURES / f"{name}-list.json").read_text("utf-8"))
    payload = capture["body"].removeprefix("event: message\ndata: ").strip()
    return list(json.loads(payload)["result"]["tools"])


@pytest.mark.parametrize("catalog", ["mapping", "progressive"])
async def test_langgraph_binds_the_gateway_visible_names(catalog: str) -> None:
    _require(BINDINGS["langgraph"])
    listed = _catalog(catalog)
    donkey = Donkey(DonkeyConfig(telemetry=False))
    with donkey.simulate_mcp("global-access", servers={_REF: listed}):
        bound = await (await donkey.tools.discover()).langgraph()
        assert sorted(t.name for t in bound) == sorted(t["name"] for t in listed)
        by_name = {t.name: t for t in bound}
        for descriptor in listed:
            assert by_name[descriptor["name"]].description == descriptor["description"]


async def test_mapped_upstream_name_is_not_bound() -> None:
    _require(BINDINGS["langgraph"])
    donkey = Donkey(DonkeyConfig(telemetry=False))
    with donkey.simulate_mcp("global-access", servers={_REF: _catalog("mapping")}):
        tools = await donkey.tools.discover()
        names = {t.name for t in await tools.langgraph()}
        assert "calculate" not in names
        assert "arithmetic" in names
