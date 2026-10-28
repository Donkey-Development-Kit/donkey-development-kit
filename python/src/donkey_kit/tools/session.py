"""MCP session management + the ``ToolSet`` facade (BG §2.7).

``ToolSet`` wraps N ``McpServerHandle``s. Filtering and collision resolution are
pure and implemented here. The per-framework binding methods return each
framework's NATIVE tool type (BG §2.7), built on the framework's own MCP class
as the local round-trip in ``scripts/mcp_binding`` verified it
(docs/verified-apis.md §9). Connections open when a set is described or bound,
never in ``discover()`` (BG §2.7).

Binding refuses a set holding any server that is not governed, before anything
else (#1150, ADR 0013): the governed transport attaches the consumer's gateway
credentials to every request, so they must never reach a server the gateway
does not front. There is no opt-out.

Every binding logs the tool count and :meth:`ToolSet.descriptor_tokens` at
debug level, and warns when either passes ``tool_warn_count`` or
``tool_warn_tokens`` (#1257; the defaults' sources are on :func:`_report_cost`).

``require_approval()`` gates named tools behind a human decision, the same
approval ``@donkey.tool(approval="required")`` gives a local function (#1153).
A per-tool binding (LangGraph, LlamaIndex, Anthropic) applies
:meth:`ToolSet._bind_tool` to each gated tool's invoker; a whole-server binding
refuses a gate. The rules and the gate live in ``tools/approval.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any

from ..core import _verify
from ..core.approvals import APPROVAL_TIMEOUT, NativePause, parse_timeout
from ..core.config import DonkeyConfig
from ..core.errors import AuthError, ConfigError, DonkeyError
from ..core.reactions import Duration
from ..registry.models import McpServerHandle
from .approval import ApprovalRule, check_rules, gated_tool, rule_for
from .auth import McpAuth
from .filter import (
    ToolDescriptor,
    ToolFilter,
    check_patterns,
    descriptor_tokens,
    resolve_collisions,
)
from .mcp import list_tools, open_session

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


@dataclasses.dataclass(frozen=True)
class _Tool:
    """One tool a binding binds: the name the model sees, its descriptor and the
    approval rule that gates it, if any."""

    exposed: str
    desc: ToolDescriptor
    rule: ApprovalRule | None


class ToolSet:
    """A governed set of MCP tools, from ``donkey.tools.discover()`` (BG §2.7).

    :meth:`filter` narrows it, :meth:`descriptor_tokens` estimates what its
    descriptors cost a model call, :meth:`require_approval` gates named tools
    behind a human decision, and :attr:`name_map` explains collision renames.
    The per-framework binding methods (:meth:`langgraph`, :meth:`adk`, ...)
    return each framework's native tool objects (docs/verified-apis.md §9).
    Each first raises
    :class:`UngovernedMcpServer` if any server in the set is not governed
    (ADR 0013), then lists the tools with :meth:`describe`.

    Docs: https://docs.donkey-kit.dev/tool-access/binding
    """

    def __init__(
        self,
        servers: list[McpServerHandle],
        *,
        _filters: tuple[ToolFilter, ...] = (),
        _approvals: tuple[ApprovalRule, ...] = (),
        _natives: tuple[NativePause, ...] = (),
        _auth: McpAuth | None = None,
        _discovered: tuple[datetime, float, float] | None = None,
        _limits: tuple[int, int] | None = None,
    ) -> None:
        self._servers = list(servers)
        # Every filter() call so far: a tool is exposed only if all accept it,
        # so a later call can narrow the set but never widen it (#222).
        self._filters = _filters
        self._approvals = _approvals
        # Framework pauses for a gated call, such as the LangGraph interrupt()
        # hook. Passed in by discovery: tools/ cannot import integrations/.
        self._natives = _natives
        # The gateway credential every binding sends (tools/auth.py); None when
        # none is configured, which only binding refuses.
        self._auth = _auth
        # (wall-clock time, monotonic time, TTL in seconds) of the discovery.
        self._discovered = _discovered
        # (tool_warn_count, tool_warn_tokens) from the config discovery read; a
        # set built by hand gets the DonkeyConfig defaults.
        self._limits = _limits or (DonkeyConfig.tool_warn_count, DonkeyConfig.tool_warn_tokens)

    def _with(
        self,
        *,
        _servers: list[McpServerHandle] | None = None,
        _filters: tuple[ToolFilter, ...] | None = None,
        _approvals: tuple[ApprovalRule, ...] | None = None,
    ) -> ToolSet:
        return ToolSet(
            _servers if _servers is not None else self._servers,
            _filters=_filters if _filters is not None else self._filters,
            _approvals=_approvals if _approvals is not None else self._approvals,
            _natives=self._natives,
            _auth=self._auth,
            _discovered=self._discovered,
            _limits=self._limits,
        )

    @property
    def servers(self) -> tuple[McpServerHandle, ...]:
        """The MCP servers in the set, each with its ``governance`` verdict."""
        return tuple(self._servers)

    # ---- staleness (discovery is cached, #221) ------------------------------
    @property
    def discovered_at(self) -> datetime | None:
        """When ``discover()`` read the registry for this set (UTC), or ``None``
        for a set built by hand. A cached ``discover()`` returns the same time."""
        return self._discovered[0] if self._discovered else None

    @property
    def is_stale(self) -> bool:
        """True once the set is older than ``registry_cache_ttl_s``: the next
        ``discover()`` call reads the registry again. A set built by hand is never stale."""
        if self._discovered is None:
            return False
        _, started, ttl = self._discovered
        return time.monotonic() - started >= ttl

    # ---- filtering (pure, implemented) ------------------------------------
    def filter(
        self,
        *,
        allow: list[str] | None = None,
        deny: list[str] | None = None,
        predicate: Callable[[ToolDescriptor], bool] | None = None,
        servers: list[str] | None = None,
    ) -> ToolSet:
        """Return a narrower view. Enterprise MCP servers can expose dozens of
        tools; handing 60 descriptors to a model degrades it and inflates token
        cost (BG §2.7).

        ``allow`` and ``deny`` take exact names or glob patterns (``get_*``),
        matched against the bare tool name and the server-qualified one
        (``hr__get_employee``, ``hr__*``); deny wins. Calls chain and only
        narrow: ``tools.filter(allow=["get_*"]).filter(deny=["get_ssn"])``
        exposes the ``get_`` tools except ``get_ssn``, and no call can bring
        back a tool an earlier one removed (#222).

        ``servers`` keeps only the servers whose asset ID (``hr``) or
        coordinates match an entry, before any tool is listed: a server it
        drops is never connected to and never receives the credential. Use it
        when the credential is good for one server only, such as a JWT whose
        audience is that server's instance.

        Args:
            allow: Keep only the tools that match an entry.
            deny: Drop the tools that match an entry.
            predicate: Keep only the tools it returns true for.
            servers: Keep only the servers that match an entry.

        Raises:
            ValueError: ``allow``, ``deny`` or ``servers`` is a string, not a list.
            ConfigError: A ``servers`` entry matches no server in the set, or
                an ``allow`` or ``deny`` entry matches no tool any server
                exposes, so a typo never empties the set or leaves a tool
                exposed. The tool names are checked when every server's
                descriptors are known.
        """
        for arg, value in (("allow", allow), ("deny", deny), ("servers", servers)):
            if isinstance(value, str):
                raise ValueError(f"{arg} must be a list of tool names, not the string {value!r}")
        new = ToolFilter(
            allow=frozenset(allow) if allow is not None else None,
            deny=frozenset(deny or ()),
            predicate=predicate,
        )
        kept = self._servers if servers is None else _select(self._servers, servers)
        if all(s.tool_descriptors is not None for s in kept):
            check_patterns(new, [d for _, d in _index(kept, None)])
        return self._with(_servers=kept, _filters=(*self._filters, new))

    def descriptor_tokens(self) -> int:
        """Estimate the prompt tokens this set's tool descriptors cost on every
        model call, after filtering and collision renames (#222).

        Each descriptor is counted as the JSON of an OpenAI-style function tool
        (name, description, input schema), at about four characters per token.
        Tokenizers differ by model, so use it to compare tool sets, not as a
        bill. A server whose descriptors are not known yet counts as zero.

        Raises:
            ConfigError: Two tools would be exposed under one name (see
                :attr:`name_map`).
        """
        exposed, _ = resolve_collisions(self._descriptors())
        return descriptor_tokens(exposed)

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
        model called ``hr__get_employee`` (BG §2.7).

        Raises:
            ConfigError: Two tools would still be exposed under one name, such
                as two servers that share a short name.
        """
        _, name_map = resolve_collisions(self._descriptors())
        return name_map

    def _descriptors(self, *, filtered: bool = True) -> list[ToolDescriptor]:
        return [d for _, d in _index(self._servers, self._filters if filtered else None)]

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

    # ---- descriptors (lazy, governed tools/list, #221) ----------------------
    async def describe(self) -> ToolSet:
        """Return this set with every server's tool descriptors loaded.

        Discovery never connects (BG §2.7), so a discovered server's tools are
        unknown until this runs: a ``tools/list`` call to each server whose
        descriptors are missing, after the governance check and with the
        binding's credential. Every binding method calls it, and so can you, so
        that :attr:`name_map`, :meth:`descriptor_tokens` and the ``filter()``
        typo check see the tools. A ``401`` or ``403`` drops the cached
        credential and is retried once, unless it is a CIBA step-up answer
        (#1320), which is raised as it is.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ConfigError: No gateway credential is configured, or a ``filter()``
                or ``require_approval()`` name matches no tool.
            GatewayApprovalPending: The gateway is waiting for the user's CIBA
                approval; re-run after ``retry_after`` seconds.
            GatewayApprovalDenied: The user denied that approval.
            AuthError: The gateway refused the credential.
            ToolInvocationError: A server could not list its tools.
        """
        self._require_governed()
        missing = [i for i, s in enumerate(self._servers) if s.tool_descriptors is None]
        servers = list(self._servers)
        if missing:
            listed = await self._list(missing)
            for index, tools in zip(missing, listed, strict=True):
                servers[index] = dataclasses.replace(servers[index], tool_descriptors=tools)
        every = [d for _, d in _index(servers, None)]
        for each in self._filters:
            check_patterns(each, every)
        check_rules(self._approvals, every)
        return self._with(_servers=servers) if missing else self

    async def _list(self, indexes: list[int]) -> list[tuple[dict[str, object], ...]]:
        auth = self._credential()
        for attempt in (1, 2):
            headers = await auth.headers()
            try:
                return list(
                    await asyncio.gather(
                        *(
                            list_tools(
                                self._servers[i].endpoint_url,
                                headers,
                                server=self._servers[i].ref.coordinates,
                            )
                            for i in indexes
                        )
                    )
                )
            except AuthError:
                if attempt == 2:
                    raise
                await auth.invalidate()
        raise AssertionError("unreachable")  # pragma: no cover

    def _credential(self) -> McpAuth:
        if self._auth is None:
            raise ConfigError(
                "No gateway credential to bind MCP servers with.",
                remediation=(
                    "Set DONKEY_LLM_PROXY_CLIENT_ID and DONKEY_LLM_PROXY_CLIENT_SECRET (the "
                    "consumer application the MCP instances have a contract with), or pass "
                    "donkey.tools.discover(auth=...) for an instance behind JWT Validation."
                ),
            )
        return self._auth

    async def _plan(
        self, binding: str, *, per_tool: bool
    ) -> tuple[list[tuple[McpServerHandle, dict[str, _Tool]]], dict[str, str]]:
        """What ``binding`` binds: each server with a tool left after
        ``filter()``, with its tools by original name, and the headers.

        ``per_tool`` is false for a framework that binds a whole server as one
        object (ADK, Strands, OpenAI Agents, Agent Framework, CrewAI). Those take an
        allow-list of tool names but cannot rename one tool or gate it, so a
        collision rename or a ``require_approval()`` refuses the bind there.
        """
        described = await self.describe()
        servers = described.servers
        indexed = _index(servers, self._filters)
        exposed, _ = resolve_collisions([d for _, d in indexed])
        exposed_as = {id(d): name for name, d in exposed.items()}
        tools: dict[int, dict[str, _Tool]] = {}
        for index, desc in indexed:
            rule = rule_for(self._approvals, desc)
            tools.setdefault(index, {})[desc.name] = _Tool(exposed_as[id(desc)], desc, rule)
        bound = [t for names in tools.values() for t in names.values()]
        if not per_tool:
            renamed = sorted(t.exposed for t in bound if t.exposed != t.desc.name)
            if renamed:
                raise ConfigError(
                    f"{binding} binds each MCP server as one object and cannot rename a "
                    f"single tool, but these names collide across servers: {', '.join(renamed)}.",
                    remediation="Keep one of each clashing tool with filter(deny=[...]).",
                )
            gated = sorted(t.exposed for t in bound if t.rule is not None)
            if gated:
                raise _verify.blocked(
                    f"require_approval() on the {binding} binding ({', '.join(gated)}): it "
                    "binds whole servers, so there is no per-tool call to gate; bind with "
                    "langgraph(), llamaindex() or anthropic() to gate a discovered tool."
                )
        headers = await self._credential().headers()
        _report_cost(binding, len(exposed), descriptor_tokens(exposed), self._limits)
        if not bound:
            _log.info(
                "%s binds no tools: %s",
                binding,
                "filter() removed every tool" if servers else "the ToolSet holds no MCP server",
            )
        return [(servers[i], names) for i, names in sorted(tools.items())], headers

    def _gate(self, tool: _Tool, invoke: Callable[..., Any]) -> Callable[..., Any] | None:
        """``invoke`` behind ``tool``'s approval gate, or ``None`` when no rule gates it."""
        if tool.rule is None:
            return None
        return self._bind_tool(tool.exposed, tool.desc, invoke, schema=tool.desc.input_schema)

    # ---- per-framework binding (native types BG §2.7, docs/verified-apis.md §9) --
    # Each method binds the framework's own MCP class, as the local round-trip in
    # scripts/mcp_binding verified it, and returns what that class returns. Each
    # first refuses an ungoverned set (ADR 0013), then loads the descriptors.
    async def langgraph(self) -> list[Any]:  # -> list[BaseTool]
        """Bind the tool set as LangChain ``BaseTool`` objects, a flat list for
        ``create_react_agent(tools=...)`` or a ``ToolNode``.

        Uses ``langchain_mcp_adapters.client.MultiServerMCPClient``; each tool
        call opens its own session. A collision rename sets the tool's ``name``,
        and a ``require_approval()`` tool pauses the graph with ``interrupt()``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ImportError: ``langchain-mcp-adapters`` is not installed.
        """
        self._require_governed()
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
        except ImportError as exc:
            raise ImportError(_install("langgraph", "langchain-mcp-adapters")) from exc
        plan, headers = await self._plan("langgraph()", per_tool=True)
        client = MultiServerMCPClient(
            {
                server.ref.coordinates: {
                    "transport": "streamable_http",
                    "url": server.endpoint_url,
                    "headers": dict(headers),
                }
                for server, _ in plan
            }
        )
        bound: list[Any] = []
        for server, names in plan:
            for tool in await client.get_tools(server_name=server.ref.coordinates):
                plan_tool = names.get(tool.name)
                if plan_tool is None:
                    continue
                gated = self._gate(plan_tool, tool.coroutine)
                tool.name = plan_tool.exposed
                if gated is not None:
                    tool.coroutine = gated
                bound.append(tool)
        return bound

    async def llamaindex(self) -> list[Any]:  # -> list[FunctionTool]
        """Bind the tool set as LlamaIndex ``FunctionTool`` objects, a flat list
        for an agent's ``tools=``.

        Uses ``llama_index.tools.mcp.BasicMCPClient`` with ``McpToolSpec``;
        each tool call opens its own session.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ImportError: ``llama-index-tools-mcp`` is not installed.
        """
        self._require_governed()
        try:
            from llama_index.core.tools import FunctionTool
            from llama_index.tools.mcp import BasicMCPClient, McpToolSpec
        except ImportError as exc:
            raise ImportError(_install("llamaindex", "llama-index-tools-mcp")) from exc
        plan, headers = await self._plan("llamaindex()", per_tool=True)
        bound: list[Any] = []
        for server, names in plan:
            client = BasicMCPClient(server.endpoint_url, headers=dict(headers))
            spec = McpToolSpec(client, allowed_tools=sorted(names))
            for tool in await spec.to_tool_list_async():
                plan_tool = names[tool.metadata.name]
                tool.metadata.name = plan_tool.exposed
                gated = self._gate(plan_tool, tool.async_fn)
                if gated is not None:
                    tool = FunctionTool.from_defaults(async_fn=gated, tool_metadata=tool.metadata)
                bound.append(tool)
        return bound

    @asynccontextmanager
    async def anthropic(self) -> AsyncIterator[list[Any]]:  # -> list[BetaAsyncFunctionTool]
        """Bind the tool set as Anthropic SDK ``BetaAsyncFunctionTool`` objects
        for ``client.beta.messages.tool_runner(tools=...)``.

        An async context manager, because the tools call through an open MCP
        ``ClientSession`` (``anthropic.lib.tools.mcp.async_mcp_tool``): one per
        server, closed on exit::

            async with tools.anthropic() as bound:
                runner = client.beta.messages.tool_runner(tools=bound, ...)

        The Messages API ``mcp_servers`` connector is not used: Anthropic's
        servers dial that URL, and it has no field for the credential headers.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ImportError: ``anthropic`` is not installed.
        """
        self._require_governed()
        try:
            from anthropic.lib.tools import BetaAsyncFunctionTool
            from anthropic.lib.tools.mcp import async_mcp_tool
        except ImportError as exc:
            raise ImportError(_install("anthropic", None)) from exc
        plan, headers = await self._plan("anthropic()", per_tool=True)
        async with AsyncExitStack() as stack:
            bound: list[Any] = []
            for server, names in plan:
                session = await stack.enter_async_context(
                    open_session(server.endpoint_url, headers)
                )
                for mcp_tool in (await session.list_tools()).tools:
                    plan_tool = names.get(mcp_tool.name)
                    if plan_tool is None:
                        continue
                    tool = async_mcp_tool(mcp_tool, session)
                    gated = self._gate(plan_tool, tool.func)
                    if gated is not None:
                        tool = BetaAsyncFunctionTool(
                            gated,
                            name=plan_tool.exposed,
                            description=tool.description,
                            input_schema=tool.input_schema,
                        )
                    tool.name = plan_tool.exposed
                    bound.append(tool)
            yield bound

    async def adk(self) -> list[Any]:  # -> list[McpToolset]
        """Bind the tool set as Google ADK ``McpToolset`` objects, one per
        server, for ``LlmAgent(tools=[...])``.

        A toolset, not a flat list: ADK connects when the agent first lists its
        tools. The tools left after ``filter()`` go in its ``tool_filter``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ConfigError: Two servers expose a tool under one name.
            ImportError: ``google-adk`` is not installed.
        """
        self._require_governed()
        try:
            from google.adk.tools.mcp_tool import McpToolset, StreamableHTTPConnectionParams
        except ImportError as exc:
            raise ImportError(_install("adk", None)) from exc
        plan, headers = await self._plan("adk()", per_tool=False)
        return [
            McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=server.endpoint_url, headers=dict(headers)
                ),
                tool_filter=sorted(names),
            )
            for server, names in plan
        ]

    async def strands(self) -> list[Any]:  # -> list[MCPClient]
        """Bind the tool set as Strands ``MCPClient`` objects, one per server.

        Use each as a context manager and pass its ``list_tools_sync()`` to
        ``Agent(tools=...)``, or pass the clients themselves. The tools left
        after ``filter()`` go in its ``tool_filters``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ConfigError: Two servers expose a tool under one name.
            ImportError: ``strands-agents`` is not installed.
        """
        self._require_governed()
        try:
            from strands.tools.mcp import MCPClient
        except ImportError as exc:
            raise ImportError(_install("strands", None)) from exc
        plan, headers = await self._plan("strands()", per_tool=False)
        return [
            MCPClient(
                url=server.endpoint_url,
                headers=dict(headers),
                tool_filters={"allowed": sorted(names)},
            )
            for server, names in plan
        ]

    async def openai(self) -> list[Any]:  # -> list[MCPServerStreamableHttp]
        """Bind the tool set as OpenAI Agents SDK ``MCPServerStreamableHttp``
        objects, one per server, for ``Agent(mcp_servers=[...])``.

        Connect each (``async with``) before the run. The tools left after
        ``filter()`` go in its static ``tool_filter``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ConfigError: Two servers expose a tool under one name.
            ImportError: ``openai-agents`` is not installed.
        """
        self._require_governed()
        try:
            from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter
        except ImportError as exc:
            raise ImportError(_install("openai-agents", None)) from exc
        plan, headers = await self._plan("openai()", per_tool=False)
        return [
            MCPServerStreamableHttp(
                params={"url": server.endpoint_url, "headers": dict(headers)},
                name=server.ref.asset_id,
                tool_filter=create_static_tool_filter(allowed_tool_names=sorted(names)),
            )
            for server, names in plan
        ]

    async def agent_framework(self) -> list[Any]:  # -> list[MCPStreamableHTTPTool]
        """Bind the tool set as Microsoft Agent Framework ``MCPStreamableHTTPTool``
        objects, one per server, entered with ``async with`` and passed as an
        agent's ``tools=``.

        The credential goes in ``static_headers`` (the class has no
        ``headers=``), and the tools left after ``filter()`` in ``allowed_tools``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ConfigError: Two servers expose a tool under one name.
            ImportError: ``agent-framework`` is not installed.
        """
        self._require_governed()
        try:
            from agent_framework import MCPStreamableHTTPTool
        except ImportError as exc:
            raise ImportError(_install("agent_framework", None)) from exc
        plan, headers = await self._plan("agent_framework()", per_tool=False)
        return [
            MCPStreamableHTTPTool(
                server.ref.asset_id,
                server.endpoint_url,
                static_headers=dict(headers),
                allowed_tools=sorted(names),
            )
            for server, names in plan
        ]

    @asynccontextmanager
    async def crewai(self) -> AsyncIterator[list[Any]]:  # -> list[crewai BaseTool]
        """Bind the tool set as CrewAI ``BaseTool`` objects for ``Agent(tools=...)``.

        An async context manager, because the tools call through a
        ``crewai_tools.MCPServerAdapter`` that holds its server connection open:
        one per server, stopped on exit::

            async with tools.crewai() as bound:
                agent = Agent(role=..., tools=bound)
                await crew.kickoff_async()

        The tools left after ``filter()`` are the adapter's ``tool_names``
        allow-list. It needs ``mcp<2``: ``mcpadapt`` connects with the 1.x
        ``streamablehttp_client``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0013).
            ConfigError: Two servers expose a tool under one name.
            ImportError: ``crewai-tools[mcp]`` is not installed.
        """
        self._require_governed()
        try:
            from crewai_tools import MCPServerAdapter
        except ImportError as exc:
            raise ImportError(_install("crewai", "'crewai-tools[mcp]'")) from exc
        plan, headers = await self._plan("crewai()", per_tool=False)
        adapters: list[Any] = []
        try:
            for server, names in plan:
                params = {
                    "url": server.endpoint_url,
                    "transport": "streamable-http",
                    "headers": dict(headers),
                }
                # The adapter connects in its constructor, on its own thread and loop.
                adapters.append(await asyncio.to_thread(MCPServerAdapter, params, *sorted(names)))
            yield [tool for adapter in adapters for tool in adapter.tools]
        finally:
            for adapter in reversed(adapters):
                await asyncio.to_thread(adapter.stop)


def _select(servers: list[McpServerHandle], patterns: list[str]) -> list[McpServerHandle]:
    """The ``servers`` whose asset ID or coordinates match a pattern; a pattern
    that matches none is a ``ConfigError``."""

    def hit(server: McpServerHandle, pattern: str) -> bool:
        ref = server.ref
        return fnmatchcase(ref.asset_id, pattern) or fnmatchcase(ref.coordinates, pattern)

    unmatched = sorted(p for p in set(patterns) if not any(hit(s, p) for s in servers))
    if unmatched:
        raise ConfigError(
            f"filter(servers=...) names no server in the set: {', '.join(unmatched)}.",
            remediation="Match the asset ID or the coordinates in ToolSet.servers.",
        )
    return [s for s in servers if any(hit(s, p) for p in patterns)]


def _index(
    servers: Sequence[McpServerHandle], filters: Sequence[ToolFilter] | None
) -> list[tuple[int, ToolDescriptor]]:
    """Each tool's descriptor with the index of the server exposing it, kept by
    every one of ``filters`` (all of them when ``filters`` is ``None``)."""
    descs: list[tuple[int, ToolDescriptor]] = []
    for index, server in enumerate(servers):
        short = server.ref.asset_id
        for raw in server.tool_descriptors or ():
            description = raw.get("description")
            raw_tags = raw.get("tags")
            schema = raw.get("inputSchema")
            descs.append(
                (
                    index,
                    ToolDescriptor(
                        server=short,
                        name=str(raw.get("name")),
                        description=description if isinstance(description, str) else None,
                        tags=tuple(str(t) for t in raw_tags)
                        if isinstance(raw_tags, (list, tuple))
                        else (),
                        input_schema=schema if isinstance(schema, Mapping) else None,
                    ),
                )
            )
    if filters is None:
        return descs
    kept = [(i, d) for i, d in descs if all(f.accepts(d) for f in filters)]
    _log.debug("ToolSet exposes %d of %d tool descriptors", len(kept), len(descs))
    return kept


def _report_cost(binding: str, count: int, tokens: int, limits: tuple[int, int]) -> None:
    """Log what ``binding`` costs a model call, and warn when it is large (#1257).

    ``count`` tool descriptors, about ``tokens`` tokens (``descriptor_tokens()``),
    go into every model call's prompt. ``limits`` is ``(tool_warn_count,
    tool_warn_tokens)``; a warning names each one passed, and 0 turns one off.
    The defaults, 20 tools and 10,000 tokens, are published vendor guidance:

    - OpenAI, https://developers.openai.com/api/docs/guides/function-calling:
      "Aim for fewer than 20 functions available at the start of a turn at any
      one time, though this is just a soft suggestion."
    - Google Gemini, https://ai.google.dev/gemini-api/docs/function-calling:
      "Tool Selection: Keep active set to 10-20 tools maximum."
    - Anthropic, https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool:
      use tool search when "Your tool definitions consume more than 10k tokens."
    """
    _log.debug(
        "%s binds %d tool descriptor(s), about %d descriptor tokens per model call",
        binding,
        count,
        tokens,
    )
    max_count, max_tokens = limits
    passed = [
        f"more than {name}={limit}"
        for name, limit, value in (
            ("tool_warn_count", max_count, count),
            ("tool_warn_tokens", max_tokens, tokens),
        )
        if limit and value > limit
    ]
    if passed:
        _log.warning(
            "%s binds %d tool descriptors, about %d descriptor tokens per model call (%s). "
            "Large tool sets degrade tool selection and cost tokens on every call: narrow "
            "the set with filter(), or raise the threshold (0 turns it off).",
            binding,
            count,
            tokens,
            ", ".join(passed),
        )


def _install(extra: str, package: str | None) -> str:
    command = f"pip install 'donkey-kit[{extra},mcp]'" + (f" {package}" if package else "")
    return f"This binding needs the framework's MCP client. Install it with: {command}"
