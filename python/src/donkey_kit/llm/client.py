"""Raw, framework-free LLM client + model listing (BG §1.8, BG §1.1).

``donkey.llm.client()`` returns an ``AsyncOpenAI`` pointed at the proxy, sharing
the SDK's shared httpx client so attribution/correlation/auth headers and the
retry policy apply. ``client(sync=True)`` returns the blocking ``OpenAI`` with
the same governance. This is the framework-free surface; the per-framework
adapters live in ``integrations/``.

VERIFICATION NOTES (docs/verified-apis.md §2/§3):
  * The proxy base URL does **NOT** include ``/v1``; it is
    ``https://<ingress-gw>/<instance>/`` (e.g. ``…/openai-sdk/``) and the OpenAI
    SDK appends the route (``/responses`` etc.) directly.
  * Auth is a ``client_id`` + ``client_secret`` REQUEST-header pair
    (client-id-enforcement), NOT a bearer token. The OpenAI SDK still requires a
    non-empty ``api_key`` slot, which the proxy ignores.
  * A ``/models`` endpoint does **NOT** exist (confirmed ``404``). ``list_models``
    therefore never fabricates a ``/models`` path; ``live=True`` reports the
    verified absence rather than guessing.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from ..core.config import DonkeyConfig
from ..core.errors import ConfigError
from ..core.transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    build_sync_http_client,
    checked_llm_config,
    proxy_api_key,
    proxy_auth_headers,
)
from ..core.transport.views import built_on_httpx2
from .catalog import ModelHandle, heuristic_capabilities

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI

__all__ = ["LLMClient", "openai_http_client", "openai_sync_http_client"]


def _openai_on_httpx2() -> bool:
    """True when the installed ``openai`` is built on ``httpx2`` (3.0 and later):
    its ``DefaultAsyncHttpxClient`` is then an ``httpx2.AsyncClient`` (#728).
    False without ``openai``, so an adapter's ``connection_kwargs()`` still
    builds on an install without it (with the view, as before)."""
    try:
        import openai
    except ImportError:
        return False
    return built_on_httpx2(getattr(openai, "DefaultAsyncHttpxClient", None))


def openai_http_client(http: DonkeyAsyncClient, *, reusable: bool = False) -> object:
    """The ``http_client`` for an ``AsyncOpenAI``: the core ``httpx2`` bridge on
    ``openai>=3``, the shared client's non-owning view before (#728, #733).

    The one place that picks between them, for ``donkey.llm``, the adapters that
    pass a pre-built ``AsyncOpenAI`` and those whose framework builds its own
    from an ``http_client`` kwarg. ``reusable`` keeps the bridge usable after
    the framework closes it (see :func:`~donkey_kit.core.transport.httpx2.bridged_client`);
    a view always is."""
    if _openai_on_httpx2():
        from ..core.transport.httpx2 import bridged_client

        return bridged_client(http, reusable=reusable)
    return http.view()


def openai_sync_http_client(http: DonkeyClient, *, reusable: bool = False) -> object:
    """Blocking twin of :func:`openai_http_client`, for ``OpenAI``. Either
    client refuses to send in a token auth mode, as ``http`` does."""
    if _openai_on_httpx2():
        from ..core.transport.httpx2 import bridged_sync_client

        return bridged_sync_client(http, reusable=reusable)
    return http.view()


class LLMClient:
    """The framework-free proxy client factory."""

    def __init__(
        self,
        cfg: DonkeyConfig,
        http_client: DonkeyAsyncClient,
        sync_http_client: Callable[[], DonkeyClient] | None = None,
    ) -> None:
        self._cfg = cfg
        self._http = http_client
        # ``Donkey`` passes its own accessor so it owns the blocking transport's
        # lifecycle; standalone use falls back to one owned here.
        self._sync_http = sync_http_client or self._own_sync_client
        self._owned_sync: DonkeyClient | None = None

    def _own_sync_client(self) -> DonkeyClient:
        if self._owned_sync is None:
            self._owned_sync = build_sync_http_client(
                self._cfg, origins=self._http.checked_origins
            )
        return self._owned_sync

    def close(self) -> None:
        """Close the blocking client built by this standalone factory, if any.

        An injected blocking client and the shared async client stay caller-owned.
        """
        if self._owned_sync is not None:
            self._owned_sync.close()
            self._owned_sync = None

    async def aclose(self) -> None:
        """Close this factory's owned blocking client in an async scope."""
        self.close()

    @overload
    def client(self, *, sync: Literal[False] = ..., **kw: Any) -> AsyncOpenAI: ...

    @overload
    def client(self, *, sync: Literal[True], **kw: Any) -> OpenAI: ...

    def client(self, *, sync: bool = False, **kw: Any) -> AsyncOpenAI | OpenAI:
        """An OpenAI client pointed at the LLM proxy, using our shared http client
        so headers + retries apply.

        Defaults to ``AsyncOpenAI``. Pass ``sync=True`` for the blocking
        ``OpenAI``, which is governed identically — same base URL, same verified
        ``client_id``/``client_secret`` headers, same correlation ID and retry
        policy, via :class:`~donkey_kit.core.transport.DonkeyClient`.

        The two are declared as overloads on ``Literal`` rather than returning a
        union, so the call site narrows to one concrete class and editors keep
        offering completions on the result.

        A ``base_url`` override must pass the same https check as the configured
        proxy URL; it then receives the configured credentials.
        """

        # Validation plus the token-mode guards: a blocking client is refused,
        # and so is a shared client with no AuthProvider (#509, #836).
        checked_llm_config(self._cfg, self._http, sync=sync)
        sync_http = self._sync_http() if sync else None
        http = self._http if sync_http is None else sync_http
        if kw.get("base_url") is not None:
            http.allow_endpoint(str(kw["base_url"]), name="base_url")
        try:
            from openai import AsyncOpenAI, OpenAI
        except ImportError as exc:  # pragma: no cover - install-time guidance
            raise ImportError(
                "The raw LLM client needs the OpenAI SDK. Install it with:\n"
                '    pip install "donkey-kit[llm]"'
            ) from exc

        assert self._cfg.llm_proxy_url is not None  # validated() guarantees this
        shared: dict[str, Any] = {
            # no /v1 at ingress (docs/verified-apis.md §2) — verbatim
            "base_url": self._cfg.llm_proxy_url,
            "api_key": proxy_api_key(self._cfg),
            # client_id/secret (docs/verified-apis.md §2/§3)
            "default_headers": proxy_auth_headers(self._cfg),
            "max_retries": 0,  # we retry in transport (BG §1.1)
            **kw,
        }
        # openai>=3 is built on httpx2 and types http_client as an httpx2 client, so
        # it gets the core httpx2 bridge, which forwards every request through the
        # shared client (#728); openai<3 gets the shared client's non-owning view.
        # Either way closing the OpenAI client (``async with donkey.llm.client()``)
        # leaves the shared client open (#733). ``cast(Any, …)`` because the
        # argument type differs by installed major (#597).
        if sync_http is not None:
            return OpenAI(http_client=cast(Any, openai_sync_http_client(sync_http)), **shared)
        return AsyncOpenAI(http_client=cast(Any, openai_http_client(self._http)), **shared)

    async def list_models(self, *, live: bool = False) -> list[ModelHandle]:
        """List logical models the proxy exposes.

        The governed proxy has **no** catalog endpoint — ``GET /models`` returns
        ``404`` (docs/verified-apis.md §2): model-based-routing only routes requests
        that carry ``model`` in the body. So ``live=True`` cannot be satisfied,
        and we say so plainly rather than guess a path. Use :meth:`resolve` for a
        heuristic :class:`ModelHandle` from a known model id, or source the
        catalog from Exchange / provider config (BG §1.1).
        """

        if live:
            raise ConfigError(
                "The governed LLM proxy exposes no /models endpoint (GET /models → 404, verified "
                "docs/verified-apis.md §2): it only routes requests carrying `model` in the body. "
                "Live model listing is not available from the proxy. Use resolve(model_id) or "
                "source the catalog from Exchange/provider config."
            )
        raise ConfigError(
            "list_models() has no offline source of truth yet, and the proxy has no /models "
            "endpoint to enumerate (verified docs/verified-apis.md §2). Use resolve(model_id) to "
            "get a heuristic ModelHandle, or source models from Exchange (BG §1.1)."
        )

    def resolve(self, model_id: str, *, provider: str | None = None) -> ModelHandle:
        """A heuristic :class:`ModelHandle` for a known model id (BG §1.1)."""
        return ModelHandle(
            id=model_id,
            provider=provider,
            capabilities=heuristic_capabilities(model_id),
        )
