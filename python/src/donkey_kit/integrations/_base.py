"""Shared base for framework adapters.

Design rule (BG §1.8): adapters return NATIVE framework objects, never wrappers.
Each adapter depends on exactly one framework. Nothing here may be imported by
``core``/``llm``/``registry``/``tools`` (the layered architecture, enforced by import-linter).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType
from typing import Any, ClassVar, TypeVar, cast

from ..core import runtime
from ..core.config import TOKEN_AUTH_MODES, DonkeyConfig, LlmProxyAuth
from ..core.errors import ConfigError
from ..core.masking import masked
from ..core.refusals import TypedRefusals
from ..core.transport import (
    DonkeyAsyncClient,
    DonkeyAsyncClientView,
    DonkeyClient,
    DonkeyClientView,
    build_sync_http_client,
    checked_llm_config,
    proxy_api_key,
    proxy_auth_headers,
)
from ..llm.client import openai_http_client, openai_sync_http_client
from . import ADAPTERS, AdapterCapabilities, missing_framework_error, typed_refusals


class Adapter(ABC):
    """Base holding the config and the shared HTTP client every adapter needs,
    and declaring the contract every adapter shares (BG §1.8, #726):
    :class:`~donkey_kit.integrations.AdapterProtocol`'s
    :meth:`connection_kwargs` and :meth:`capabilities`, plus ``extra``.

    A subclass declares :attr:`factories`, and every ``connection_kwargs()``
    builds on :meth:`_connection`, so the config validation and the token-mode
    guards run in one place (ADR 0004)."""

    @property
    def extra(self) -> str:
        """The pip extra that provides this adapter's framework, for the curated
        ImportError raised when it is not installed (BG §1.8). Read from the
        ``ADAPTERS`` roster, the one place it is declared; empty for an adapter
        not on it."""
        cls = type(self).__name__
        return next((s.extra for s in ADAPTERS.values() if s.cls == cls), "")

    #: Each factory method's name, mapped to the frozen capabilities of the
    #: native object it builds (#726). The first entry is the default factory,
    #: the one ``connection_kwargs()`` configures. Declared once per class as a
    #: read-only mapping and never changed on an instance (#741).
    factories: ClassVar[Mapping[str, AdapterCapabilities]]

    #: Whether a governed model call through the default factory reaches
    #: ``donkey.last_call`` (#362): ``capabilities().observes_last_call``, set
    #: from :attr:`factories` when the class is defined and kept for the callers
    #: that read it directly. A ``False`` here is why ``donkey.last_call``
    #: reports "not available on this surface" rather than a bare ``None``
    #: (hazard #3), and it is the fact the conformance suite asserts as an
    #: exemption.
    observes_last_call: ClassVar[bool]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "factories" in cls.__dict__:
            if not cls.factories:
                raise TypeError(f"{cls.__name__}.factories declares no factory")
            # Read-only whatever mapping the subclass wrote (#741).
            cls.factories = MappingProxyType(dict(cls.factories))
            cls.observes_last_call = cls.capabilities().observes_last_call
        elif not hasattr(cls, "factories") and not _still_abstract(cls):
            # A concrete adapter with no capabilities would fail later, at
            # donkey.last_call or capabilities(), far from the mistake.
            raise TypeError(f"{cls.__name__} must declare factories (ADR 0004)")

    @classmethod
    def capabilities(cls, factory: str | None = None) -> AdapterCapabilities:
        """The frozen :class:`~donkey_kit.integrations.AdapterCapabilities` of
        ``factory`` (a factory method's name), by default the default factory's.
        Raises ``ValueError`` for a name that is not one of :attr:`factories`."""
        if factory is None:
            return next(iter(cls.factories.values()))
        try:
            return cls.factories[factory]
        except KeyError:
            raise ValueError(
                f"{cls.__name__} has no factory {factory!r}; "
                f"its factories are {', '.join(cls.factories)}"
            ) from None

    def __init__(
        self,
        cfg: DonkeyConfig,
        http_client: DonkeyAsyncClient,
        sync_http_client: Callable[[], DonkeyClient] | None = None,
    ) -> None:
        self._cfg = cfg
        self._http = http_client
        # ``Donkey`` passes its own accessor so it owns the blocking client's
        # lifecycle; standalone use falls back to one owned here.
        self._sync_http = sync_http_client or self._own_sync_client
        self._owned_sync: DonkeyClient | None = None
        # The http_client each openai kwarg slot was given, with the shared client
        # it sends through: one per adapter, as a view is one per shared client.
        self._kwarg_clients: dict[str, tuple[object, Any]] = {}

    @contextmanager
    def _native_import(self) -> Iterator[None]:
        """Wrap a factory's lazy framework import: a missing framework, or a
        missing dependency of it, raises the same curated ImportError as
        ``Donkey.<framework>``, never a bare ``ModuleNotFoundError`` (BG §1.8, #741)."""
        try:
            yield
        except ModuleNotFoundError as exc:
            raise missing_framework_error(self.extra, exc.name) from exc

    @abstractmethod
    def connection_kwargs(self) -> dict[str, Any]:
        """The governed kwargs for the framework's own client constructor,
        and the whole supported surface for a ``connection_kwargs()``-only
        framework (BG §1.8). Returned through
        :func:`~donkey_kit.core.masking.masked`, so printing it hides secrets."""

    @staticmethod
    def typed_refusals() -> TypedRefusals:
        """The typed-refusal bridge, :func:`donkey_kit.typed_refusals` (#724).

        Lets ``with donkey.strands.typed_refusals(): ...`` read naturally next
        to the adapter's factories. Every adapter shares this one bridge: no
        adapter holds classification logic of its own (ADR 0002)."""
        return typed_refusals()

    def _own_sync_client(self) -> DonkeyClient:
        if self._owned_sync is None:
            self._owned_sync = build_sync_http_client(self._cfg, origins=self._http.checked_origins)
        return self._owned_sync

    def close(self) -> None:
        """Close the blocking client built by this standalone adapter, if any.

        A client supplied by a Runtime and the shared async client stay with
        their original owner.
        """
        if self._owned_sync is not None:
            self._owned_sync.close()
            self._owned_sync = None

    async def aclose(self) -> None:
        """Close this adapter's owned blocking client in an async scope."""
        self.close()

    def _proxy_headers(self) -> dict[str, str]:
        """Default headers for a native OpenAI-compatible client pointed at the
        proxy: the client_id/client_secret consumer-auth pair plus
        any attribution headers (docs/verified-apis.md §2/§3)."""
        return proxy_auth_headers(self._cfg)

    def _proxy_api_key(self) -> str:
        """Value for the framework client's mandatory ``api_key`` slot (the proxy
        enforces the client_id/secret headers instead; see :func:`proxy_api_key`)."""
        return proxy_api_key(self._cfg)

    def http_client(self) -> DonkeyAsyncClientView:
        """The governed ``httpx.AsyncClient`` to hand a framework that takes one:
        a non-owning view of the shared client, so a framework that closes it
        leaves every other surface working (#733)."""
        return self._http.view()

    def sync_http_client(self) -> DonkeyClientView:
        """The blocking twin of :meth:`http_client`, for a framework that also
        makes sync calls. It shares the async client's checked endpoints."""
        return self._sync_http().view()

    # The names adapters used before the views were public.
    _http_client = http_client
    _sync_http_client = sync_http_client

    def _openai_kwarg_http_client(self) -> Any:
        """The ``http_client`` for a framework that builds its own ``AsyncOpenAI``
        from kwargs (LangGraph, LlamaIndex, Strands): the core ``httpx2`` bridge
        on ``openai>=3``, the shared client's view (:meth:`http_client`) before
        (#728). The bridge is reusable, because Strands closes the
        ``AsyncOpenAI`` it builds around this client after every request, and
        the same one is returned each time, as the view is."""
        return self._kwarg_client(
            "async", self._http, lambda: openai_http_client(self._http, reusable=True)
        )

    def _openai_kwarg_sync_http_client(self) -> Any:
        """Blocking twin of :meth:`_openai_kwarg_http_client`, for the ``OpenAI``
        a framework builds for its sync calls. It sends through the blocking
        shared client, which refuses in a token auth mode."""
        sync = self._sync_http()
        return self._kwarg_client(
            "sync", sync, lambda: openai_sync_http_client(sync, reusable=True)
        )

    def _kwarg_client(self, slot: str, shared: object, build: Callable[[], Any]) -> Any:
        held = self._kwarg_clients.get(slot)
        if held is None or held[0] is not shared:
            held = (shared, build())
            self._kwarg_clients[slot] = held
        return held[1]

    def _proxy_openai_client(self, base_url: str | None = None) -> Any:
        """A native ``AsyncOpenAI`` bound to the proxy (or to ``base_url``, an
        override already allowed through :meth:`_allow_endpoints`) that sends
        through the shared client, for a framework that takes a pre-built OpenAI
        client rather than an ``http_client``."""
        conn = self._connection()
        with self._native_import():
            from openai import AsyncOpenAI

        # openai>=3 is built on httpx2: it gets the core httpx2 bridge, openai<3 the
        # shared client's view (#728). ``cast(Any, …)`` because the argument type
        # differs by installed major (#597).
        return AsyncOpenAI(
            base_url=base_url or conn["base_url"],
            api_key=conn["api_key"],
            default_headers=conn["default_headers"],
            http_client=cast(Any, openai_http_client(self._http)),
            max_retries=0,  # we retry in transport (BG §1.1)
        )

    def _proxy_openai_client_kwarg(self, name: str, base_url: str | None = None) -> dict[str, Any]:
        """``{name: self._proxy_openai_client(base_url)}``, or ``{}`` without the
        OpenAI SDK. For frameworks that depend on it, so their
        ``connection_kwargs()`` still builds on a base install."""
        try:
            return {name: self._proxy_openai_client(base_url)}
        except ImportError:
            return {}

    def _allow_endpoints(self, overrides: Mapping[str, Any], *names: str) -> None:
        """Check each URL override in ``overrides`` under ``names`` — a URL passed
        in code to a factory — with the config's https check, then let the
        shared client send the proxy credentials to it. Called before the
        framework is imported, so a refused URL fails the same with or without
        the framework installed."""
        for name in names:
            url = overrides.get(name)
            if url is not None:
                self._http.allow_endpoint(str(url), name=name)

    def _connection(self, factory: str | None = None) -> dict[str, Any]:
        """The three governed values every OpenAI-compatible client needs to
        reach the proxy: ``base_url``, an ``api_key`` slot, and the verified
        consumer-auth ``default_headers``, for ``factory`` (by default the
        default factory). Every ``connection_kwargs()`` builds on this, so the
        guards below run once, in one place, for every adapter (#726):

        - a ``"framework"``-transport factory is refused in a token auth mode
          (jwt or bearer), since the token rides only the shared client (#828);
        - :func:`~donkey_kit.core.transport.checked_llm_config` validates the
          proxy config and refuses a token mode with no ``AuthProvider`` on the
          shared client, which would otherwise send no token and 401 (#836).

        It does not refuse a token mode for a ``sync``-capable factory: each of
        those also has an async path, and the blocking client refuses each
        blocking send itself (#736).

        Adapters map these onto their framework's own kwarg names in the public
        :meth:`connection_kwargs`, returned through
        :func:`~donkey_kit.core.masking.masked`, so printing it never shows the
        ``api_key`` or the secret header.
        """
        caps = self.capabilities(factory)
        mode = self._cfg.llm_proxy_auth
        if caps.transport == "framework" and mode in TOKEN_AUTH_MODES:
            raise self._token_mode_error(mode)
        cfg = checked_llm_config(self._cfg, self._http)
        return masked(
            {
                "base_url": cfg.llm_proxy_url,
                "api_key": self._proxy_api_key(),
                "default_headers": self._proxy_headers(),
            }
        )

    def _token_mode_error(self, mode: LlmProxyAuth) -> ConfigError:
        """The error for a token auth mode on a factory whose framework builds
        its own HTTP clients. An adapter may override it to name alternatives."""
        return ConfigError(
            f"{type(self).__name__} does not support llm_proxy_auth={mode!r}: the "
            "framework builds its own HTTP clients, so the rotating token, which "
            "rides only the SDK's shared client, never reaches the request. Use "
            "client-id auth with it."
        )


def _still_abstract(cls: type) -> bool:
    """Whether ``cls`` leaves an abstract method unimplemented. ``inspect.isabstract``
    cannot answer this inside ``__init_subclass__``: ``ABCMeta`` sets the new
    class's ``__abstractmethods__`` only after it returns."""
    return any(
        getattr(getattr(cls, name, None), "__isabstractmethod__", False)
        for name in Adapter.__abstractmethods__
    )


A = TypeVar("A", bound=Adapter)

# One cached default adapter per class, backing the module-level factories
# (e.g. ``from donkey_kit.integrations.langgraph import chat_model``). Each is
# built on the process-default runtime from core, never via ``Donkey`` — the
# layered import contract forbids ``integrations`` from importing the top
# package — so every factory shares one client, budget and auth (#725).
_DEFAULT_ADAPTERS: dict[type[Adapter], Adapter] = {}
_DEFAULT_ADAPTERS_LOCK = threading.Lock()


def default_adapter(cls: type[A]) -> A:
    """Return a process-wide default instance of ``cls`` on the process-default
    runtime (:func:`donkey_kit.core.runtime.default`): configured from the
    environment like ``Donkey.from_env()``, with its budget, control-plane auth
    and OTLP export, and sharing one governed HTTP client with every other
    module-level factory. Lets those factories work without an explicit
    :class:`~donkey_kit.Donkey` handle.

    The runtime is closed at interpreter exit. Prefer an explicit ``Donkey``
    when you need lifecycle control (``aclose``), non-env configuration, or a
    data-plane ``llm_auth`` provider (jwt or bearer mode).
    """
    rt = runtime.default()
    with _DEFAULT_ADAPTERS_LOCK:
        inst = _DEFAULT_ADAPTERS.get(cls)
        # Rebuild if the default runtime was closed and replaced since.
        # Same-module collaborator: Adapter's own client, compared by identity.
        if inst is None or inst._http is not rt.http:  # noqa: SLF001
            inst = cls(rt.config, rt.http, rt.sync_http)
            _DEFAULT_ADAPTERS[cls] = inst
    return cast(A, inst)


def _reset_for_tests() -> None:
    """Drop every cached default adapter (#750).

    :data:`_DEFAULT_ADAPTERS` keeps one instance per adapter class for the life
    of the process, so a test that calls a module-level factory (e.g.
    ``donkey_kit.integrations.langgraph.chat_model``) leaves an instance
    behind, still holding its closed runtime's config and clients, until a
    later test happens to rebuild it. Called from the autouse fixture in
    ``tests/conftest.py``; test code should never clear this by hand."""
    with _DEFAULT_ADAPTERS_LOCK:
        _DEFAULT_ADAPTERS.clear()
