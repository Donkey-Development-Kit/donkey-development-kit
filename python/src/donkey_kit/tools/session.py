"""MCP session management + the ``ToolSet`` facade (BG §2.7).

``ToolSet`` wraps N ``McpServerHandle``s. Filtering and collision resolution are
pure and implemented here. The per-framework binding methods return each
framework's NATIVE tool type (BG §2.7), built on the framework's own MCP class
as the local round-trip in ``scripts/mcp_binding`` verified it
(docs/verified-apis.md §9). Connections open when a set is described or bound,
never in ``discover()`` (BG §2.7).

Binding refuses a set holding any server that is not governed, before anything
else (#1150, ADR 0017): the governed transport attaches the consumer's gateway
credentials to every request, so they must never reach a server the gateway
does not front. There is no opt-out.

Nor does a bound server's redirect send them elsewhere (#1415): every binding
whose framework takes an HTTP client, a client factory or a transport gets one
from ``tools/mcp.py`` that strips the credential on a request to another origin
than the server's. Agent Framework's ``static_headers`` are removed on a
cross-origin redirect by the framework itself, and the Anthropic binding's
session does not follow redirects.

Every binding logs the tool count and :meth:`ToolSet.descriptor_tokens` at
debug level, and warns when either passes ``tool_warn_count`` or
``tool_warn_tokens`` (#1257; the defaults' sources are on :func:`_report_cost`).

``require_approval()`` gates named tools behind a human decision, the same
approval ``@donkey.tool(approval="required")`` gives a local function (#1153).
A per-tool binding (LangGraph, LlamaIndex, Anthropic) applies
:meth:`ToolSet._bind_tool` to each gated tool's invoker; a whole-server binding
refuses a gate. The rules and the gate live in ``tools/approval.py``.

Inside ``donkey.as_user(...)`` (BG §2.4, #1366) listing and binding send the
user's token in the user-token header (``Authorization: Bearer`` by default,
docs/verified-apis.md §13) instead of the credential's ``Authorization``; the
credential's other headers still go. The token is read when the set lists or
binds and goes into the framework client's static headers, so a binding sends
the identity it was bound for. A per-tool binding records that identity, and a
call under any other identity (another token, the service where a user was
bound, or the reverse) raises :class:`~donkey_kit.core.errors.AuthError` before it is sent
(#1418). A whole-server binding hands each call to the framework, out of the
SDK's sight, so it cannot refuse one: bind it inside the block that uses it.

Before a discovered set lists or binds, it checks the control plane its server
URLs were read from, as for any endpoint read from the project config files
(ADR 0005, ADR 0019, #1439, #1479): the gateway credential and the user token go
only where ``tools.auth.check_gateway_endpoint`` passes them, and otherwise
``ConfigError`` is raised and nothing is sent. A set built by hand has its URLs
in code, so it has no such check.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import logging
import time
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any, TypeVar

from ..core import _verify
from ..core.approvals import APPROVAL_TIMEOUT, Approvals, NativePause, parse_timeout
from ..core.config import DonkeyConfig
from ..core.errors import AuthError, ConfigError, ConfigWarning, DonkeyError
from ..core.identity import Requester, capture_requester, current_user
from ..core.reactions import Duration
from ..core.step_up import wait_for_approval
from ..core.tool_access import tool_access_denial
from ..registry.models import McpServerHandle
from .approval import ApprovalRule, check_rules, gated_tool, rule_for
from .auth import McpAuth
from .filter import (
    ToolDescriptor,
    ToolExclusion,
    ToolFilter,
    check_patterns,
    descriptor_tokens,
    resolve_collisions,
)
from .mcp import client_factory, list_tools, open_session, open_transport

__all__ = ["ToolSet", "UngovernedMcpServer"]

_log = logging.getLogger(__name__)

_T = TypeVar("_T")

#: A step-up wait longer than this outlives the gateway's approval request. OBO policy 1.1.3
#: starts a new one after about 124s (docs/verified-apis.md §4), which asks the user again.
_STEP_UP_FLOW_S = 120.0


class UngovernedMcpServer(DonkeyError):
    """A ``ToolSet`` binding was refused because a server in it is not governed
    (#1150, ADR 0017).

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
        "of the set: donkey.tools.discover() does by default (governed=True), so "
        "drop governed=None, or narrow the set with ToolSet.filter(servers=[...]). "
        "Run donkey.registry.explain(ref) to see which governance check failed."
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
    """One tool a binding binds: the name the model sees, its descriptor, the
    approval rule that gates it, if any, and who it was bound for."""

    exposed: str
    desc: ToolDescriptor
    rule: ApprovalRule | None
    bound_for: Requester


class ToolSet:
    """A governed set of MCP tools, from ``donkey.tools.discover()`` (BG §2.7).

    :meth:`filter` narrows it, :meth:`descriptor_tokens` estimates what its
    descriptors cost a model call, :meth:`require_approval` gates named tools
    behind a human decision, and :attr:`name_map` explains collision renames.
    The per-framework binding methods (:meth:`langgraph`, :meth:`adk`, ...)
    return each framework's native tool objects (docs/verified-apis.md §9).
    Each first raises
    :class:`UngovernedMcpServer` if any server in the set is not governed
    (ADR 0017), then lists the tools with :meth:`describe`.

    Docs: https://docs.donkey-kit.dev/tool-access/binding
    """

    def __init__(
        self,
        servers: list[McpServerHandle],
        *,
        _filters: tuple[ToolFilter, ...] = (),
        _approvals: tuple[ApprovalRule, ...] = (),
        _natives: tuple[NativePause, ...] = (),
        _approval_registry: Approvals | None = None,
        _auth: McpAuth | None = None,
        _discovered: tuple[datetime, float, float] | None = None,
        _limits: tuple[int, int] | None = None,
        _user_header: str | None = None,
        _endpoint_check: Callable[[bool, bool], None] | None = None,
        _step_up_wait: float = 0.0,
    ) -> None:
        self._servers = list(servers)
        # Every filter() call so far: a tool is exposed only if all accept it,
        # so a later call can narrow the set but never widen it (#222).
        self._filters = _filters
        self._approvals = _approvals
        # Framework pauses for a gated call, such as the LangGraph interrupt()
        # hook. Passed in by discovery: tools/ cannot import integrations/.
        self._natives = _natives
        # The discovering client's donkey.approvals: a gated call outside any
        # client scope records there; None (a set built by hand) needs a scope.
        self._approval_registry = _approval_registry
        # The gateway credential every binding sends (tools/auth.py); None when
        # none is configured, which only binding refuses.
        self._auth = _auth
        # (wall-clock time, monotonic time, TTL in seconds) of the discovery.
        self._discovered = _discovered
        # (tool_warn_count, tool_warn_tokens) from the config discovery read; a
        # set built by hand gets the DonkeyConfig defaults.
        self._limits = _limits or (DonkeyConfig.tool_warn_count, DonkeyConfig.tool_warn_tokens)
        # The header a donkey.as_user() token rides in (core.identity.user_token_header).
        self._user_header = _user_header or _verify.USER_TOKEN_HEADER
        # Called with (credential goes, user token goes) before either is sent to
        # the servers: discovery read their URLs from the control plane, which may
        # come from the working directory's config files (ADR 0019, #1479).
        self._endpoint_check = _endpoint_check
        # How long listing waits for a gateway step-up (CIBA) approval; 0 raises at once (#214).
        self._step_up_wait = _step_up_wait

    def _with(
        self,
        *,
        _servers: list[McpServerHandle] | None = None,
        _filters: tuple[ToolFilter, ...] | None = None,
        _approvals: tuple[ApprovalRule, ...] | None = None,
        _step_up_wait: float | None = None,
    ) -> ToolSet:
        return ToolSet(
            _servers if _servers is not None else self._servers,
            _filters=_filters if _filters is not None else self._filters,
            _approvals=_approvals if _approvals is not None else self._approvals,
            _natives=self._natives,
            _approval_registry=self._approval_registry,
            _auth=self._auth,
            _discovered=self._discovered,
            _limits=self._limits,
            _user_header=self._user_header,
            _endpoint_check=self._endpoint_check,
            _step_up_wait=self._step_up_wait if _step_up_wait is None else _step_up_wait,
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
    def wait_for_gateway_approval(self, timeout: Duration = "2m") -> ToolSet:
        """Return a view that waits for the gateway's step-up approval when it lists tools
        (BG §2.3, #214).

        When the OAuth2 OBO policy asks the user to approve a call on another device (CIBA),
        the gateway answers ``401`` until they do. With this view, :meth:`describe` re-sends
        the ``tools/list`` after each :class:`~donkey_kit.core.step_up.GatewayApprovalPending`'s
        ``retry_after`` until it goes through, the user denies it
        (:class:`~donkey_kit.core.step_up.GatewayApprovalDenied`, never retried), or ``timeout``
        passes (the last pending error is raised). Without it, the first pending answer is
        raised at once. The servers are listed together, and the first one to fail stops the
        others' waits (#1437).

        The gateway decides when step-up applies and keeps the audit trail; this only waits.
        It covers listing. A tool *call* behind the policy is not retried by the bindings: what
        each framework's MCP class does with the ``401`` is recorded in docs/verified-apis.md
        §9, and most of them lose the response body that tells a pending approval from a bad
        credential.

        Args:
            timeout: How long to wait for the user, such as ``"2m"``. The live policy's
                request expires after about two minutes and silently starts a new one,
                which asks the user again, so a longer timeout emits a
                :class:`~donkey_kit.core.errors.ConfigWarning`.

        Raises:
            ValueError: ``timeout`` is not a positive duration.
        """
        seconds = parse_timeout(timeout)
        if seconds > _STEP_UP_FLOW_S:
            warnings.warn(
                f"wait_for_gateway_approval({timeout!r}) waits longer than the gateway's "
                f"approval request lasts (about {_STEP_UP_FLOW_S:.0f}s): past it, the gateway "
                "starts a new approval request and asks the user again. Keep the timeout "
                "at '2m' or less unless repeated prompts are what you want.",
                ConfigWarning,
                stacklevel=2,
            )
        return self._with(_step_up_wait=seconds)

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
        ``donkey.approvals``: the client whose ``donkey.run()`` /
        ``donkey.as_user()`` block runs the call, else the client whose
        ``tools.discover()`` built the set (ADR 0018).

        A bare name (``"issue_refund"``) gates that tool on every server; the
        exposed form (``"billing__issue_refund"``) gates one server's tool. Rules
        accumulate, ``filter()`` keeps them, and when several match a tool the
        last one sets its ``risk`` and ``timeout``. Binding refuses a name no
        server exposes, so a typo never leaves a tool ungated. The approval is
        client-side and advisory; the gateway's own step-up is separate (see
        :meth:`wait_for_gateway_approval`, #214).

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
        ``tool`` (#1153). Bindings call :meth:`describe` first, which refuses a
        ``require_approval()`` name that matches no tool."""
        rule = rule_for(self._approvals, tool)
        if rule is None:
            return invoke
        return gated_tool(
            exposed_name,
            invoke,
            rule=rule,
            schema=schema,
            natives=self._natives,
            declared_on=self._approval_registry,
        )

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

    @property
    def excluded(self) -> tuple[ToolExclusion, ...]:
        """The tools the servers list that this set does not expose, and why (#312).

        Today every entry is a tool a :meth:`filter` call removed
        (``reason="filter"``). A tool outside the consumer's MCP access profile is
        not here: the gateway leaves it out of ``tools/list`` for that caller
        (docs/verified-apis.md §9, #303), so the SDK never sees it. A server
        whose descriptors are not known yet (see :meth:`describe`) adds nothing.
        """
        return tuple(
            ToolExclusion(name=d.name, server=d.server, reason="filter")
            for _, d in _index(self._servers, None)
            if not all(f.accepts(d) for f in self._filters)
        )

    def _descriptors(self, *, filtered: bool = True) -> list[ToolDescriptor]:
        return [d for _, d in _index(self._servers, self._filters if filtered else None)]

    def _require_governed(self) -> None:
        """Refuse to bind unless every server is governed (#1150, ADR 0017).

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
        (#1320), which is raised as it is, or the call ran inside
        ``donkey.as_user(...)``, where it was the user's token that was refused.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
            ConfigError: No gateway credential is configured outside a
                ``donkey.as_user(...)`` block; the server URLs were read from a
                ``base_url`` in the working directory's config files that may
                not receive the credential or the user token (ADR 0019); or a
                ``filter()`` or ``require_approval()`` name matches no tool.
            GatewayApprovalPending: The gateway is waiting for the user's CIBA
                approval; re-run after ``retry_after`` seconds, or build the set with
                :meth:`wait_for_gateway_approval` to wait for it here.
            GatewayApprovalDenied: The user denied that approval.
            AuthError: The gateway refused the credential or the user's token.
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
        user = current_user() is not None
        for attempt in (1, 2):
            headers = await self._headers()
            try:
                return await _all_or_none(
                    [
                        wait_for_approval(
                            functools.partial(
                                list_tools,
                                self._servers[i].endpoint_url,
                                headers,
                                server=self._servers[i].ref.coordinates,
                            ),
                            timeout=self._step_up_wait,
                        )
                        for i in indexes
                    ]
                )
            except AuthError as exc:
                if user:
                    raise AuthError(
                        f"{exc} The request carried the donkey.as_user() token.",
                        remediation=(
                            "Pass a current access token issued for the MCP instance: "
                            "its audience must name the instance, so exchange the "
                            "user's login token at your identity provider first "
                            "(RFC 8693; docs/verified-apis.md §13)."
                        ),
                    ) from exc
                if attempt == 2:
                    raise
                await self._credential().invalidate()
        raise AssertionError("unreachable")  # pragma: no cover

    async def _headers(self) -> dict[str, str]:
        """The headers listing and binding send: the credential's, with the
        ``donkey.as_user(...)`` token in the user-token header inside a block
        (#1366). It replaces the credential's ``Authorization``; the rest stays.
        A server URL that may not receive what would go raises ``ConfigError``
        (ADR 0019)."""
        user = current_user()
        if user is None:
            credential = self._credential()
            if self._endpoint_check is not None:
                self._endpoint_check(True, False)
            return await credential.headers()
        headers = await self._auth.headers() if self._auth is not None else {}
        header = self._user_header
        if header.lower() == _verify.LLM_PROXY_WALLET_JWT_HEADER.lower():
            value = f"{_verify.LLM_PROXY_WALLET_JWT_SCHEME} {user.token}"
        else:
            value = user.token
        kept = {k: v for k, v in headers.items() if k.lower() != header.lower()}
        if self._endpoint_check is not None:
            self._endpoint_check(bool(kept), True)
        return {**kept, header: value}

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

        Each tool records who it is bound for, read with the headers' user
        token, so a per-tool call under another user is refused (#1418).
        """
        described = await self.describe()
        servers = described.servers
        indexed = _index(servers, self._filters)
        exposed, _ = resolve_collisions([d for _, d in indexed])
        exposed_as = {id(d): name for name, d in exposed.items()}
        bound_for = capture_requester()
        tools: dict[int, dict[str, _Tool]] = {}
        for index, desc in indexed:
            rule = rule_for(self._approvals, desc)
            tools.setdefault(index, {})[desc.name] = _Tool(
                exposed_as[id(desc)], desc, rule, bound_for
            )
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
                raise ConfigError(
                    f"require_approval() names tools the {binding} binding cannot gate "
                    f"({', '.join(gated)}): it binds each MCP server as one object, so there "
                    "is no per-tool call for the SDK to hold.",
                    remediation=(
                        "Bind with langgraph(), llamaindex() or anthropic() to gate a "
                        "discovered tool, or use the framework's own approval on the "
                        "server object and drop require_approval() from this set."
                    ),
                )
        headers = await self._headers()
        _report_cost(binding, len(exposed), descriptor_tokens(exposed), self._limits)
        if not bound:
            _log.info(
                "%s binds no tools: %s",
                binding,
                "filter() removed every tool" if servers else "the ToolSet holds no MCP server",
            )
        return [(servers[i], names) for i, names in sorted(tools.items())], headers

    def _invoker(
        self, tool: _Tool, invoke: Callable[..., Any], server: McpServerHandle
    ) -> Callable[..., Any]:
        """``invoke`` raising :class:`~donkey_kit.core.tool_access.ToolNotInProfile` for
        the gateway's tool-access denial (#312) and refusing a call under another
        user than the binding's (#1418), behind ``tool``'s approval gate when a
        rule gates it."""
        refusing = _refusing(
            invoke, tool=tool.desc.name, server=server.ref.coordinates, bound_for=tool.bound_for
        )
        if tool.rule is None:
            return refusing
        return self._bind_tool(tool.exposed, tool.desc, refusing, schema=tool.desc.input_schema)

    # ---- per-framework binding (native types BG §2.7, docs/verified-apis.md §9) --
    # Each method binds the framework's own MCP class, as the local round-trip in
    # scripts/mcp_binding verified it, and returns what that class returns. Each
    # first refuses an ungoverned set (ADR 0017), then loads the descriptors.
    # The per-tool ones (LangGraph, LlamaIndex, Anthropic) wrap each tool's call so
    # the gateway's tool-access denial raises ToolNotInProfile (#312) and a call
    # under another user than the bind's raises AuthError (#1418); the
    # whole-server ones leave the call to the framework (docs/verified-apis.md §9).
    async def langgraph(self) -> list[Any]:  # -> list[BaseTool]
        """Bind the tool set as LangChain ``BaseTool`` objects, a flat list for
        ``create_react_agent(tools=...)`` or a ``ToolNode``.

        Uses ``langchain_mcp_adapters.client.MultiServerMCPClient``; each tool
        call opens its own session. A collision rename sets the tool's ``name``,
        and a ``require_approval()`` tool pauses the graph with ``interrupt()``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
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
                    "httpx_client_factory": client_factory(server.endpoint_url, headers),
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
                tool.coroutine = self._invoker(plan_tool, tool.coroutine, server)
                tool.name = plan_tool.exposed
                bound.append(tool)
        return bound

    async def llamaindex(self) -> list[Any]:  # -> list[FunctionTool]
        """Bind the tool set as LlamaIndex ``FunctionTool`` objects, a flat list
        for an agent's ``tools=``.

        Uses ``llama_index.tools.mcp.BasicMCPClient`` with ``McpToolSpec``;
        each tool call opens its own session.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
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
            # BasicMCPClient sends through a passed client and ignores headers= then.
            http = client_factory(server.endpoint_url, headers)(headers=headers)
            client = BasicMCPClient(server.endpoint_url, http_client=http)
            spec = McpToolSpec(client, allowed_tools=sorted(names))
            for tool in await spec.to_tool_list_async():
                plan_tool = names[tool.metadata.name]
                tool.metadata.name = plan_tool.exposed
                invoke = self._invoker(plan_tool, tool.async_fn, server)
                bound.append(
                    FunctionTool.from_defaults(async_fn=invoke, tool_metadata=tool.metadata)
                )
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
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
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
                    tool = BetaAsyncFunctionTool(
                        self._invoker(plan_tool, tool.func, server),
                        name=plan_tool.exposed,
                        description=tool.description,
                        input_schema=tool.input_schema,
                    )
                    bound.append(tool)
            yield bound

    async def adk(self) -> list[Any]:  # -> list[McpToolset]
        """Bind the tool set as Google ADK ``McpToolset`` objects, one per
        server, for ``LlmAgent(tools=[...])``.

        A toolset, not a flat list: ADK connects when the agent first lists its
        tools. The tools left after ``filter()`` go in its ``tool_filter``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
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
                    url=server.endpoint_url,
                    headers=dict(headers),
                    httpx_client_factory=client_factory(server.endpoint_url, headers),
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
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
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
                # The url= form builds its own client; the transport form takes ours.
                transport_callable=functools.partial(
                    open_transport, server.endpoint_url, dict(headers)
                ),
                tool_filters={"allowed": sorted(names)},
            )
            for server, names in plan
        ]

    async def openai_agents(self) -> list[Any]:  # -> list[MCPServerStreamableHttp]
        """Bind the tool set as OpenAI Agents SDK ``MCPServerStreamableHttp``
        objects, one per server, for ``Agent(mcp_servers=[...])``.

        Connect each (``async with``) before the run. The tools left after
        ``filter()`` go in its static ``tool_filter``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
            ConfigError: Two servers expose a tool under one name.
            ImportError: ``openai-agents`` is not installed.
        """
        self._require_governed()
        try:
            from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter
        except ImportError as exc:
            raise ImportError(_install("openai-agents", None)) from exc
        plan, headers = await self._plan("openai_agents()", per_tool=False)
        return [
            MCPServerStreamableHttp(
                params={
                    "url": server.endpoint_url,
                    "headers": dict(headers),
                    "httpx_client_factory": client_factory(server.endpoint_url, headers),
                },
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
        ``headers=``), which the framework removes on a redirect to another
        origin, and the tools left after ``filter()`` in ``allowed_tools``.

        Raises:
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
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
            UngovernedMcpServer: A server in the set is not governed (ADR 0017).
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
                    "httpx_client_factory": client_factory(server.endpoint_url, headers),
                }
                # The adapter connects in its constructor, on its own thread and loop.
                adapters.append(await asyncio.to_thread(MCPServerAdapter, params, *sorted(names)))
            yield [tool for adapter in adapters for tool in adapter.tools]
        finally:
            for adapter in reversed(adapters):
                await asyncio.to_thread(adapter.stop)


def _refusing(
    invoke: Callable[..., Any], *, tool: str, server: str, bound_for: Requester
) -> Callable[..., Any]:
    """``invoke``, an async MCP tool call, re-raising the gateway's tool-access
    denial as :class:`~donkey_kit.core.tool_access.ToolNotInProfile` (#312). Every
    other exception, and every result (an ``isError`` one too), passes through.

    The call carries the headers of the user it was bound for, so a call under
    any other user raises :class:`~donkey_kit.core.errors.AuthError` and is not
    sent (#1418). An approved call resumes under its requester's binding
    (``core.identity.requester_scope``), so this check sees that user."""

    @functools.wraps(invoke)
    async def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if not bound_for.matches_current_user():
            raise _wrong_user(tool, server, bound_for)
        try:
            return await invoke(*args, **kwargs)
        except Exception as exc:
            denied = tool_access_denial(exc, tool=tool, server=server)
            if denied is None:
                raise
            raise denied from exc

    return call


def _wrong_user(tool: str, server: str, bound_for: Requester) -> AuthError:
    made = "inside donkey.as_user()" if bound_for.user is not None else "as the service"
    now = (
        "inside donkey.as_user() with a different token"
        if current_user() is not None
        else "outside any donkey.as_user() block"
    )
    return AuthError(
        f"MCP tool {tool!r} on {server} was not called: its binding was made {made} and "
        f"sends that identity's headers, but the call runs {now}, so it would have reached "
        "the gateway as someone other than the caller.",
        remediation=(
            "Bind the ToolSet where the agent runs: inside the donkey.as_user() block, once "
            "per user or request, to act for that user, or outside any block to act as "
            "the service. A binding sends the identity it was bound for (BG §2.4)."
        ),
    )


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


async def _all_or_none(calls: Sequence[Awaitable[_T]]) -> list[_T]:
    """Await ``calls`` together and return their results in order (#1437).

    The first call to fail stops the rest: they are cancelled and awaited, and then its
    error is raised, the earliest call's when several failed at once. A sibling must not
    go on waiting for a step-up approval after another server denied it or refused the
    credential, because each re-send can push the user's device again. Cancelling the
    caller cancels them all too. ``asyncio.gather`` leaves the rest running after a failure,
    and ``TaskGroup`` needs Python 3.11.
    """
    tasks = [asyncio.ensure_future(call) for call in calls]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:
            error = task.exception() if task in done and not task.cancelled() else None
            if error is not None:
                raise error
        return [task.result() for task in tasks]
    finally:
        running = [task for task in tasks if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)


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
