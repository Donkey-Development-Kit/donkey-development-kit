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

``require_approval()`` gates named tools behind a human decision, the same
approval ``@donkey.tool(approval="required")`` gives a local function (#1153).
Each binding applies :meth:`ToolSet._bind_tool` to its per-tool invoker; the
rules and the gate live in ``tools/approval.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..core import _verify
from ..core.approvals import APPROVAL_TIMEOUT, NativePause, parse_timeout
from ..core.errors import DonkeyError
from ..core.reactions import Duration
from ..registry.models import McpServerHandle
from .approval import ApprovalRule, check_rules, gated_tool, rule_for
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

    :meth:`filter` narrows it, :meth:`require_approval` gates named tools behind
    a human decision, and :attr:`name_map` explains collision renames.
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
        _approvals: tuple[ApprovalRule, ...] = (),
        _natives: tuple[NativePause, ...] = (),
    ) -> None:
        self._servers = list(servers)
        self._filter = _filter if _filter is not None else ToolFilter()
        self._approvals = _approvals
        # Framework pauses for a gated call, such as the LangGraph interrupt()
        # hook. Passed in by discovery: tools/ cannot import integrations/.
        self._natives = _natives

    def _with(
        self,
        *,
        _filter: ToolFilter | None = None,
        _approvals: tuple[ApprovalRule, ...] | None = None,
    ) -> ToolSet:
        return ToolSet(
            self._servers,
            _filter=_filter if _filter is not None else self._filter,
            _approvals=_approvals if _approvals is not None else self._approvals,
            _natives=self._natives,
        )

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

        return self._with(
            _filter=ToolFilter(
                allow=frozenset(allow) if allow is not None else None,
                deny=frozenset(deny or ()),
                predicate=predicate,
            ),
        )

    # ---- approval (pure, implemented; applied by binding, #1153) ------------
    def require_approval(
        self,
        *,
        tools: Sequence[str],
        risk: str | None = None,
        timeout: Duration = APPROVAL_TIMEOUT,
    ) -> ToolSet:
        """Return a view whose named tools need a human approval before they run
        (BG §2.3, #1153), as ``@donkey.tool(approval="required")`` does for a
        local function. A gated call raises
        :class:`~donkey_kit.core.approvals.ApprovalRequired`, or pauses a
        LangGraph graph with ``interrupt()``, and resolves through
        ``donkey.approvals``.

        A bare name (``"issue_refund"``) gates that tool on every server; the
        exposed form (``"billing__issue_refund"``) gates one server's tool. Rules
        accumulate, ``filter()`` keeps them, and when several match a tool the
        last one sets its ``risk`` and ``timeout``. Binding refuses a name no
        server exposes, so a typo never leaves a tool ungated. The approval is
        client-side and advisory until gateway step-up (#214).

        Args:
            tools: Exact tool names to gate.
            risk: A label for the approver and the span, such as ``"financial"``.
            timeout: How long a pending approval waits for a decision.

        Raises:
            ValueError: ``tools`` is empty or names a blank tool, ``risk`` is
                blank, or ``timeout`` is not a positive duration.
        """
        if isinstance(tools, str):
            raise ValueError(f"tools must be a list of tool names, not the string {tools!r}")
        names = frozenset(tools)
        if not names or not all(isinstance(n, str) and n.strip() for n in names):
            raise ValueError(f"tools must name at least one tool, not {list(tools)!r}")
        if risk is not None and (not isinstance(risk, str) or not risk.strip()):
            raise ValueError(f"risk must be a non-empty label such as 'financial', not {risk!r}")
        rule = ApprovalRule(tools=names, risk=risk, timeout=parse_timeout(timeout))
        return self._with(_approvals=(*self._approvals, rule))

    def _bind_tool(
        self,
        exposed_name: str,
        tool: ToolDescriptor,
        invoke: Callable[..., Any],
        *,
        schema: Mapping[str, Any] | None = None,
    ) -> Callable[..., Any]:
        """The invoker a binding hands its framework for one tool: ``invoke``
        itself, or ``invoke`` behind the approval gate when a rule matches
        ``tool`` (#1153). Bindings call :meth:`_check_approvals` once first."""
        rule = rule_for(self._approvals, tool)
        if rule is None:
            return invoke
        return gated_tool(exposed_name, invoke, rule=rule, schema=schema, natives=self._natives)

    def _check_approvals(self) -> None:
        """Refuse a ``require_approval()`` name that matches no tool on any
        server, checked against every tool before ``filter()`` (#1153)."""
        check_rules(self._approvals, self._descriptors(filtered=False))

    @property
    def name_map(self) -> dict[str, str]:
        """``exposed_name -> original_name`` so a developer can debug why the
        model called ``hr__get_employee`` (BG §2.7)."""
        exposed, name_map = resolve_collisions(self._descriptors())
        return name_map

    def _descriptors(self, *, filtered: bool = True) -> list[ToolDescriptor]:
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
        if not filtered:
            return descs
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
