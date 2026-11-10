"""Registry value types (BG §2.7)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from .criteria import GovernanceReport

__all__ = ["AgentHandle", "AssetRef", "AssetType", "LlmMetadata", "McpServerHandle"]

AssetType = Literal["mcp", "a2a-agent", "agent", "api", "llm"]


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
    server only when ``governance.governed`` is true (#1150, ADR 0017)."""

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


@dataclass(frozen=True)
class LlmMetadata:
    """What an Exchange ``llm`` asset version's ``llm-metadata`` file declares
    (docs/verified-apis.md §7, #1533): an LLM proxy's, or a custom LLM
    provider's.

    Read tolerantly: a field the file leaves out, or sets to a value of the
    wrong type, is ``None`` or empty, and fields the SDK does not read are
    ignored. A DDK LLM proxy's file sets only ``platform``.
    """

    platform: str | None
    name: str | None = None
    #: ``openai``, ``anthropic`` or ``gemini``: the wire format the platform takes.
    input_format: str | None = None
    #: The model IDs, from the plain-string entries and the ``id`` of the object ones.
    models: tuple[str, ...] = ()
    #: The platform's ``capabilities`` object as published, such as ``toolCalling``.
    capabilities: Mapping[str, object] = field(default_factory=dict)
