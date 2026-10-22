"""Registry value types (BG §2.7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .criteria import GovernanceReport

__all__ = ["AgentHandle", "AssetRef", "AssetType", "McpServerHandle"]

AssetType = Literal["mcp", "a2a-agent", "agent", "api"]


@dataclass(frozen=True)
class AssetRef:
    """``group_id/asset_id/version`` plus discovery metadata (BG §2.7).

    Accept a shorthand string form (``"com.acme/vendor-shipment-mcp/1.0.0"``)
    everywhere a ref is taken, via :meth:`parse`.
    """

    group_id: str
    asset_id: str
    version: str
    name: str | None = None
    type: AssetType | None = None
    tags: tuple[str, ...] = ()
    description: str | None = None
    exchange_url: str | None = None

    @classmethod
    def parse(cls, value: AssetRef | str) -> AssetRef:
        """Return ``value`` as an :class:`AssetRef`, parsing the string form.

        Raises:
            ValueError: The string is not ``group_id/asset_id/version``.
        """
        if isinstance(value, AssetRef):
            return value
        parts = value.split("/")
        if len(parts) != 3:
            raise ValueError(
                f"Asset ref {value!r} must be 'group_id/asset_id/version' "
                f"(e.g. 'com.acme/vendor-shipment-mcp/1.0.0')."
            )
        group_id, asset_id, version = parts
        return cls(group_id=group_id, asset_id=asset_id, version=version)

    @property
    def coordinates(self) -> str:
        """The ``group_id/asset_id/version`` string."""
        return f"{self.group_id}/{self.asset_id}/{self.version}"


@dataclass(frozen=True)
class McpServerHandle:
    """A resolved MCP server. Carries connection info; does NOT open a
    connection (BG §2.7 — connect on first tool use).

    ``governance`` is the ``registry.explain()`` verdict for the server, attached
    by discovery so an unfiltered ``ToolSet`` can still list an ungoverned server
    with its reason. ``None`` means it was never evaluated. ``ToolSet`` binds a
    server only when ``governance.governed`` is true (#1150, ADR 0013)."""

    ref: AssetRef
    endpoint_url: str
    transport: str = "streamable_http"
    auth_required: bool = True
    #: if exposed without a live connection
    tool_descriptors: tuple[dict[str, object], ...] | None = None
    governance: GovernanceReport | None = None


@dataclass(frozen=True)
class AgentHandle:
    """A resolved A2A-compliant agent (BG §2.9)."""

    ref: AssetRef
    endpoint_url: str
    skills: tuple[str, ...] = field(default_factory=tuple)
