"""The ``Donkey`` public surface (`BG §1.1`).

    from donkey_kit import Donkey
    donkey = Donkey.from_env()

    client = donkey.llm.client()                 # AsyncOpenAI at the proxy
    donkey.langgraph.chat_model("gpt-4o")        # native ChatOpenAI

Per-framework adapters are lazy attributes. Accessing one whose extra is not
installed raises :class:`ImportError` with the exact install command — never a
bare ``ModuleNotFoundError`` (`BG §1.1`).
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import inspect
import os
import sys
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Literal, ParamSpec, TypeVar, cast, overload

from .core import _verify, spanflush
from .core.approvals import (
    APPROVAL_TIMEOUT,
    Approval,
    ApprovalMode,
    Approvals,
    gate,
    parse_timeout,
)
from .core.approvals import approvals as _approvals
from .core.auth import AuthProvider
from .core.budget import Budget
from .core.cachecontrol import CacheControls, CacheScope, cache_scope
from .core.classifier import Classifier
from .core.classifier import classify as _classify
from .core.config import ConfigOverrides, DonkeyConfig
from .core.correlation import AttributionScope, attribution_scope
from .core.cost import CostTags
from .core.identity import UserScope
from .core.lastcall import UNOBSERVED, LastCall, current_last_call, unavailable
from .core.reactions import Duration, OnRefusal
from .core.runtime import Runtime
from .core.telemetry import RunScope, run_scope
from .core.toolspec import register_agent, register_tool, validate_service_id
from .core.transport import (
    DonkeyAsyncClient,
    DonkeyAsyncClientView,
    DonkeyClient,
    DonkeyClientView,
)
from .integrations import ADAPTERS, missing_framework_error
from .integrations import typed_refusals as _typed_refusals
from .llm.client import LLMClient
from .registry.criteria import GovernanceCriteria
from .registry.exchange import ExchangeRegistry
from .tools.session import ToolSet

# Imported at runtime: the public API surface check resolves ``Donkey.from_env``'s
# ``**overrides`` annotation (#719, #727).
if sys.version_info >= (3, 11):
    from typing import Unpack
else:
    from typing_extensions import Unpack

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI

    from .core.errors import DonkeyError, PolicyViolation
    from .integrations._base import Adapter
    from .integrations.adk import ADKAdapter
    from .integrations.agent_framework import AgentFrameworkAdapter
    from .integrations.anthropic import AnthropicAdapter
    from .integrations.crewai import CrewAIAdapter
    from .integrations.langgraph import LangGraphAdapter
    from .integrations.llamaindex import LlamaIndexAdapter
    from .integrations.openai_agents import OpenAIAgentsAdapter
    from .integrations.strands import StrandsAdapter

__all__ = ["Donkey", "ToolsFacade"]


_Callable = TypeVar("_Callable", bound=Callable[..., Any])
_P = ParamSpec("_P")
_R = TypeVar("_R")


def _missing_module(probe: tuple[str, ...]) -> str | None:
    """The first of a framework's required modules that cannot be located, or
    ``None`` when all of them can. Any error locating one means 'not installed'."""
    for module in probe:
        try:
            if importlib.util.find_spec(module) is None:
                return module
        except (ImportError, ValueError):
            return module
    return None


def _langgraph_interrupt(
    tool: str, arguments: Mapping[str, Any], risk: str | None
) -> Approval | None:
    """The LangGraph native pause for a gated tool (#212). A no-op until the
    process has imported LangGraph, so ``import donkey_kit`` never loads it."""
    if "langgraph" not in sys.modules:
        return None
    from .integrations.langgraph import approval_interrupt

    return approval_interrupt(tool, arguments, risk)


def _openai_agents_approval(
    tool: str, arguments: Mapping[str, Any], risk: str | None
) -> Approval | None:
    """The OpenAI Agents SDK's approved call for a gated tool (#213). A no-op
    until the process has imported the Agents SDK, so ``import donkey_kit``
    never loads it."""
    if "agents" not in sys.modules:
        return None
    from .integrations.openai_agents import approved_call

    return approved_call(tool, arguments, risk)


def _strands_approval(tool: str, arguments: Mapping[str, Any], risk: str | None) -> Approval | None:  # noqa: ARG001
    """The Strands native pause for a gated tool (#1234): the approval a
    Strands ``ApprovalHook`` granted for this call, which paused the agent with
    Strands' interrupt before the tool ran. A no-op until the process has
    imported Strands."""
    if "strands" not in sys.modules:
        return None
    from .integrations.strands import approved_call

    return approved_call(tool)


def _adk_approval(tool: str, arguments: Mapping[str, Any], risk: str | None) -> Approval | None:  # noqa: ARG001
    """The ADK native pause for a gated tool (#1235): the approval ADK's tool
    confirmation granted for this call, through the plugin
    ``integrations.adk.approval_plugin()`` builds. A no-op until the process
    has imported ADK."""
    if "google.adk" not in sys.modules:
        return None
    from .integrations.adk import approved_call

    return approved_call(tool)


# The framework pauses a gated tool tries before raising ApprovalRequired. The
# Agents hook only answers inside a call its function_tool() is running, and the
# Strands and ADK hooks only for a call their hook or plugin approved.
_NATIVES = (_openai_agents_approval, _strands_approval, _adk_approval, _langgraph_interrupt)


class ToolsFacade:
    """``donkey.tools`` — discovery + lock (BG §2.7)."""

    def __init__(self, registry: ExchangeRegistry) -> None:
        self._registry = registry

    async def discover(
        self,
        *,
        domain: str | None = None,
        tags: list[str] | None = None,
        governed: bool | GovernanceCriteria | None = None,
        locked: bool = False,
    ) -> ToolSet:
        """Discover a governed tool catalog and return a bindable ``ToolSet``.

        ``governed`` defaults to ``None`` (no filtering) in v1, with a startup
        log line stating discovery is unfiltered (governed-only discovery). Flipping the default
        to ``True`` is a breaking change reserved for a later major version.
        Unfiltered discovery still *lists* an ungoverned server, with its
        ``registry.explain()`` verdict on ``McpServerHandle.governance``, but the
        ``ToolSet`` binding methods refuse it with ``UngovernedMcpServer`` (ADR 0013).

        Blocked until Exchange search + the governed-state join are verified
        (verification discipline / the Verification milestone).
        """

        raise _verify.blocked(
            "Exchange search + governed-state join (governed-only discovery, the Verification "
            "milestone). ToolSet filtering and binding scaffolding exist; wire discover() once the "
            "APIs are confirmed, use registry.explain() for the empty-result reason, and pass "
            "ToolSet(_natives=_NATIVES) so require_approval() can pause a graph or an Agents run."
        )

    def lock(self) -> None:
        """Write a ``donkey.lock`` of resolved versions + digests (BG §2.7)."""
        raise _verify.blocked(
            "resolution API needed for the lockfile (BG §2.7, the Verification milestone)."
        )


class Donkey:
    """The SDK entry point: one governed handle on Agent Fabric (`BG §1.1`).

    A ``Donkey`` holds the resolved :class:`~donkey_kit.core.config.DonkeyConfig`,
    the auth providers and the shared governed transports. Every surface hangs off
    it: ``donkey.llm`` and :meth:`openai` for model calls, ``donkey.registry`` and
    ``donkey.tools`` for Exchange, and one lazy attribute per framework adapter
    (``donkey.langgraph``, ``donkey.adk``, ``donkey.crewai``, ...)::

        async with Donkey.from_env() as donkey:
            client = donkey.openai()

    Args:
        config: The resolved configuration. ``None`` resolves it from the
            environment, as :meth:`from_env` does without cost tags.
        auth: The control-plane auth provider. ``None`` builds the default for
            ``config``.
        llm_auth: The data-plane auth provider, when it differs from ``auth``.

    Raises:
        ConfigError: The configuration is incomplete or inconsistent.
        ImportError: A framework adapter attribute is read whose extra is not
            installed; the message carries the install command.

    Close it with :meth:`aclose` (or ``async with``); sync-only callers use
    :meth:`close` (or ``with``). Refusals surface as
    :class:`~donkey_kit.core.errors.DonkeyError` subclasses.

    Docs: https://docs.donkey-kit.dev/quickstart
    """

    # Adapters are resolved lazily by __getattr__ so an uninstalled framework
    # never breaks ``import donkey_kit``. These annotations exist purely so an
    # editor knows what each one is: without them a type checker only sees the
    # ``Adapter`` return type of __getattr__, and `donkey.langgraph.chat_model`
    # gets no completion and reads as an unknown attribute. Annotations bind no
    # value, so __getattr__ still runs at import-safe runtime.
    if TYPE_CHECKING:
        langgraph: LangGraphAdapter
        adk: ADKAdapter
        strands: StrandsAdapter
        agent_framework: AgentFrameworkAdapter
        openai_agents: OpenAIAgentsAdapter
        anthropic: AnthropicAdapter
        crewai: CrewAIAdapter
        llamaindex: LlamaIndexAdapter

    def __init__(
        self,
        config: DonkeyConfig | None = None,
        *,
        auth: AuthProvider | None = None,
        llm_auth: AuthProvider | None = None,
    ) -> None:
        # Config, auth, budget, the shared clients and the OTLP bootstrap all
        # live on the runtime, so this handle and the module-level factories
        # (which use the process-default runtime) are built the same way (#725).
        self._runtime = Runtime(config, auth=auth, llm_auth=llm_auth)
        self._cfg = self._runtime.config
        self._auth = self._runtime.auth
        self._llm_auth = self._runtime.llm_auth
        self._budget = self._runtime.budget
        self._http: DonkeyAsyncClient = self._runtime.http
        self._control_http: DonkeyAsyncClient = self._runtime.control_http
        self._llm = LLMClient(self._cfg, self._http, self._sync_http_client)
        self._registry = ExchangeRegistry(self._cfg, self._control_http)
        self._tools = ToolsFacade(self._registry)
        self._adapter_cache: dict[str, Adapter] = {}

    @classmethod
    def from_env(
        cls,
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
        path: str | os.PathLike[str] | None = None,
        **overrides: Unpack[ConfigOverrides],
    ) -> Donkey:
        """Build from the environment and the config files (`BG §1.1`), with
        any :class:`~donkey_kit.core.config.DonkeyConfig` field set in code on
        top (§2.1, #727)::

            donkey = Donkey.from_env(team="support", project="triage-v2", env="prod")
            donkey = Donkey.from_env(timeout_s=10, telemetry_capture_content=True)

        Forwards to :meth:`DonkeyConfig.resolve
        <donkey_kit.core.config.DonkeyConfig.resolve>`: each keyword argument
        beats the environment, which beats ``.donkey-kit.local.toml``,
        ``.donkey-kit.toml`` and the user file, which beat the default.

        ``team`` / ``project`` / ``env`` / ``enduser_id`` (the ``enduser.id``
        tag) are shorthands for the fixed cost-attribution dimensions (BG §1.7,
        #196): each merges over a ``cost=CostTags(...)`` argument and over
        anything resolved from ``DONKEY_COST_*`` env vars or the
        ``[donkey.cost]`` table. Per-call overrides layer on via
        ``donkey.run(...)``. ``on_model_substitution="raise"`` opts into model
        determinism (BG §1.7, #309): a call raises
        :class:`~donkey_kit.core.errors.ModelSubstituted` when the gateway
        serves a different model than requested.

        Args:
            team: The cost-attribution team.
            project: The cost-attribution project.
            env: The cost-attribution environment (not the Anypoint
                ``environment`` field).
            enduser_id: The cost-attribution ``enduser.id``.
            path: A config file to read in place of the working directory's
                ``.donkey-kit.toml``.
            **overrides: Any ``DonkeyConfig`` field, by name.

        Raises:
            ConfigError: A value is invalid (all of them are listed in one
                error) or ``path`` names no file.
            TypeError: An override names no ``DonkeyConfig`` field.

        Docs: https://docs.donkey-kit.dev/reference/configuration#precedence
        """
        shorthand = CostTags(team=team, project=project, env=env, enduser_id=enduser_id)
        base = overrides.get("cost", CostTags())
        # A cost that isn't a CostTags is left for resolve() to report.
        if not shorthand.is_empty and isinstance(base, CostTags):
            overrides["cost"] = base.merge(shorthand)
        return cls(DonkeyConfig.resolve(path=path, **overrides))

    # --- framework-free surfaces -------------------------------------------
    @property
    def config(self) -> DonkeyConfig:
        """The resolved configuration this Donkey was built with (read-only).

        Docs: https://docs.donkey-kit.dev/reference/configuration
        """
        return self._cfg

    @property
    def llm(self) -> LLMClient:
        """The governed model-call surface: native OpenAI-compatible clients and
        per-framework connection kwargs pointed at the proxy (`BG §1.1`).

        Docs: https://docs.donkey-kit.dev/quickstart
        """
        return self._llm

    @property
    def budget(self) -> Budget:
        """The token-budget window for this Donkey, updated in-band from every
        response's ``x-token-*`` headers (BG §1.3, #185). Unobserved (all fields
        ``None``) until the first call returns; there is no budget-query endpoint,
        so it is only as fresh as ``budget.observed_at`` (upstream gap #2)."""
        return self._budget

    @property
    def last_call(self) -> LastCall:
        """The gateway's own metadata about the most recent governed model call in
        this context — its ``request_id``, ``api_instance_id`` and
        ``environment_id`` (BG §1.7, #362); what the gateway *did* with the request —
        ``served_provider`` / ``served_model`` / ``routing_type`` and the
        ``fallback`` flag, with ``substituted`` true when the served model differs
        from ``requested_model`` (BG §1.7, #309); and the per-call usage token counts
        (``input_tokens`` / ``output_tokens`` / ``total_tokens`` and the
        cost-relevant ``cached_tokens`` / ``cache_write_tokens`` /
        ``reasoning_tokens``, #307). The success-path counterpart to the ids
        :class:`~donkey_kit.core.errors.DonkeyError` hands you on a refusal.

        Usage counts are read from the response body, so they are ``None`` (never
        ``0``) when the gateway sent no ``usage`` (or Gemini ``usageMetadata``)
        object; on a streamed response
        they land once the terminal SSE event has been consumed, not at first read.

        Contextvar-scoped, not instance-scoped (hazard #2): under the parallel
        fan-out ``donkey.run()`` encourages, each task reads the call *it* made,
        never whichever sibling's response landed last. A framework-spawned task
        copies the context at creation, so it sees its own record and never
        clobbers the parent's.

        Three honest states, never a bare ``None`` (hazard #3):

        * **OBSERVED** — a governed response populated it (fields may still be
          ``None`` if the gateway sent no identity headers: "we saw the response,
          it said nothing").
        * **UNOBSERVED** — no governed model call has returned in this context yet.
        * **UNAVAILABLE** — every adapter used on this Donkey routes outside our
          transport (today only CrewAI, whose native OpenAI provider builds its
          own clients, #740), so a response can never reach the record.
          :attr:`LastCall.surface` names which. This is derived from the
          adapters actually resolved, and the conformance suite asserts the
          exemption rather than skipping it (the conformance kit).
        """
        observed = current_last_call()
        if observed is not None:
            return observed
        # No response reached the contextvar. Distinguish a cold read from a
        # structurally-unobservable surface: if every adapter resolved on this
        # Donkey routes outside our transport, say so by name instead of leaving
        # an indistinguishable UNOBSERVED (hazard #3). An empty cache (raw client
        # / not used yet) is a cold read, not UNAVAILABLE.
        used = list(self._adapter_cache.values())
        if used and all(not a.observes_last_call for a in used):
            return unavailable(", ".join(sorted(self._adapter_cache)))
        return UNOBSERVED

    @overload
    def openai(self, *, sync: Literal[False] = ..., **kw: Any) -> AsyncOpenAI: ...

    @overload
    def openai(self, *, sync: Literal[True], **kw: Any) -> OpenAI: ...

    def openai(self, *, sync: bool = False, **kw: Any) -> AsyncOpenAI | OpenAI:
        """The headline two-line ergonomic (`BG §1.1`): a native OpenAI client
        pointed at the governed proxy, with nothing new to learn.

        This is a *real method*, not a lazy adapter — it shadows ``__getattr__``,
        so ``donkey.openai()`` never probes for (or demands the install of) the
        OpenAI Agents SDK. It delegates to :meth:`LLMClient.client`, returning a
        real ``openai.AsyncOpenAI`` (or ``OpenAI`` with ``sync=True``), governed
        on identical terms. The Agents SDK adapter now lives at
        ``donkey.openai_agents`` (BG §1.8).
        """
        if sync:
            return self._llm.client(sync=True, **kw)
        return self._llm.client(sync=False, **kw)

    @overload
    def http_client(self, *, sync: Literal[False] = ...) -> DonkeyAsyncClientView: ...

    @overload
    def http_client(self, *, sync: Literal[True]) -> DonkeyClientView: ...

    def http_client(self, *, sync: bool = False) -> DonkeyAsyncClientView | DonkeyClientView:
        """The governed data-plane ``httpx`` client, for a framework or SDK that
        takes one (``http_client=``, ``async_http_client=``, a pre-built
        ``AsyncOpenAI``). Requests sent through it get the same headers, retries,
        span, budget and ``donkey.last_call`` as every adapter.

        It is a non-owning view of the shared client: closing it, as a framework
        that owns its client's lifecycle does, leaves the shared client open, and
        only :meth:`aclose`/:meth:`close` end the pool (#733). Defaults to the
        ``httpx.AsyncClient``; ``sync=True`` returns the blocking ``httpx.Client``.
        Event hooks added to it run for every request sent through it.
        """
        if sync:
            return self._sync_http_client().view()
        return self._http.view()

    @property
    def registry(self) -> ExchangeRegistry:
        """The Exchange registry client used for tool discovery and resolution (BG §2.7).

        Docs: https://docs.donkey-kit.dev/tool-access/discovery
        """
        return self._registry

    @property
    def tools(self) -> ToolsFacade:
        """Governed tool discovery and the ``donkey.lock`` lockfile (BG §2.7).

        Docs: https://docs.donkey-kit.dev/tool-access
        """
        return self._tools

    def run(
        self,
        id: str | None = None,
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
        business_group: str | None = None,
        typed_refusals: bool = True,
    ) -> RunScope:
        """Group one logical agent run under a shared correlation id (BG §1.7, #195).

        The headline ergonomic — bind a run id once, and every governed model
        call inside the block carries it, with no threading through framework
        state::

            async with donkey.run(id=ticket.id):
                await triage_agent.run(ticket)

        The returned :class:`~donkey_kit.core.telemetry.RunScope` is a **dual
        sync/async** context manager, so plain ``with donkey.run(...)`` works too.
        The bound id becomes the ``X-Correlation-Id`` on every request in the
        block (the client↔gateway join key), the span's ``donkey.correlation_id``,
        and :attr:`DonkeyError.correlation_id` — so a Slack log line joins to the
        gateway record (Scenario C). Each individual request still gets its own
        unique per-call id (``X-Donkey-Request-Id`` → :attr:`DonkeyError.call_id`).

        Propagation is automatic: framework-spawned ``asyncio`` tasks copy the
        current context, so a LangGraph node running the model on a child task
        sees the same run id. Concurrent runs in the same process do not leak into
        each other, and nested ``donkey.run()`` blocks rebind then restore.
        Omitting ``id`` binds a generated id (a "run of one").

        Works with or without OpenTelemetry installed — correlation is pure
        contextvar + headers; spans only decorate when OTel is present.

        Cost-attribution overrides (BG §1.7, #196) bind for the block on top
        of the tags set once on the Donkey: ``donkey.run(team=..., project=...,
        env=..., enduser_id=...)`` wins per field for every call inside, and the
        rest fall back to the configured tags. Like the run id, the binding rides
        the contextvar, so it reaches framework-spawned tasks and restores on exit.
        ``business_group=`` (#310) does the same for the business group: it
        replaces the configured one for every call in the block. To attribute
        part of a run without starting a new one, use :meth:`attribution`.

        **Typed refusals** (BG §1.2, #724): a governance refusal or a transport
        failure leaving the block is re-raised as its typed
        :class:`~donkey_kit.core.errors.DonkeyError` (``PIIDetected``,
        ``GatewayUnavailable``, ``ModelSubstituted``, ...), whichever framework or
        HTTP SDK wrapped it on the way out, so the caller writes one ``except``
        for every framework. Anything that is not a refusal propagates unchanged.
        It is the bridge :func:`donkey_kit.typed_refusals` applies on its own;
        pass ``typed_refusals=False`` to get the framework's own errors instead.
        """
        override = CostTags(team=team, project=project, env=env, enduser_id=enduser_id)
        return run_scope(
            id,
            override if not override.is_empty else None,
            _typed_refusals() if typed_refusals else None,
            business_group,
        )

    def run_from_headers(
        self,
        headers: Mapping[str, str],
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
        business_group: str | None = None,
        typed_refusals: bool = True,
    ) -> RunScope:
        """Join the caller's run: :meth:`run`, with the id read from an inbound
        request's headers (#308).

        An agent or service that serves HTTP receives its caller's correlation
        ID in a header. Binding it makes every governed call the handler makes
        send the caller's ID, so a supervisor and the sub-agents it calls share
        one run end to end instead of each minting its own::

            @app.post("/task")
            async def handle(request: Request):
                async with donkey.run_from_headers(request.headers):
                    return await agent.run(await request.json())

        The ID is read from the ``correlation_header`` setting's header
        (``X-Correlation-Id`` by default), case-insensitively. It is adopted
        when it is 1-128 of ``[A-Za-z0-9._:-]``; when it is missing or
        malformed a fresh one is minted, never forwarded. The rule is the one a
        served A2A task uses (``donkey.serve()``, #225). Every ``donkey.llm.chat``
        span in the block records ``donkey.correlation_id.source`` as
        ``"adopted"`` or ``"minted"``, so a broken chain is visible in the trace
        rather than silently plausible. The block yields the bound ID.

        It takes the keyword arguments of :meth:`run` and binds them the same
        way. To send the bound ID on an outbound hop the governed transport
        does not carry, such as your own MCP or A2A client, pass it
        :meth:`correlation_headers`.

        Docs: https://docs.donkey-kit.dev/telemetry
        """
        from .core.inbound import correlation_id_from_headers

        inbound = correlation_id_from_headers(headers, self._cfg.correlation_header)
        override = CostTags(team=team, project=project, env=env, enduser_id=enduser_id)
        return run_scope(
            inbound.correlation_id,
            override if not override.is_empty else None,
            _typed_refusals() if typed_refusals else None,
            business_group,
            inbound.source,
        )

    def correlation_headers(self) -> dict[str, str]:
        """The bound run's correlation header, for an outbound hop the governed
        transport does not carry (#308).

        Every request through this client's transport (model calls, registry
        lookups, ``donkey.http_client()``) already sends the run's ID. A call
        made with another HTTP client, such as your MCP client or a call to
        another agent over A2A, does not, so pass it these headers::

            async with donkey.run(id=ticket.id):
                await agents.post(url, json=task, headers=donkey.correlation_headers())

        Returns ``{name: id}``, named by the ``correlation_header`` setting
        (``X-Correlation-Id`` by default) inside a :meth:`run` or
        :meth:`run_from_headers` block, and ``{}`` outside one: a call outside a
        run is its own run, so there is nothing to join.
        """
        from .core.correlation import current_correlation_id

        rid = current_correlation_id()
        if rid is None:
            return {}
        return {self._cfg.correlation_header or _verify.CORRELATION_ID_HEADER: rid}

    def run_context(self, run_id: str | None = None) -> RunScope:
        """Deprecated alias for :meth:`run` (BG §1.7): use ``donkey.run(id=…)``.
        Emits a :class:`DeprecationWarning` (#720)."""
        warnings.warn(
            "Donkey.run_context() is deprecated; use donkey.run(id=...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.run(run_id)

    def attribution(
        self,
        *,
        business_group: str | None = None,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
    ) -> AttributionScope:
        """Attribute every governed call in the block to a business group and
        cost tags, per request, without starting a run (#310).

        One process serving several tenants or teams keeps one client,
        connection pool, auth cache and budget, and states which group each
        call belongs to::

            async def handle(tenant: str, prompt: str):
                with donkey.attribution(business_group=tenant):
                    return await model.ainvoke(prompt)

        The business group replaces the configured ``business_group`` for the
        block (precedence: this scope, then the ``Donkey`` config, then env,
        then file). The cost tags merge per field over any already bound by an
        enclosing :meth:`run` or ``attribution()`` block. The correlation ID is
        left alone, so it composes with :meth:`run` and one run can span two
        cost centres::

            async with donkey.run(id=job.id):
                with donkey.attribution(business_group="risk-eu"):
                    await score(job)
                with donkey.attribution(business_group="risk-us"):
                    await score(job)

        A **dual sync/async** context manager. The binding is a contextvar, so
        it reaches framework-spawned ``asyncio`` tasks, concurrent tasks never
        see each other's group, and nesting restores the outer value on exit.
        Both the async and the blocking client honour it. Invalid values raise
        :class:`~donkey_kit.core.errors.ConfigError` here, not on the first
        request.

        The same documented degradation as :meth:`run` applies (BG §1.8): an
        adapter handed only a ``default_headers`` snapshot
        (``connection_kwargs()``) captured the configured group when it was
        built and does not follow this scope. The header name is still an
        unverified placeholder (``docs/verified-apis.md`` §3, #21).
        """
        return attribution_scope(
            CostTags(team=team, project=project, env=env, enduser_id=enduser_id),
            business_group,
        )

    def cache(
        self,
        *,
        skip: bool | None = None,
        no_store: bool | None = None,
        ttl: int | None = None,
        threshold: float | None = None,
        principal_id: str | None = None,
    ) -> CacheScope:
        """Steer the gateway's **semantic cache** for every governed model call in
        the block (docs/verified-apis.md §2, #587).

        When the proxy is fronted by the Anypoint semantic-caching policy, this
        binds the ``x-cache-*`` steering headers for the block — the same
        contextvar-bound ergonomic as :meth:`run`, so the controls reach every call
        inside (including calls on framework-spawned ``asyncio`` tasks) with no
        threading through framework state::

            with donkey.cache(skip=True):
                await agent.run(task)          # bypass the cache for this block

            async with donkey.cache(ttl=60, threshold=0.9):
                ...                            # tighter match, shorter-lived entries

        The returned :class:`~donkey_kit.core.cachecontrol.CacheScope` is a **dual
        sync/async** context manager, so plain ``with`` works too. The controls:

        * ``skip`` — bypass the cache policy entirely (passthrough).
        * ``no_store`` — look up, but do not write the result on a miss.
        * ``ttl`` — override the entry time-to-live, in seconds (positive int).
        * ``threshold`` — override the similarity threshold, a float in ``[0.0, 1.0]``.
        * ``principal_id`` — override the id the similarity filter partitions on.

        The **outcome** of each call is surfaced on ``donkey.last_call.cache_status``
        / ``.cache_score`` (and the ``donkey.cache.*`` span attributes), and a cache
        ``hit`` never advances the token budget (a verbatim replay is no fresh
        spend, #587). This is not client-side caching — the SDK caches nothing and
        computes no embeddings; it steers and surfaces the *gateway's* cache.

        The same documented degradation as :meth:`run` applies (BG §1.8): a
        ``connection_kwargs()``-only adapter that does not route through the
        shared transport does not see the contextvar, so its calls are not
        steered. Invalid controls raise :class:`~donkey_kit.core.errors.ConfigError`
        at the call site, not on the first request.
        """
        return cache_scope(
            CacheControls(
                skip=skip,
                no_store=no_store,
                ttl=ttl,
                threshold=threshold,
                principal_id=principal_id,
            )
        )

    def as_user(self, *, id_token: str) -> UserScope:
        """Run the governed model calls in the block as the user behind
        ``id_token``, not as this service (BG §2.4, #215).

        Entering the block trades the user's OIDC ``id_token`` for a
        gateway-scoped access token with an RFC 8693 token exchange, then every
        data-plane call in the block carries that token::

            async with donkey.as_user(id_token=request.id_token):
                await agent.run(task)

        The binding is a contextvar like :meth:`run`: it reaches the ``asyncio``
        tasks the block spawns and never leaks into a concurrent task, and a
        nested block restores the outer user on exit. Exchanged tokens are
        cached per user until shortly before they expire. Control-plane calls
        stay the service's own.

        The exchange endpoint and the header the token rides in are UNVERIFIED
        placeholders (docs/verified-apis.md §13): set ``token_exchange_path`` and
        ``user_token_header`` to the values your gateway uses. The SDK forwards
        the user's token and makes no authorisation decision of its own.

        The same documented degradation as :meth:`run` applies (BG §1.8): a
        ``connection_kwargs()``-only adapter that does not route through the
        shared transport never sees the user.

        Raises:
            ConfigError: No connected-app credentials, or a config the exchange
                can't run under (see ``TokenExchange.check``).
            AuthError: On entry, when the exchange refuses the id_token or
                returns a malformed response.
        """
        return UserScope(self._runtime.token_exchange(), id_token)

    # --- one-line on-ramps: decorators (#200) ------------------------------
    # The overloads keep the decorated callable's own signature, for both the
    # bare and the keyword form, under a downstream ``mypy --strict`` (#716).
    @overload
    def governed(
        self,
        func: Callable[_P, _R],
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
        business_group: str | None = None,
        typed_refusals: bool = True,
    ) -> Callable[_P, _R]: ...

    @overload
    def governed(
        self,
        func: None = None,
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
        business_group: str | None = None,
        typed_refusals: bool = True,
    ) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]: ...

    def governed(
        self,
        func: Callable[_P, _R] | None = None,
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
        business_group: str | None = None,
        typed_refusals: bool = True,
    ) -> Callable[_P, _R] | Callable[[Callable[_P, _R]], Callable[_P, _R]]:
        """Wrap a callable so its body runs inside a ``donkey.run()`` scope (#200).

        The one-line on-ramp to governed execution: every governed model call
        made inside the decorated function carries a fresh run/correlation id (a
        "run of one"), the optional per-run cost tags and the OTel span, and a
        refusal leaves it as its typed :class:`~donkey_kit.core.errors.DonkeyError`
        (#724) — exactly the scope :meth:`run` establishes (BG §1.7, #195), with
        nothing to thread through framework state. ``typed_refusals=False`` opts
        out of the typed re-raise, as it does on :meth:`run`::

            @donkey.governed(team="support")
            async def handle_ticket(ticket): ...

        Wraps **both sync and async** callables: an async callable is wrapped with
        ``async with self.run(...)`` so every ``await`` inside sees the same run
        id (contextvar propagation reaches framework-spawned tasks); a sync
        callable uses the plain ``with`` form. Usable bare (``@donkey.governed``)
        or parametrised (``@donkey.governed(team=...)``).

        There is deliberately **no** ``id=`` argument: each call opens its own
        run, and a fixed id pinned across every call would collapse unrelated runs
        into one correlation. When you need to pin a specific id, use
        ``donkey.run(id=...)`` directly. ``approval=`` / ``risk=`` live on
        :meth:`tool`, per tool (BG §2.3).
        """

        def decorate(fn: Callable[_P, _R]) -> Callable[_P, _R]:
            if inspect.iscoroutinefunction(fn):
                # Here ``_R`` is the coroutine type, which mypy cannot narrow
                # from ``iscoroutinefunction``; the casts restate that fact.
                coro_fn = cast(Callable[_P, Awaitable[Any]], fn)

                @functools.wraps(fn)
                async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> object:
                    async with self.run(
                        team=team,
                        project=project,
                        env=env,
                        enduser_id=enduser_id,
                        business_group=business_group,
                        typed_refusals=typed_refusals,
                    ):
                        return await coro_fn(*args, **kwargs)

                return cast(Callable[_P, _R], async_wrapper)

            @functools.wraps(fn)
            def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
                with self.run(
                    team=team,
                    project=project,
                    env=env,
                    enduser_id=enduser_id,
                    business_group=business_group,
                    typed_refusals=typed_refusals,
                ):
                    return fn(*args, **kwargs)

            return sync_wrapper

        # Bare ``@donkey.governed`` passes the callable positionally; the
        # parametrised ``@donkey.governed(...)`` passes nothing and returns the
        # decorator to be applied next.
        if func is not None:
            return decorate(func)
        return decorate

    @overload
    @staticmethod
    def tool(func: _Callable, /) -> _Callable: ...

    @overload
    @staticmethod
    def tool(
        *,
        approval: ApprovalMode | None = None,
        risk: str | None = None,
        approval_timeout: Duration = APPROVAL_TIMEOUT,
        server: str | None = None,
    ) -> Callable[[_Callable], _Callable]: ...

    @staticmethod
    def tool(
        func: _Callable | None = None,
        /,
        *,
        approval: ApprovalMode | None = None,
        risk: str | None = None,
        approval_timeout: Duration = APPROVAL_TIMEOUT,
        server: str | None = None,
    ) -> _Callable | Callable[[_Callable], _Callable]:
        """Mark a callable as a governed tool, recording its name, signature and
        docstring in an introspectable registry (#200), and optionally gate it
        behind a human approval (`BG §2.3`).

        Bare, it returns the **same** callable (identity preserved, not
        wrapped) with a ``__donkey_tool__`` marker attached, and appends a
        :class:`~donkey_kit.core.toolspec.ToolSpec` to the process-global
        registry read by :func:`~donkey_kit.core.toolspec.registered_tools`::

            @donkey.tool
            async def lookup_crm(customer_id: str) -> dict: ...

        The same marker is what the Phase 2 scanner (BG §2.5) and the A2A agent-card
        generator (BG §2.9) both read, so the one annotation pays off three times
        (#200).

        ``server=`` attributes the tool to the MCP server it belongs to, by that
        server's service ID. The ID is permanent and always yours to set: it is
        never inferred from a module path or function name (#503). A tool with
        no ``server`` is reported by the scanner as unattributed::

            @donkey.tool(server="sku-manager")
            async def lookup_sku(sku: str) -> dict: ...

        With ``approval="required"`` a call does not run the tool. It records a
        pending :class:`~donkey_kit.core.approvals.Approval`, emits it as a span
        event and raises :class:`~donkey_kit.core.approvals.ApprovalRequired`;
        :attr:`approvals` resolves or rejects it::

            @donkey.tool(approval="required", risk="financial")
            async def issue_refund(ticket_id: str, amount: float) -> str: ...

            try:
                await issue_refund("T-1", 140.0)
            except ApprovalRequired as err:
                ...  # ask a human, then:
                await donkey.approvals.resolve(err.approval.id, approved_by="m.keller")

        Inside a LangGraph graph run with a checkpointer, the call pauses the
        graph with LangGraph's own ``interrupt()`` instead, and
        ``Command(resume={"approved": True, "by": "m.keller"})`` resumes it (see
        :func:`donkey_kit.integrations.langgraph.approval_interrupt`). Under the
        OpenAI Agents SDK, build the tool with
        :func:`donkey_kit.integrations.openai_agents.function_tool`: the run
        stops on the SDK's own ``needs_approval``, and ``RunState`` approves it.

        The approval is client-side and advisory: calling the undecorated
        function bypasses it, and the gateway never learns of it (gateway
        step-up is #214). It never overrides a refusal: the gateway still judges
        every model call the resumed tool makes.

        Args:
            func: The tool, when used bare (``@donkey.tool``).
            approval: ``"required"`` gates every call behind a human decision.
            risk: A label for the approver and the span, such as ``"financial"``.
            approval_timeout: How long a pending approval waits before resolving
                it raises :class:`~donkey_kit.core.approvals.ApprovalTimedOut`:
                seconds, a ``timedelta`` or a string such as ``"15m"``.
            server: The service ID (lowercase kebab-case, such as
                ``"sku-manager"``) of the MCP server this tool belongs to.

        Raises:
            ValueError: The callable has no docstring (an undescribed tool is
                useless to a model and to the registry, #200), ``approval`` is
                not ``"required"``, ``risk`` is blank, ``approval_timeout``
                is not a positive duration, or ``server`` is not a kebab-case
                service ID.

        Docs: https://docs.donkey-kit.dev/hitl
        """
        if approval is not None and approval != "required":
            raise ValueError(f'approval must be "required" or None, not {approval!r}')
        if risk is not None and (not isinstance(risk, str) or not risk.strip()):
            raise ValueError(f"risk must be a non-empty label such as 'financial', not {risk!r}")
        timeout = parse_timeout(approval_timeout)
        if server is not None:
            validate_service_id(server)

        def decorate(fn: _Callable) -> _Callable:
            target = gate(fn, risk=risk, timeout=timeout, natives=_NATIVES) if approval else fn
            return register_tool(target, approval=approval, risk=risk, server=server)

        # Bare ``@donkey.tool`` passes the callable positionally; the
        # parametrised ``@donkey.tool(...)`` passes nothing and returns the
        # decorator to be applied next.
        if func is not None:
            return decorate(func)
        return decorate

    @staticmethod
    def agent(*, name: str, skills: Sequence[str] = ()) -> Callable[[_Callable], _Callable]:
        """Mark a callable as an agent entry point, recording its name, skills
        and docstring in an introspectable registry (#217).

        Like ``@donkey.tool``, it returns the **same** callable with an
        ``__donkey_agent__`` marker attached and appends an
        :class:`~donkey_kit.core.toolspec.AgentSpec` to the process-global
        registry read by :func:`~donkey_kit.core.toolspec.registered_agents`::

            @donkey.agent(name="support-triage", skills=["triage", "draft-reply"])
            async def handle(task): ...

        ``donkey scan`` reads the marker statically (BG §2.5), and the A2A card
        generator behind ``donkey.serve()`` (BG §2.9, #224) reads the same
        record, so the two cannot drift. It changes nothing about how the
        callable runs.

        Args:
            name: The agent's service ID: lowercase kebab-case, permanent, and
                never inferred (#503).
            skills: The skill IDs the agent advertises, such as ``"triage"``.

        Raises:
            ValueError: ``name`` is not a kebab-case service ID, a skill is
                blank or repeated, or the callable has no docstring (it is the
                agent's description).

        Docs: https://docs.donkey-kit.dev/a2a
        """
        validate_service_id(name, what="name")
        if isinstance(skills, str):
            raise ValueError(f"skills must be a list of skill IDs, not the string {skills!r}")
        recorded = tuple(skills)
        if any(not isinstance(s, str) or not s.strip() for s in recorded):
            raise ValueError(f"every skill must be a non-empty string, not {list(recorded)!r}")
        if len(set(recorded)) != len(recorded):
            raise ValueError(f"skills must not repeat, not {list(recorded)!r}")

        def decorate(fn: _Callable) -> _Callable:
            return register_agent(fn, name=name, skills=recorded)

        return decorate

    def serve(
        self,
        handler: Callable[..., Any],
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        url: str | None = None,
        version: str = "0.0.0",
    ) -> None:
        """Serve a ``@donkey.agent`` handler over A2A on ``host:port``, blocking
        until the server stops (`BG §2.9.1`, #224)::

            @donkey.agent(name="support-triage", skills=["triage"])
            async def handle(task: A2ATask) -> str:
                \"\"\"Triage a support ticket.\"\"\"
                return await graph.ainvoke(task.input)

            donkey.serve(handle)   # card at /.well-known/agent-card.json

        The official ``a2a-sdk`` serves the protocol; the card is generated from
        the ``@donkey.agent`` marker, so it matches what ``donkey scan`` reads.
        It binds to localhost by default: the agent sits behind the gateway and
        is never the public face.

        Each task is governed (#225): its downstream calls share one run whose
        correlation ID is the caller's ``X-Correlation-Id`` (or a fresh one) and
        carry this client's cost tags, a ``donkey.a2a.task`` span parents their
        spans, and a governance refusal fails the task with its typed name in a
        structured ``error`` part, never a stack trace.
        :func:`donkey_kit.a2a.build_app` returns the
        same server as an ASGI app, and :func:`donkey_kit.a2a.serve` is this
        method as a function.

        Args:
            handler: A callable marked with ``@donkey.agent``. It takes an
                :class:`~donkey_kit.a2a.A2ATask` and returns a ``str`` or an
                :class:`~donkey_kit.a2a.A2AResult`.
            host: The interface to bind. Pass ``"0.0.0.0"`` only when the
                gateway reaches the agent over the network.
            port: The port to bind.
            url: The URL the card advertises; defaults to
                ``http://<host>:<port>/``. Pass the gateway's URL when one
                fronts the agent.
            version: The agent version the card advertises.

        Raises:
            TypeError: ``handler`` is not marked with ``@donkey.agent``.
            ImportError: The ``[a2a]`` extra is not installed.

        Docs: https://docs.donkey-kit.dev/a2a
        """
        from .a2a import serve

        serve(
            handler,
            host=host,
            port=port,
            url=url,
            version=version,
            telemetry=self._cfg.telemetry,
            correlation_header=self._cfg.correlation_header,
            refusals=_typed_refusals(),
        )

    @property
    def approvals(self) -> Approvals:
        """The pending tool approvals of this process (`BG §2.3`): resolve or
        reject the call a :class:`~donkey_kit.core.approvals.ApprovalRequired`
        names. Process-wide, like the tool registry, so every ``Donkey`` returns
        the same one.

        Docs: https://docs.donkey-kit.dev/hitl
        """
        return _approvals

    def on(self, refusal: type[PolicyViolation]) -> OnRefusal:
        """Declare once what to do when a call is refused with ``refusal`` (`BG §2.1`).

        The reaction runs inside this Donkey's governed transport, for every
        model call it makes, so no call site needs its own try/except::

            donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait="45m")
            donkey.on(PIIDetected).call(mask_and_retry, max_times=1)
            donkey.on(ContentSafetyBlocked).fallback(model="internal-safe-model")
            donkey.on(PolicyViolation).escalate(to=hitl_queue)  # catch-all

        The most specific registered type wins, so ``on(PolicyViolation)`` is
        the catch-all, and registering a type again replaces its reaction.

        A handler reacts; it never decides policy. No reaction turns a refusal
        into a success: it either lets the refusal propagate, or issues a new
        governed request that the gateway judges afresh. There is no
        ``ignore()``, and ``max_times``/``max_wait`` bound every reaction. A
        handler that raises is logged and the refusal propagates.
        :class:`~donkey_kit.core.errors.AgentKilled` is never handled
        (`BG §2.6`).

        Raises:
            TypeError: ``refusal`` is not a ``PolicyViolation`` subclass, or is
                ``AgentKilled``.

        Docs: https://docs.donkey-kit.dev/refusal-handlers
        """
        return self._runtime.reactions.on(refusal)

    @property
    def classify(self) -> Classifier:
        """The process-wide refusal classifier (`BG §2.2`): the same object as
        ``donkey_kit.classify``. Call it to type an error response, or register
        a custom gateway policy's refusal shape on it::

            class SalaryLeakBlocked(PolicyViolation):
                remediation = "Salary figures may not leave HR. Ask the user to open the HR portal."

            donkey.classify.register(
                match=dict(status=403, error_type="acme_salary_block"),
                raises=SalaryLeakBlocked,
            )

        A registration is process-wide, not per ``Donkey``: ``classify()`` also
        runs where no ``Donkey`` exists (the transport, ``classify(err.response)``
        bridging, ``donkey doctor``), and every path must type a refusal the same
        way. Handlers (:meth:`on`) and spans pick a registered type up with no
        extra wiring.

        Docs: https://docs.donkey-kit.dev/custom-policies
        """
        return _classify

    def simulate(self, error: type[DonkeyError], *, times: int = 1) -> AbstractContextManager[None]:
        """Inject a real gateway refusal in-process, no server (#190, BG §1.5).

        Swaps a fixture-returning transport onto this Donkey's data-plane HTTP client(s) for
        the next ``times`` calls, so the branch of your agent that handles a typed
        refusal runs with no network and no gateway::

            with donkey.simulate(PIIDetected):
                # every call in here fails as a real PIIDetected, then normal
                await agent.ainvoke(...)

        The injected body is the **same captured fixture** ``classify()`` and the
        ``donkey mock`` server are tested against — so it lights up as exactly the
        typed refusal you asked for, not a hand-rolled stand-in — and every
        injected response carries ``x-donkey-simulator: true`` (BG §1.4).

        ``times`` counts logical calls (retries of one call count once); call
        ``times``+1 onward proceeds normally. Nesting composes and the previous
        transport is restored on exit, even if the block raises.

        Swaps both the async and the blocking client, building the blocking one
        if nothing has yet, so a sync call — ``client(sync=True)``, or
        ``ChatOpenAI.invoke()`` on a model built inside the block — is injected
        into too (#736). Raises
        ``ValueError`` for a refusal type with no captured fixture (e.g.
        :class:`~donkey_kit.core.errors.GatewayUnavailable`, which no gateway
        response produces). Body-shaping (specific PII entities, a custom
        message) is the follow-up #188; this injects the fixture verbatim.
        """
        from .simulator.inject import simulate as _simulate

        clients: list[Any] = [self._http, self._sync_http_client()]
        return _simulate(clients, error, times=times)

    @property
    def _sync_http(self) -> DonkeyClient | None:
        return self._runtime.built_sync_http

    @property
    def _owned_auth_http(self) -> DonkeyAsyncClient | None:
        return self._runtime.owned_auth_http

    def _sync_http_client(self) -> DonkeyClient:
        return self._runtime.sync_http()

    def flush_telemetry(self, timeout_s: float = 2.0) -> bool:
        """Export the spans DDK's own ``TracerProvider`` still holds, waiting up to
        ``timeout_s``, before a serverless runtime freezes the process or a script
        exits.

        DDK's provider is process-wide, shared by every ``Donkey``: the DDK-scoped
        one built from an OTLP endpoint, or the one installed as the global with
        ``telemetry_install_global``. Spans riding a provider your process set are
        yours to flush, so it is left alone. This call blocks; from async code,
        ``await asyncio.to_thread(donkey.flush_telemetry)``.

        Returns ``True`` once the spans are exported, or when DDK built no
        provider; ``False`` if the flush timed out or failed. It never raises for
        an export failure.

        Raises:
            ValueError: if ``timeout_s`` is not positive.

        Docs: https://docs.donkey-kit.dev/telemetry
        """
        if not timeout_s > 0:
            raise ValueError(f"timeout_s must be positive, got {timeout_s!r}")
        return spanflush.flush_owned(max(1, round(timeout_s * 1000)))

    async def aclose(self) -> None:
        """Close every transport this Donkey owns: the data-plane and
        control-plane clients, the connected-app token-fetch client it built (a
        caller-supplied ``auth`` provider stays caller-owned), and the blocking
        client. Each one is closed even if an earlier close raises."""
        await self._runtime.aclose()

    async def __aenter__(self) -> Donkey:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def close(self) -> None:
        """Close the blocking transport.

        This sync method cannot close the async transports. A sync-only caller
        never opens them; mixed or async callers must use :meth:`aclose`, which
        closes every async transport and calls this method for the blocking one.
        """
        self._runtime.close()

    def __enter__(self) -> Donkey:
        """Sync context manager for the blocking surface. ``__exit__`` cannot
        await, so it does not touch the async transport — harmless, because httpx
        opens no connection until a request is actually made, and a purely
        synchronous caller never makes one through it."""
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- lazy per-framework adapters ---------------------------------------
    def __getattr__(self, name: str) -> Adapter:
        # Only called for attributes not found normally.
        spec = ADAPTERS.get(name)
        if spec is None:
            raise AttributeError(f"{type(self).__name__!r} has no attribute {name!r}")
        if name in self._adapter_cache:
            return self._adapter_cache[name]
        missing = _missing_module(spec.probe)
        if missing is not None:
            raise missing_framework_error(spec.extra, missing)
        module = importlib.import_module(spec.module, package="donkey_kit.integrations")
        adapter_cls = getattr(module, spec.cls)
        adapter: Adapter = adapter_cls(self._cfg, self._http, self._sync_http_client)
        self._adapter_cache[name] = adapter
        return adapter
