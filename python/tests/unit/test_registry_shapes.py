"""Fixture-driven validation of the registry value types against real data.

The fixtures under ``tests/fixtures/a2d/`` are faithful captures from the A2D
platform's MCP tools (see that directory's README). These tests prove the SDK's
``McpServerHandle`` / ``ToolDescriptor`` / ``AssetRef`` types can represent
real-world shapes (BG §1.5) — WITHOUT wiring the (still-blocked) ``ExchangeRegistry``
to any live endpoint. The raw→domain mapping lives here in the test, not in the
SDK, precisely because the direct Anypoint REST contract is still UNVERIFIED
(verification discipline); only the shapes are confirmed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from donkey_kit import AssetRef, AssetType, PublicationAssetType
from donkey_kit.registry.models import McpServerHandle
from donkey_kit.tools.filter import ToolDescriptor, resolve_collisions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "a2d"

# Observed transport kinds → the SDK's normalized transport identifier.
_TRANSPORT_NORMALIZATION = {"streamableHttp": "streamable_http"}


def test_package_root_asset_types_share_canonical_values() -> None:
    discovery_type: AssetType = "mcp"
    ref = AssetRef(group_id="com.acme", asset_id="tools", version="1.0.0", type=discovery_type)

    assert ref.type == "mcp"
    assert PublicationAssetType.MCP_SERVER.value == ref.type
    assert PublicationAssetType.A2A_AGENT.value == "a2a-agent"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _endpoint_for(environments: list[dict], asset_id: str, env_type: str) -> str:
    for env in environments:
        if (
            env["asset_type"] == "mcp_server"
            and env["asset_id"] == asset_id
            and env["environment_type"] == env_type
        ):
            return env["base_url"]
    raise LookupError(f"no mcp_server env for {asset_id} / {env_type}")


def test_mcp_server_spec_maps_to_handle() -> None:
    spec = _load("mcp_server_spec.account_management.json")["spec"]
    environments = _load("environments.sample.json")["environments"]

    # A production mcp_server endpoint exists in the captured environments.
    asset_id = "370fbb9f-f0f5-400b-b120-19c7ee72dcb7"
    endpoint = _endpoint_for(environments, asset_id, "prod")

    handle = McpServerHandle(
        ref=AssetRef(group_id="a2d", asset_id=asset_id, version="prod"),
        endpoint_url=endpoint,
        transport=_TRANSPORT_NORMALIZATION[spec["transport"]["kind"]],
        auth_required=False,  # captured envs report auth_type=null
        tool_descriptors=tuple(spec["tools"]),
    )

    assert handle.transport == "streamable_http"
    assert handle.endpoint_url.endswith(f"/api/platform/{asset_id}/mcp")
    assert handle.tool_descriptors is not None
    assert len(handle.tool_descriptors) == 3
    # Every captured tool carries the MCP-standard name/description/inputSchema.
    for tool in handle.tool_descriptors:
        assert set(tool) >= {"name", "description", "inputSchema"}
        assert tool["inputSchema"]["type"] == "object"


def test_spec_tools_become_filterable_descriptors() -> None:
    spec = _load("mcp_server_spec.account_management.json")["spec"]
    descriptors = [
        ToolDescriptor(server="account-management", name=t["name"], description=t["description"])
        for t in spec["tools"]
    ]
    exposed, name_map = resolve_collisions(descriptors)

    # No collisions within one server → names are unprefixed and round-trip.
    assert set(name_map.values()) == {"listAccounts", "getAccount", "createAccount"}
    assert exposed["getAccount"].description == "Get account by ID"


def test_a2d_uuid_identity_is_not_a_maven_ref() -> None:
    """FINDING: A2D assets are identified by a bare UUID, but AssetRef.parse
    expects Anypoint Maven coordinates (group/asset/version). The resolver that
    bridges the two is a real design gap recorded in docs/verified-apis.md."""
    server = _load("mcp_servers.list.json")["servers"][0]
    with pytest.raises(ValueError):
        AssetRef.parse(server["id"])  # e.g. "00a87cda-f938-46ba-..."
