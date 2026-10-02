"""The governed runtime: one config, its credentials, budget and shared clients.

:class:`Runtime` owns everything a :class:`~donkey_kit.Donkey` builds before it
hands out a surface — the resolved config, the OTLP export bootstrap, the
control-plane auth provider, the :class:`~donkey_kit.core.budget.Budget`, and one
shared client per credential plane (BG §1.1) — plus their close. ``Donkey``
wraps one; :func:`default` is the process-wide instance behind the module-level
adapter factories (``donkey_kit.integrations.langgraph.chat_model()`` and
friends, #725).

It lives in ``core`` so ``integrations`` can reach it without importing the top
package, which the layered architecture forbids.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import functools
import threading
from contextlib import AsyncExitStack

from .auth import AnypointConnectedApp, AuthProvider, EndpointCheckedAuth
from .budget import Budget
from .config import TOKEN_AUTH_MODES, DonkeyConfig
from .telemetry import configure_otlp_export
from .transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    build_http_client,
    build_sync_http_client,
)


class Runtime:
    """Config, auth, budget and the shared transports for one governed handle.

    ``auth`` is the control-plane provider (built from the connected-app
    credentials when omitted); ``llm_auth`` is the data-plane credential, used
    only in the ``jwt`` (#509) and ``bearer`` (#836) modes. See
    :class:`~donkey_kit.Donkey` for the public contract.
    """

    def __init__(
        self,
        config: DonkeyConfig | None = None,
        *,
        auth: AuthProvider | None = None,
        llm_auth: AuthProvider | None = None,
    ) -> None:
        self._cfg = config or DonkeyConfig.from_env()
        # Zero-config OTLP export (BG §1.6, #194): installs an exporter when an
        # OTEL_EXPORTER_OTLP_ENDPOINT is set and telemetry is on; a no-op (and
        # never an error) otherwise. This is the single funnel — every Donkey and
        # the default runtime pass through here — and it is idempotent.
        configure_otlp_export(self._cfg)
        self._owned_auth_http: DonkeyAsyncClient | None = None
        if auth is None:
            self._auth, self._owned_auth_http = _default_auth(self._cfg)
        else:
            self._auth = auth
        # One shared client per credential plane (BG §1.1). The data-plane (LLM
        # proxy) client carries only the data-plane credential: the rotating
        # token from ``llm_auth`` in the jwt (#509) and bearer (#836) modes, and no token
        # provider in the default client-id mode, which authenticates on the
        # client_id/client_secret header pair. The control-plane provider never
        # rides it; it drives a separate client for the registry and other
        # Anypoint platform calls.
        self._llm_auth = llm_auth
        data_plane_auth = llm_auth if self._cfg.llm_proxy_auth in TOKEN_AUTH_MODES else None
        if data_plane_auth is not None and self._cfg.llm_proxy_auth == "bearer":
            # Bearer mode (#836): check the endpoint before every token, so a
            # raw request through the shared client is covered as well as the
            # factories, which run the same check through validated().
            data_plane_auth = EndpointCheckedAuth(
                data_plane_auth, functools.partial(self._cfg.check_endpoints, need="llm")
            )
        # One Budget per runtime (never global, BG §1.3 / #185): both data-plane
        # transports feed it in-band from every response's x-token-* headers.
        self._budget = Budget()
        self._http: DonkeyAsyncClient = build_http_client(
            self._cfg, data_plane_auth, budget=self._budget
        )
        control_plane_auth = self._auth
        if auth is not None:
            # A caller-supplied provider is set in code, so its token counts as
            # coming from outside the project config files (config resolution).
            control_plane_auth = EndpointCheckedAuth(
                auth,
                functools.partial(
                    self._cfg.check_endpoints,
                    need="control_plane",
                    code_credential="the token from the Donkey(auth=...) provider",
                ),
            )
        self._control_http: DonkeyAsyncClient = build_http_client(
            self._cfg, control_plane_auth, control_plane=True
        )
        # Built only if someone asks for a blocking client, so the common async
        # path never opens a connection pool it will not use.
        self._sync_http: DonkeyClient | None = None

    @property
    def config(self) -> DonkeyConfig:
        """The resolved configuration."""
        return self._cfg

    @property
    def auth(self) -> AuthProvider | None:
        """The control-plane provider (connected-app or caller-supplied)."""
        return self._auth

    @property
    def llm_auth(self) -> AuthProvider | None:
        """The data-plane provider, used only in the ``jwt`` and ``bearer`` modes."""
        return self._llm_auth

    @property
    def budget(self) -> Budget:
        """The token-budget window both data-plane transports update (BG §1.3)."""
        return self._budget

    @property
    def http(self) -> DonkeyAsyncClient:
        """The shared data-plane (LLM proxy) client."""
        return self._http

    @property
    def control_http(self) -> DonkeyAsyncClient:
        """The shared control-plane (Anypoint platform) client."""
        return self._control_http

    @property
    def owned_auth_http(self) -> DonkeyAsyncClient | None:
        """The token-fetch client this runtime built for its own connected-app
        provider, if any (a caller-supplied ``auth`` stays caller-owned)."""
        return self._owned_auth_http

    @property
    def built_sync_http(self) -> DonkeyClient | None:
        """The blocking client if it has been built, without building it."""
        return self._sync_http

    def sync_http(self) -> DonkeyClient:
        """The shared blocking client, built on first use."""
        if self._sync_http is None:
            # Same Budget object as the async client, so a blocking caller updates
            # the budget on identical terms (BG §1.3, #185).
            self._sync_http = build_sync_http_client(
                self._cfg, budget=self._budget, origins=self._http.checked_origins
            )
        return self._sync_http

    async def aclose(self) -> None:
        """Close every transport this runtime owns: the data-plane and
        control-plane clients, the connected-app token-fetch client it built,
        and the blocking client. Each one is closed even if an earlier close
        raises."""
        auth_http = self._owned_auth_http
        self._owned_auth_http = None
        async with AsyncExitStack() as stack:
            # Callbacks unwind last-in, first-out: data plane first, blocking last.
            stack.callback(self.close)
            if auth_http is not None:
                stack.push_async_callback(auth_http.aclose)
            stack.push_async_callback(self._control_http.aclose)
            stack.push_async_callback(self._http.aclose)

    def close(self) -> None:
        """Close the blocking transport. The async transports need :meth:`aclose`."""
        if self._sync_http is not None:
            self._sync_http.close()
            self._sync_http = None


def _default_auth(
    cfg: DonkeyConfig,
) -> tuple[AuthProvider | None, DonkeyAsyncClient | None]:
    """Build control-plane auth when credentials are present. The LLM proxy
    credential is separate and handled by the OpenAI client (`BG §1.1`)."""
    if cfg.client_id and cfg.client_secret:
        # token fetches need no auth
        http_client = build_http_client(cfg, None, control_plane=True)
        auth = AnypointConnectedApp.from_config(cfg, http_client=http_client)
        return auth, http_client
    return None, None


# --- the process-default runtime ---------------------------------------------

_DEFAULT: Runtime | None = None
_DEFAULT_LOCK = threading.Lock()
_ATEXIT_REGISTERED = False


def default() -> Runtime:
    """The process-wide runtime, configured from the environment on first use —
    equivalent to the one ``Donkey.from_env()`` builds. Backs the module-level
    adapter factories so they share one client, budget and auth (#725).

    Built lazily under a lock, so concurrent first calls get the same instance,
    and closed at interpreter exit. Prefer an explicit ``Donkey`` when you need
    lifecycle control or non-env configuration.
    """
    global _DEFAULT, _ATEXIT_REGISTERED
    rt = _DEFAULT
    if rt is not None:
        return rt
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = Runtime()
            if not _ATEXIT_REGISTERED:
                atexit.register(close_default)
                _ATEXIT_REGISTERED = True
        return _DEFAULT


def close_default() -> None:
    """Close and drop the process-default runtime; the next :func:`default`
    builds a fresh one from the environment. Registered with :mod:`atexit`.

    The async transports are closed on a fresh event loop. When called from
    inside a running loop, only the blocking client can be closed here; await
    ``default().aclose()`` instead in that case.
    """
    global _DEFAULT
    with _DEFAULT_LOCK:
        rt, _DEFAULT = _DEFAULT, None
    if rt is None:
        return
    rt.close()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Best effort at exit: a pool left over from an earlier, already-closed
        # loop may refuse to close cleanly, and that must not mask the exit.
        with contextlib.suppress(Exception):
            asyncio.run(rt.aclose())
