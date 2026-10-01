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
from ..core.config import DonkeyConfig
from ..core.errors import ConfigError
from ..core.masking import masked
from ..core.transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    attribution_headers,
    build_sync_http_client,
    missing_jwt_provider_error,
    proxy_api_key,
    proxy_auth_headers,
)
from . import missing_framework_error


class Adapter(ABC):
    """Base holding the config and the shared HTTP client every adapter needs,
    and declaring the contract every adapter shares (BG §1.8): ``extra``,
    ``observes_last_call`` and :meth:`connection_kwargs`."""

    #: pip extra that provides this adapter's framework, for the curated
    #: ImportError raised on access when it is not installed (BG §1.8).
    extra: str = ""

    #: Whether a governed model call through this adapter reaches ``donkey.last_call``
    #: (#362). True when the adapter hands the framework our shared
    #: :class:`DonkeyAsyncClient`, directly or through the ``_httpx2_bridge``
    #: (its ``_on_response`` observes the response);
    #: False when the SDK does not own the transport — the framework builds its own
    #: client (ADK ``model()`` via LiteLLM, CrewAI via its native OpenAI provider) or the
    #: adapter is given only ``default_headers`` (LlamaIndex, MS Agent Framework).
    #: A ``False`` here is why ``donkey.last_call`` reports "not available on this
    #: surface" rather than a bare ``None`` (hazard #3),
    #: and it is the fact the conformance suite asserts as an exemption (the conformance kit).
    #: It describes the adapter's default factory and its ``connection_kwargs()``;
    #: a factory that routes differently is listed in
    #: :attr:`factory_observes_last_call`. Neither is ever changed on an instance (#741).
    observes_last_call: bool = True

    #: Per-factory overrides of :attr:`observes_last_call`, keyed by method name
    #: (ADK ``gemini()`` observes where ``model()`` does not). Read-only.
    factory_observes_last_call: ClassVar[Mapping[str, bool]] = MappingProxyType({})

    #: Whether this adapter's requests can carry the rotating model-wallet JWT
    #: in jwt auth mode (#509): true when every governed call sends through the
    #: shared client, which adds it per send. False when the framework builds its
    #: own client from a static header snapshot (CrewAI), so in jwt mode every
    #: factory and :meth:`connection_kwargs` raise ``ConfigError`` instead (#835).
    carries_jwt: ClassVar[bool] = True

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
        # Which factories with a per-factory capability have built an object, so
        # observing_last_call() answers for what was used, not what was called last.
        self._built: set[str] = set()

    def _record_factory(self, name: str) -> None:
        self._built.add(name)

    def observing_last_call(self) -> bool:
        """Whether a model call through anything this adapter built can reach
        ``donkey.last_call``: true if any factory it was used through observes.
        Before any such factory is used, the class's :attr:`observes_last_call`."""
        if not self._built:
            return self.observes_last_call
        return any(
            self.factory_observes_last_call.get(name, self.observes_last_call)
            for name in self._built
        )

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

    def _own_sync_client(self) -> DonkeyClient:
        if self._owned_sync is None:
            self._owned_sync = build_sync_http_client(
                self._cfg, origins=self._http.checked_origins
            )
        return self._owned_sync

    def _attribution_headers(self) -> dict[str, str]:
        return attribution_headers(self._cfg)

    def _proxy_headers(self) -> dict[str, str]:
        """Default headers for a native OpenAI-compatible client pointed at the
        proxy: the client_id/client_secret consumer-auth pair plus
        any attribution headers (docs/verified-apis.md §2/§3)."""
        return proxy_auth_headers(self._cfg)

    def _proxy_api_key(self) -> str:
        """Value for the framework client's mandatory ``api_key`` slot (the proxy
        enforces the client_id/secret headers instead; see :func:`proxy_api_key`)."""
        return proxy_api_key(self._cfg)

    def _http_client(self) -> DonkeyAsyncClient:
        return self._http

    def _sync_http_client(self) -> DonkeyClient:
        """The shared blocking client, for a framework that also makes sync
        calls. It shares the async client's checked endpoints."""
        return self._sync_http()

    def _proxy_openai_client(self, base_url: str | None = None) -> Any:
        """A native ``AsyncOpenAI`` bound to the proxy (or to ``base_url``, an
        override already allowed through :meth:`_allow_endpoints`) that sends
        through the shared client, for a framework that takes a pre-built OpenAI
        client rather than an ``http_client``."""
        conn = self._openai_connection()
        with self._native_import():
            from openai import AsyncOpenAI

        # openai 3.x retyped http_client to httpx2.AsyncClient (a distinct class from a
        # separate distribution); our DonkeyAsyncClient is an httpx subclass, duck-typed
        # at runtime. Typecheck-only mismatch — docs/verified-apis.md (openai >=3.0 row).
        # `cast(Any, …)` erases the argument type so this typechecks clean under BOTH
        # majors; a bare `# type: ignore` is `unused-ignore` under openai<3 (#597).
        return AsyncOpenAI(
            base_url=base_url or conn["base_url"],
            api_key=conn["api_key"],
            default_headers=conn["default_headers"],
            http_client=cast(Any, self._http_client()),
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

    def _require_proxy(self) -> DonkeyConfig:
        """Validate the proxy config, and in jwt mode refuse before any request
        a form that would send without the JWT (#835): one that cannot carry it,
        or a shared client with no provider to supply it (the module-level
        factories' default runtime, or a ``Donkey`` built without ``llm_auth``)."""
        cfg = self._cfg.validated(need="llm")
        if cfg.llm_proxy_auth == "jwt":
            if not self.carries_jwt:
                raise ConfigError(
                    f"The {self.extra} adapter cannot carry the rotating model-wallet "
                    "JWT in llm_proxy_auth='jwt' mode: the framework builds its own "
                    "HTTP client from a static header snapshot, so its requests would "
                    "reach the proxy without the JWT. Use a surface that sends through "
                    "the SDK's shared client — `donkey.llm.client()`, LangGraph, "
                    "Strands, OpenAI Agents, Anthropic, ADK, LlamaIndex or Agent "
                    "Framework (async calls) — or switch to client-id auth."
                )
            if self._http.token_provider is None:
                raise missing_jwt_provider_error()
        return cfg

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

    def _openai_connection(self) -> dict[str, Any]:
        """The three governed values every OpenAI-compatible client needs to
        reach the proxy: ``base_url``, an ``api_key`` slot, and the verified
        consumer-auth ``default_headers``. Validates proxy config first.

        Adapters map these onto their framework's own kwarg names in the public
        :meth:`connection_kwargs`; both the ``donkey.<framework>.<factory>()``
        methods and the module-level factories build on top of this so there is
        one source of truth for the governed connection.

        Every ``connection_kwargs()`` returns its mapping through
        :func:`~donkey_kit.core.masking.masked`, so printing it never shows the
        ``api_key`` or the secret header.
        """
        cfg = self._require_proxy()
        return masked(
            {
                "base_url": cfg.llm_proxy_url,
                "api_key": self._proxy_api_key(),
                "default_headers": self._proxy_headers(),
            }
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
    data-plane ``llm_auth`` provider (jwt mode).
    """
    rt = runtime.default()
    with _DEFAULT_ADAPTERS_LOCK:
        inst = _DEFAULT_ADAPTERS.get(cls)
        # Rebuild if the default runtime was closed and replaced since.
        if inst is None or inst._http is not rt.http:
            inst = cls(rt.config, rt.http, rt.sync_http)
            _DEFAULT_ADAPTERS[cls] = inst
    return cast(A, inst)
