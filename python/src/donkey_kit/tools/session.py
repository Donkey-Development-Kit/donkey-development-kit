"""MCP session management + the ``ToolSet`` facade (BG §2.7).

``ToolSet`` wraps N ``McpServerHandle``s. Filtering and collision resolution are
pure and implemented here. The per-framework binding methods return each
framework's NATIVE tool type (BG §2.7) and are gated on verification of the MCP
binding class names (docs/verified-apis.md §9) — connections open on first tool
use, never in ``discover()`` (BG §2.7).

Binding refuses a set holding any server that is not governed, before anything
else (#1150, ADR 0013): the governed transport attaches the consumer's gateway
credentials to every request, so they must never reach a server the gateway
does not front. There is no opt-out.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..core import _verify
from ..core.errors import DonkeyError
from ..registry.models import McpServerHandle
from .filter import ToolDescriptor, ToolFilter, resolve_collisions

__all__ = ["ToolSet", "UngovernedMcpServer"]

_log = logging.getLogger(__name__)


class UngovernedMcpServer(DonkeyError):
    """A ``ToolSet`` binding was refused because a server in it is not governed
    (#1150, ADR 0013).

    Raised locally, before any connection opens: the governed transport attaches
    the consumer's gateway credentials to every request, so binding a server the
    gateway does not front would send them to that server, with no policy, access
    profile or kill switch in the path. A server whose governance was never
    evaluated is refused too.

    Carries ``servers`` (the ``group_id/asset_id/version`` of each refused server)
    and ``reasons`` (``coordinates: check: detail`` for every check that did not
    pass, from ``registry.explain()``)."""

    remediation: str = (
        "Front the server with the gateway so its policies apply, or leave it out "
        "of the set with donkey.tools.discover(governed=True). Run "
        "donkey.registry.explain(ref) to see which governance check failed."
    )

    def __init__(
        self,
        message: str,
        *,
        servers: tuple[str, ...] = (),
        reasons: tuple[str, ...] = (),
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.servers = servers
        self.reasons = reasons


class ToolSet:
    """A governed set of MCP tools, from ``donkey.tools.discover()`` (BG §2.7).

    :meth:`filter` narrows it and :attr:`name_map` explains collision renames.
    The per-framework binding methods (:meth:`langgraph`, :meth:`adk`, ...)
    return each framework's native tool objects; they raise
    ``NotImplementedError`` for now (docs/verified-apis.md §9). Each first raises
    :class:`UngovernedMcpServer` if any server in the set
    is not governed (ADR 0013).

    Docs: https://docs.donkey-kit.dev/tool-access/binding
    """

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

    def _require_governed(self) -> None:
        """Refuse to bind unless every server is governed (#1150, ADR 0013).

        Runs before any connection or credential exists, so a refused server
        never receives a request. A server with no verdict is refused too."""
        refused = [s for s in self._servers if s.governance is None or not s.governance.governed]
        if not refused:
            return
        servers = tuple(s.ref.coordinates for s in refused)
        reasons = tuple(
            f"{s.ref.coordinates}: {reason}"
            for s in refused
            for reason in (
                (s.governance.reasons_failed() or ["governance: not governed"])
                if s.governance is not None
                else ["governance: not evaluated"]
            )
        )
        raise UngovernedMcpServer(
            f"Refusing to bind {len(refused)} MCP server(s) that are not governed: "
            f"{', '.join(servers)}. No gateway credentials were sent.",
            servers=servers,
            reasons=reasons,
        )

    # ---- per-framework binding (native types BG §2.7, gated on verification discipline,
    # docs/verified-apis.md §9) ------
    def langgraph(self) -> list[object]:  # -> list[BaseTool]
        """Bind the tool set as LangChain ``BaseTool`` objects.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked(
            "langchain_mcp_adapters.client.MultiServerMCPClient binding (docs/verified-apis.md §9)."
        )

    def adk(self) -> list[object]:  # -> list[McpToolset]
        """Bind the tool set as Google ADK ``McpToolset`` objects.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked(
            "ADK McpToolset / StreamableHTTPConnectionParams (docs/verified-apis.md §9)."
        )

    def strands(self) -> list[object]:  # -> list[MCPClient]
        """Bind the tool set as Strands ``MCPClient`` objects.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked(
            "Strands MCPClient(streamablehttp_client(...)) (docs/verified-apis.md §9)."
        )

    def llamaindex(self) -> list[object]:  # -> list[FunctionTool]
        """Bind the tool set as LlamaIndex ``FunctionTool`` objects.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked("LlamaIndex BasicMCPClient + McpToolSpec (docs/verified-apis.md §9).")

    def openai(self) -> list[object]:  # -> list[agents.mcp.MCPServer]
        """Bind the tool set as OpenAI Agents SDK MCP server objects.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked(
            "OpenAI Agents SDK agents.mcp.MCPServerStreamableHttp (docs/verified-apis.md §9)."
        )

    def anthropic(self) -> list[object]:  # -> list[MCP tool defs]
        """Bind the tool set as Anthropic SDK MCP tool definitions.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked(
            "Anthropic SDK MCP tool / mcp_servers binding (docs/verified-apis.md §9)."
        )

    def crewai(self) -> list[object]:  # -> list[crewai BaseTool]
        """Bind the tool set as CrewAI ``BaseTool`` objects.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked(
            "CrewAI crewai_tools.MCPServerAdapter binding (docs/verified-apis.md §9)."
        )

    def agent_framework(self) -> list[object]:
        """Bind the tool set as Microsoft Agent Framework tool objects.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §9.
        """
        self._require_governed()
        raise _verify.blocked("Agent Framework MCP client/tool class (docs/verified-apis.md §9).")
