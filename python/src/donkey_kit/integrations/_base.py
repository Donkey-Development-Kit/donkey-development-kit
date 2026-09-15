"""Shared base for framework adapters.

Design rule (§3.1): adapters return NATIVE framework objects, never wrappers.
Each adapter depends on exactly one framework. Nothing here may be imported by
``core``/``llm``/``registry``/``tools`` (§1.1, enforced by import-linter).
"""

from __future__ import annotations

from typing import Any, TypeVar, cast

from ..core.config import DonkeyConfig
from ..core.transport import (
    DonkeyAsyncClient,
    attribution_headers,
    build_http_client,
    proxy_api_key,
    proxy_auth_headers,
)


class Adapter:
    """Base holding the config and the shared HTTP client every adapter needs."""

    #: pip extra that provides this adapter's framework, for the curated
    #: ImportError raised on access when it is not installed (§3.2).
    extra: str = ""

    #: Whether a governed model call through this adapter reaches ``donkey.last_call``
    #: (#362). True when the adapter hands the framework our shared
    #: :class:`DonkeyAsyncClient` (its ``_on_response`` observes the response);
    #: False when the SDK does not own the transport — LiteLLM-backed adapters
    #: (ADK, CrewAI) or adapters given only ``default_headers`` (LlamaIndex, MS
    #: Agent Framework). A ``False`` here is why ``donkey.last_call`` reports
    #: "not available on this surface" rather than a bare ``None`` (hazard #3),
    #: and it is the fact the conformance suite asserts as an exemption (§8.1).
    observes_last_call: bool = True

    def __init__(self, cfg: DonkeyConfig, http_client: DonkeyAsyncClient) -> None:
        self._cfg = cfg
        self._http = http_client

    def _attribution_headers(self) -> dict[str, str]:
        return attribution_headers(self._cfg)

    def _proxy_headers(self) -> dict[str, str]:
        """Default headers for a native OpenAI-compatible client pointed at the
        proxy: the LIVE-VERIFIED client_id/client_secret consumer-auth pair plus
        any attribution headers (docs §2/§3)."""
        return proxy_auth_headers(self._cfg)

    def _proxy_api_key(self) -> str:
        """Value for the framework client's mandatory ``api_key`` slot (the proxy
        enforces the client_id/secret headers instead; see :func:`proxy_api_key`)."""
        return proxy_api_key(self._cfg)

    def _http_client(self) -> DonkeyAsyncClient:
        return self._http

    def _require_proxy(self) -> DonkeyConfig:
        return self._cfg.validated(need="llm")

    def _openai_connection(self) -> dict[str, Any]:
        """The three governed values every OpenAI-compatible client needs to
        reach the proxy: ``base_url``, an ``api_key`` slot, and the verified
        consumer-auth ``default_headers``. Validates proxy config first.

        Adapters map these onto their framework's own kwarg names in the public
        :meth:`connection_kwargs`; both the ``donkey.<framework>.<factory>()``
        methods and the module-level factories build on top of this so there is
        one source of truth for the governed connection.
        """
        cfg = self._require_proxy()
        return {
            "base_url": cfg.llm_proxy_url,
            "api_key": self._proxy_api_key(),
            "default_headers": self._proxy_headers(),
        }


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
