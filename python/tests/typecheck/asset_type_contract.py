"""Static contract for package-root asset-type imports (#463)."""

from __future__ import annotations

from donkey_kit import AssetRef, AssetType, Publication, PublicationAssetType
from donkey_kit.registry import ExchangeRegistry

discovery_type: AssetType = "mcp"
publication_type: AssetType = PublicationAssetType.MCP_SERVER.value
asset_ref = AssetRef(
    group_id="com.acme", asset_id="tools", version="1.0.0", type=discovery_type
)

publication = Publication(
    asset_type=PublicationAssetType.MCP_SERVER,
    group_id="com.acme",
    asset_id="tools",
    version="1.0.0",
    name="Tools",
    description="Tools for static contract verification.",
)


async def search_by_public_asset_type(registry: ExchangeRegistry) -> None:
    await registry.search(asset_types=[discovery_type])
