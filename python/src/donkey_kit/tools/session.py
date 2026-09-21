"""MCP session management + the ``ToolSet`` facade (BG §2.7).

``ToolSet`` wraps N ``McpServerHandle``s. Filtering and collision resolution are
pure and implemented here. The per-framework binding methods return each
framework's NATIVE tool type (BG §2.7) and are gated on M0 verification of the MCP
binding class names (docs/verified-apis.md §9) — connections open on first tool
use, never in ``discover()`` (BG §2.7).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..core import _verify
from ..registry.models import McpServerHandle
from .filter import ToolDescriptor, ToolFilter, resolve_collisions

_log = logging.getLogger("donkey_kit.tools")


class ToolSet:
    def __init__(
        self,
        servers: list[McpServerHandle],
        *,
        _filter: ToolFilter | None = None,
    ) -> None:
        self._servers = list(servers)
        self._filter = _filter if _filter is not None else ToolFilter()

    # ---- filtering (pure, implemented) ------------------------------------
    def filter(
        self,
        *,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        predicate: Callable[[ToolDescriptor], bool] | None = None,
    ) -> ToolSet:
        """Return a filtered view. Enterprise MCP servers can expose dozens of
        tools; handing 60 descriptors to a model degrades it and inflates token
        cost (BG §2.7)."""

        return ToolSet(
            self._servers,
            _filter=ToolFilter(
                allow=frozenset(allow) if allow is not None else None,
                deny=frozenset(deny or ()),
                predicate=predicate,
            ),
        )

    @property
    def name_map(self) -> dict[str, str]:
        """``exposed_name -> original_name`` so a developer can debug why the
        model called ``hr__get_employee`` (BG §2.7)."""
        exposed, name_map = resolve_collisions(self._descriptors())
        return name_map

    def _descriptors(self) -> list[ToolDescriptor]:
        descs: list[ToolDescriptor] = []
        for server in self._servers:
            short = server.ref.asset_id
            for raw in server.tool_descriptors or ():
                description = raw.get("description")
                raw_tags = raw.get("tags")
                descs.append(
                    ToolDescriptor(
                        server=short,
                        name=str(raw.get("name")),
                        description=description if isinstance(description, str) else None,
                        tags=tuple(str(t) for t in raw_tags)
                        if isinstance(raw_tags, (list, tuple))
                        else (),
                    )
                )
        kept = [d for d in descs if self._filter.accepts(d)]
        _log.debug("ToolSet exposes %d of %d tool descriptors", len(kept), len(descs))
        return kept

    # ---- per-framework binding (native types BG §2.7, gated on verification discipline,
    # docs/verified-apis.md §9) ------
    def langgraph(self) -> list[object]:  # -> list[BaseTool]
        raise _verify.blocked(
            "langchain_mcp_adapters.client.MultiServerMCPClient binding "
            "(docs/verified-apis.md §9)."
        )

    def adk(self) -> list[object]:  # -> list[McpToolset]
        raise _verify.blocked(
            "ADK McpToolset / StreamableHTTPConnectionParams (docs/verified-apis.md §9)."
        )

    def strands(self) -> list[object]:  # -> list[MCPClient]
        raise _verify.blocked(
            "Strands MCPClient(streamablehttp_client(...)) (docs/verified-apis.md §9)."
        )

    def llamaindex(self) -> list[object]:  # -> list[FunctionTool]
        raise _verify.blocked("LlamaIndex BasicMCPClient + McpToolSpec (docs/verified-apis.md §9).")

    def openai(self) -> list[object]:  # -> list[agents.mcp.MCPServer]
        raise _verify.blocked(
            "OpenAI Agents SDK agents.mcp.MCPServerStreamableHttp (docs/verified-apis.md §9)."
        )

    def anthropic(self) -> list[object]:  # -> list[MCP tool defs]
        raise _verify.blocked(
            "Anthropic SDK MCP tool / mcp_servers binding (docs/verified-apis.md §9)."
        )

    def crewai(self) -> list[object]:  # -> list[crewai BaseTool]
        raise _verify.blocked(
            "CrewAI crewai_tools.MCPServerAdapter binding (docs/verified-apis.md §9)."
        )

    def agent_framework(self) -> list[object]:
        raise _verify.blocked("Agent Framework MCP client/tool class (docs/verified-apis.md §9).")
