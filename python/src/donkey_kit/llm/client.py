"""Raw, framework-free LLM client + model listing (BG §1.8, BG §1.1).

``donkey.llm.client()`` returns an ``AsyncOpenAI`` pointed at the proxy, sharing
the SDK's shared httpx client so attribution/correlation/auth headers and the
retry policy apply. ``client(sync=True)`` returns the blocking ``OpenAI`` with
the same governance. This is the framework-free surface; the per-framework
adapters live in ``integrations/``.

VERIFICATION NOTES (LIVE-VERIFIED 2026-08-28, docs/verified-apis.md §2/§3):
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
    proxy_api_key,
    proxy_auth_headers,
)
from .catalog import ModelHandle, heuristic_capabilities

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI


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
            self._owned_sync = build_sync_http_client(self._cfg)
        return self._owned_sync

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
        """

        self._cfg.validated(need="llm")
        if self._cfg.llm_proxy_auth == "jwt":
            # The rotating JWT enters through an AuthProvider on the shared async
            # transport, never a config field (#509). Two things config alone
            # cannot check, enforced here where the provider is known:
            if sync:
                # Proposal 6 / AC 7: jwt mode is async-only. The blocking
                # DonkeyClient takes no AuthProvider (the protocol is async-only),
                # so a sync client could only send a stale or absent token — never
                # hand one back silently unauthenticated.
                raise ConfigError(
                    "JWT / model-wallet auth mode (llm_proxy_auth='jwt') is async-only: "
                    "the credential is a rotating JWT fetched from an async AuthProvider, "
                    "and the blocking client cannot await it. Use the async client — "
                    "`donkey.llm.client()` / `donkey.openai()` without sync=True — or switch "
                    "to client-id auth for a synchronous caller."
                )
            if self._http.token_provider is None:
                # AC 1: jwt mode with no provider attached fails with actionable guidance.
                raise ConfigError(
                    "llm_proxy_auth='jwt' requires an AuthProvider that supplies the "
                    "model-wallet JWT, but none is attached. Pass one when constructing "
                    "Donkey, e.g. `Donkey(llm_auth=StaticToken(jwt))` or a custom "
                    "AuthProvider that refreshes the token (see donkey_kit.core.auth)."
                )
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
        # openai 3.x retyped http_client to httpx2.AsyncClient (a distinct class from
        # a separate distribution); our DonkeyClient/DonkeyAsyncClient are httpx
        # subclasses. Typecheck-only mismatch: when an http_client is injected,
        # openai sends every request THROUGH it, so httpx2 never touches this path.
        # Runtime-verified end to end (async + sync) against openai 3.x by
        # tests/unit/test_llm_client_openai3_injection.py (#18); see
        # docs/verified-apis.md (openai >=3.0 row). No upper pin, by design (the
        # floors-never-ceilings rule). `cast(Any, …)` erases the argument type so
        # this typechecks clean under BOTH majors: a bare `# type: ignore` is
        # `unused-ignore` under openai<3 where the types already match (#597).
        if sync:
            return OpenAI(http_client=cast(Any, self._sync_http()), **shared)
        return AsyncOpenAI(http_client=cast(Any, self._http), **shared)

    async def list_models(self, *, live: bool = False) -> list[ModelHandle]:
        """List logical models the proxy exposes.

        The governed proxy has **no** catalog endpoint — ``GET /models`` returns
        ``404`` (LIVE-VERIFIED, docs/verified-apis.md §2): model-based-routing only routes requests
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
