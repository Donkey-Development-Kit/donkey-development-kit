"""Shared base for framework adapters.

Design rule (BG §1.8): adapters return NATIVE framework objects, never wrappers.
Each adapter depends on exactly one framework. Nothing here may be imported by
``core``/``llm``/``registry``/``tools`` (the layered architecture, enforced by import-linter).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

from ..core.config import DonkeyConfig
from ..core.masking import masked
from ..core.transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    attribution_headers,
    build_http_client,
    build_sync_http_client,
    proxy_api_key,
    proxy_auth_headers,
)


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
    observes_last_call: bool = True

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
        proxy: the LIVE-VERIFIED client_id/client_secret consumer-auth pair plus
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
        return self._cfg.validated(need="llm")

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
# (e.g. ``from donkey_kit.integrations.langgraph import chat_model``). Built
# straight from core (config + transport), never via ``Donkey`` — the layered
# import contract forbids ``integrations`` from importing the top package.
_DEFAULT_ADAPTERS: dict[type[Adapter], Adapter] = {}


def default_adapter(cls: type[A]) -> A:
    """Return a process-wide default instance of ``cls``, configured from the
    environment and sharing one governed HTTP client. Lets the module-level
    factories work without an explicit :class:`~donkey_kit.Donkey` handle.

    Prefer an explicit ``Donkey`` when you need lifecycle control (``aclose``)
    or non-env configuration; this trades that for a shorter call site.
    """
    inst = _DEFAULT_ADAPTERS.get(cls)
    if inst is None:
        cfg = DonkeyConfig.from_env()
        inst = cls(cfg, build_http_client(cfg, None))
        _DEFAULT_ADAPTERS[cls] = inst
    return cast(A, inst)
