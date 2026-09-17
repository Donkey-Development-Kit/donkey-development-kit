"""The ``Donkey`` public surface (§3.2).

    from donkey_kit import Donkey
    donkey = Donkey.from_env()

    client = donkey.llm.client()                 # AsyncOpenAI at the proxy
    donkey.langgraph.chat_model("gpt-4o")        # native ChatOpenAI

Per-framework adapters are lazy attributes. Accessing one whose extra is not
installed raises :class:`ImportError` with the exact install command — never a
bare ``ModuleNotFoundError`` (§3.2).
"""

from __future__ import annotations

import functools
import importlib
import importlib.util
import inspect
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any, Literal, TypeVar, overload

from .core import _verify
from .core.auth import AnypointConnectedApp, AuthProvider
from .core.budget import Budget
from .core.config import DonkeyConfig, OnModelSubstitution
from .core.cost import CostTags
from .core.lastcall import UNOBSERVED, LastCall, current_last_call, unavailable
from .core.telemetry import RunScope, configure_otlp_export, run_scope
from .core.toolspec import register_tool
from .core.transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    build_http_client,
    build_sync_http_client,
)
from .integrations import ADAPTERS
from .llm.client import LLMClient
from .registry.exchange import ExchangeRegistry
from .registry.governance import GovernanceCriteria
from .tools.session import ToolSet

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI

    from .core.errors import DonkeyError
    from .integrations._base import Adapter
    from .integrations.adk import ADKAdapter
    from .integrations.agent_framework import AgentFrameworkAdapter
    from .integrations.anthropic import AnthropicAdapter
    from .integrations.crewai import CrewAIAdapter
    from .integrations.langgraph import LangGraphAdapter
    from .integrations.llamaindex import LlamaIndexAdapter
    from .integrations.openai_agents import OpenAIAgentsAdapter
    from .integrations.strands import StrandsAdapter


_Callable = TypeVar("_Callable", bound=Callable[..., Any])


def _framework_installed(probe: str) -> bool:
    """Whether a framework's representative module can be located, without
    importing it. Any error locating it means 'not installed'."""
    try:
        return importlib.util.find_spec(probe) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


class _ToolsFacade:
    """``donkey.tools`` — discovery + lock (§4.1, §4.2)."""

    def __init__(self, registry: ExchangeRegistry) -> None:
        self._registry = registry

    async def discover(
        self,
        *,
        domain: str | None = None,
        tags: list[str] | None = None,
        governed: bool | GovernanceCriteria | None = None,
        governance: Any | None = None,
        locked: bool = False,
    ) -> ToolSet:
        """Discover a governed tool catalog and return a bindable ``ToolSet``.

        ``governed`` defaults to ``None`` (no filtering) in v1, with a startup
        log line stating discovery is unfiltered (§6.1.2). Flipping the default
        to ``True`` is a breaking change reserved for a later major version.

        Blocked until Exchange search + the governed-state join are verified
        (§0.3 / §6.7).
        """

        raise _verify.blocked(
            "Exchange search + governed-state join (§6.1, §6.7). ToolSet filtering "
            "and binding scaffolding exist; wire discover() once the APIs are "
            "confirmed and use registry.explain() for the empty-result reason."
        )

    def lock(self) -> None:
        """Write a ``donkey.lock`` of resolved versions + digests (§4.2)."""
        raise _verify.blocked("resolution API needed for the lockfile (§4.2, §6.7).")


class Donkey:
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
    ) -> None:
        self._cfg = config or DonkeyConfig.from_env()
        # Zero-config OTLP export (BG §1.6, #194): installs an exporter when an
        # OTEL_EXPORTER_OTLP_ENDPOINT is set and telemetry is on; a no-op (and
        # never an error) otherwise. This is the single funnel — from_env()
        # delegates here — and it is idempotent across many Donkey() instances.
        configure_otlp_export(self._cfg)
        self._auth = auth if auth is not None else self._default_auth(self._cfg)
        # One Budget per Donkey (never global, §1.3 / #185): both transports feed
        # it in-band from every response's x-token-* headers.
        self._budget = Budget()
        self._http: DonkeyAsyncClient = build_http_client(
            self._cfg, self._auth, budget=self._budget
        )
        # Built only if someone asks for a blocking client, so the common async
        # path never opens a connection pool it will not use.
        self._sync_http: DonkeyClient | None = None
        self._llm = LLMClient(self._cfg, self._http, self._sync_http_client)
        self._registry = ExchangeRegistry(self._cfg, self._http)
        self._tools = _ToolsFacade(self._registry)
        self._adapter_cache: dict[str, Adapter] = {}

    @classmethod
    def from_env(
        cls,
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
        on_model_substitution: OnModelSubstitution | None = None,
    ) -> Donkey:
        """Build from the environment (`BG §1.1`), optionally setting the fixed
        cost-attribution tags once for every call (§3, BG §1.7, #196)::

            donkey = Donkey.from_env(team="support", project="triage-v2", env="prod")

        The four dimensions — ``team`` / ``project`` / ``env`` / ``enduser_id``
        (the ``enduser.id`` tag) — are the fixed set; each override merges over
        anything already resolved from ``DONKEY_COST_*`` env vars or the
        ``[donkey.cost]`` toml table. Values are validated by
        :class:`~donkey_kit.core.cost.CostTags`. Per-call overrides layer on via
        ``donkey.run(...)``.

        ``on_model_substitution`` opts into model determinism (§3, #309): pass
        ``"raise"`` to have a call raise
        :class:`~donkey_kit.core.errors.ModelSubstituted` when the gateway serves
        a different model than requested (a routing fallback). Defaults to the
        resolved config value (``DONKEY_ON_MODEL_SUBSTITUTION`` / toml / ``"off"``)
        when left ``None``; the substitution is always visible passively on
        ``donkey.last_call`` regardless.
        """
        cfg = DonkeyConfig.from_env()
        override = CostTags(team=team, project=project, env=env, enduser_id=enduser_id)
        if not override.is_empty:
            cfg = cfg.with_overrides(cost=cfg.cost.merge(override))
        if on_model_substitution is not None:
            cfg = cfg.with_overrides(on_model_substitution=on_model_substitution)
        return cls(cfg)

    # --- framework-free surfaces -------------------------------------------
    @property
    def config(self) -> DonkeyConfig:
        return self._cfg

    @property
    def llm(self) -> LLMClient:
        return self._llm

    @property
    def budget(self) -> Budget:
        """The token-budget window for this Donkey, updated in-band from every
        response's ``x-token-*`` headers (§1.3, #185). Unobserved (all fields
        ``None``) until the first call returns; there is no budget-query endpoint,
        so it is only as fresh as ``budget.observed_at`` (upstream gap #2)."""
        return self._budget

    @property
    def last_call(self) -> LastCall:
        """The gateway's own metadata about the most recent governed model call in
        this context — its ``request_id``, ``api_instance_id`` and
        ``environment_id`` (§3, #362); what the gateway *did* with the request —
        ``served_provider`` / ``served_model`` / ``routing_type`` and the
        ``fallback`` flag, with ``substituted`` true when the served model differs
        from ``requested_model`` (§3, #309); and the per-call usage token counts
        (``input_tokens`` / ``output_tokens`` / ``total_tokens`` and the
        cost-relevant ``cached_tokens`` / ``cache_write_tokens`` /
        ``reasoning_tokens``, #307). The success-path counterpart to the ids
        :class:`~donkey_kit.core.errors.DonkeyError` hands you on a refusal.

        Usage counts are read from the response body, so they are ``None`` (never
        ``0``) when the gateway sent no ``usage`` object; on a streamed response
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
          transport (LiteLLM-backed ADK/CrewAI, or ``default_headers``-only
          LlamaIndex / MS Agent Framework), so a response can never reach the
          record. :attr:`LastCall.surface` names which. This is derived from the
          adapters actually resolved, and the conformance suite asserts the
          exemption rather than skipping it (§8.1).
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
        ``donkey.openai_agents`` (§3.3).
        """
        if sync:
            return self._llm.client(sync=True, **kw)
        return self._llm.client(sync=False, **kw)

    @property
    def registry(self) -> ExchangeRegistry:
        return self._registry

    @property
    def tools(self) -> _ToolsFacade:
        return self._tools

    def run(
        self,
        id: str | None = None,
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
    ) -> RunScope:
        """Group one logical agent run under a shared correlation id (§2.3, #195).

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

        Cost-attribution overrides (§3, BG §1.7, #196) bind for the block on top
        of the tags set once on the Donkey: ``donkey.run(team=..., project=...,
        env=..., enduser_id=...)`` wins per field for every call inside, and the
        rest fall back to the configured tags. Like the run id, the binding rides
        the contextvar, so it reaches framework-spawned tasks and restores on exit.
        """
        override = CostTags(team=team, project=project, env=env, enduser_id=enduser_id)
        return run_scope(id, override if not override.is_empty else None)

    def run_context(self, run_id: str | None = None) -> RunScope:
        """Back-compat alias for :meth:`run` (§2.3). Prefer ``donkey.run(id=…)``."""
        return self.run(run_id)

    # --- one-line on-ramps: decorators (#200) ------------------------------
    def governed(
        self,
        func: _Callable | None = None,
        *,
        team: str | None = None,
        project: str | None = None,
        env: str | None = None,
        enduser_id: str | None = None,
    ) -> _Callable | Callable[[_Callable], _Callable]:
        """Wrap a callable so its body runs inside a ``donkey.run()`` scope (#200).

        The one-line on-ramp to governed execution: every governed model call
        made inside the decorated function carries a fresh run/correlation id (a
        "run of one"), the optional per-run cost tags, the OTel span and typed
        refusals — exactly the scope :meth:`run` establishes (§2.3, #195), with
        nothing to thread through framework state::

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
        ``donkey.run(id=...)`` directly. ``approval=`` / ``risk=`` arrive with
        HITL (2.3) and are out of scope here (#200).
        """

        def decorate(fn: _Callable) -> _Callable:
            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    async with self.run(
                        team=team, project=project, env=env, enduser_id=enduser_id
                    ):
                        return await fn(*args, **kwargs)

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.run(
                    team=team, project=project, env=env, enduser_id=enduser_id
                ):
                    return fn(*args, **kwargs)

            return sync_wrapper  # type: ignore[return-value]

        # Bare ``@donkey.governed`` passes the callable positionally; the
        # parametrised ``@donkey.governed(...)`` passes nothing and returns the
        # decorator to be applied next.
        if func is not None:
            return decorate(func)
        return decorate

    @staticmethod
    def tool(func: _Callable) -> _Callable:
        """Mark a callable as a governed tool **without changing call behaviour**,
        recording its name, signature and docstring in an introspectable registry
        (#200).

        Returns the **same** callable (identity preserved — it is not wrapped)
        with a ``__donkey_tool__`` marker attached, and appends a
        :class:`~donkey_kit.core.toolspec.ToolSpec` to the process-global registry
        read by :func:`~donkey_kit.core.toolspec.registered_tools`::

            @donkey.tool
            async def lookup_crm(customer_id: str) -> dict: ...

        The same marker is what the Phase 2 scanner (§2.5) and the A2A agent-card
        generator (§2.9) both read, so the one annotation pays off three times
        (#200).

        Raises :class:`ValueError` when the callable has no docstring — an
        undescribed tool is useless to a model and to the registry, so it is
        rejected at decoration time rather than recorded blank (#200).
        """
        return register_tool(func)

    def simulate(
        self, error: type[DonkeyError], *, times: int = 1
    ) -> AbstractContextManager[None]:
        """Inject a real gateway refusal in-process, no server (#190, BG §1.5).

        Swaps a fixture-returning transport onto this Donkey's HTTP client(s) for
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

        Swaps the async client always, and the blocking client only if it has
        already been built (``client(sync=True)`` was called earlier); a sync
        client created *inside* the block is not retro-swapped. Raises
        ``ValueError`` for a refusal type with no captured fixture (e.g.
        :class:`~donkey_kit.core.errors.ContentSafetyBlocked`, still
        under-documented, #253). Body-shaping (specific PII entities, a custom
        message) is the follow-up #188; this injects the fixture verbatim.
        """
        from .simulator.inject import simulate as _simulate

        clients: list[Any] = [self._http]
        if self._sync_http is not None:
            clients.append(self._sync_http)
        return _simulate(clients, error, times=times)

    def _sync_http_client(self) -> DonkeyClient:
        if self._sync_http is None:
            # Same Budget object as the async client, so a blocking caller updates
            # donkey.budget on identical terms (§1.3, #185).
            self._sync_http = build_sync_http_client(self._cfg, budget=self._budget)
        return self._sync_http

    async def aclose(self) -> None:
        await self._http.aclose()
        self.close()

    async def __aenter__(self) -> Donkey:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def close(self) -> None:
        """Close the blocking transport. ``aclose()`` calls this too, so an async
        caller who also used ``client(sync=True)`` still closes both."""
        if self._sync_http is not None:
            self._sync_http.close()
            self._sync_http = None

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
        if not _framework_installed(spec.probe):
            raise ImportError(
                f"The {name!r} integration is not installed. Install it with:\n"
                f'    pip install "donkey-kit[{spec.extra}]"'
            )
        module = importlib.import_module(spec.module, package="donkey_kit.integrations")
        adapter_cls = getattr(module, spec.cls)
        adapter: Adapter = adapter_cls(self._cfg, self._http)
        self._adapter_cache[name] = adapter
        return adapter

    @staticmethod
    def _default_auth(cfg: DonkeyConfig) -> AuthProvider | None:
        """Build control-plane auth when credentials are present. The LLM proxy
        credential is separate and handled by the OpenAI client (§2.2)."""
        if cfg.client_id and cfg.client_secret:
            return AnypointConnectedApp(
                client_id=cfg.client_id,
                client_secret=cfg.client_secret,
                control_plane_url=cfg.control_plane_url,
                http_client=build_http_client(cfg, None),  # token fetches need no auth
            )
        return None
