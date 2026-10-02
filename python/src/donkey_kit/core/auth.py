"""Auth (BG §1.1).

``AuthProvider`` is a tiny protocol so a customer can plug in their own vault.
We ship three implementations: :class:`AnypointConnectedApp` (OAuth2
client_credentials), :class:`StaticToken` (CI, token injected), and
:class:`ChainedAuth`.

The control-plane credential and the LLM-proxy credential are SEPARATE and must
not be conflated (BG §1.1).

VERIFICATION NOTE: the default token endpoint path is
``_verify.OAUTH_TOKEN_PATH``; see ``docs/verified-apis.md`` §1 and §12.1. The
scopes each operation needs and the operations that require an *admin*
connected app with user context remain UNVERIFIED. The latter path is not yet
implemented and raises a verification-blocked error where it is needed.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from . import _verify
from .endpoints import require_secure_url
from .errors import AuthError, ConfigError

if TYPE_CHECKING:
    import httpx

    from .config import DonkeyConfig

__all__ = [
    "AnypointConnectedApp",
    "AuthProvider",
    "ChainedAuth",
    "EndpointCheckedAuth",
    "StaticToken",
]

_log = logging.getLogger(__name__)

_EXPIRY_SAFETY_MARGIN_S = 60.0


@runtime_checkable
class AuthProvider(Protocol):
    async def token(self) -> str: ...
    async def invalidate(self) -> None: ...


class StaticToken(AuthProvider):
    """A token injected out-of-band (e.g. CI). Never refreshes."""

    def __init__(self, token: str) -> None:
        self._token = token

    async def token(self) -> str:
        return self._token

    async def invalidate(self) -> None:
        # A static token cannot be refreshed; invalidation is a no-op. Callers
        # relying on refresh should use AnypointConnectedApp.
        return None


class AnypointConnectedApp(AuthProvider):
    """OAuth2 client_credentials against the Anypoint token endpoint.

    Caches the token in memory with a 60s safety margin before expiry. On a 401
    from any downstream call, ``invalidate()`` then retry exactly once (the
    retry is performed by the transport layer, BG §1.1).
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        control_plane_url: str,
        http_client: httpx.AsyncClient,
        token_path: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        endpoint_check: Callable[[], None] | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base = control_plane_url.rstrip("/")
        self._http = http_client
        # The default is from core/_verify (docs/verified-apis.md §12.1); callers may
        # override it for their environment.
        self._token_path = token_path or _verify.OAUTH_TOKEN_PATH
        self._clock = clock
        # Runs before every token POST; raises ConfigError to stop it.
        self._endpoint_check = endpoint_check
        self._cached: str | None = None
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_config(
        cls,
        cfg: DonkeyConfig,
        *,
        http_client: httpx.AsyncClient,
        token_path: str | None = None,
    ) -> AnypointConnectedApp:
        """Build from a resolved config. Before the first token POST, the
        control-plane endpoint is checked against where the credentials came from
        (:meth:`DonkeyConfig.check_endpoints`), so nothing is sent to a host the
        credentials are not bound to. The check is deferred to the first fetch so
        an unused control plane never blocks LLM-only use."""
        if not (cfg.client_id and cfg.client_secret):
            raise ConfigError(
                "The Anypoint connected app needs client_id and client_secret "
                "(env ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET)."
            )
        return cls(
            client_id=cfg.client_id,
            client_secret=cfg.client_secret,
            control_plane_url=cfg.control_plane_url,
            http_client=http_client,
            token_path=token_path,
            endpoint_check=functools.partial(cfg.check_endpoints, need="control_plane"),
        )

    async def token(self) -> str:
        cached = self._fresh()
        if cached is not None:
            return cached
        # Concurrent callers wait on one fetch instead of each POSTing (#813);
        # the first to get the lock fetches, the rest find the fresh token.
        async with self._fetch_lock():
            cached = self._fresh()
            if cached is not None:
                return cached
            return await self._fetch()

    def _fresh(self) -> str | None:
        if self._cached is not None and self._clock() < self._expires_at:
            return self._cached
        return None

    def _fetch_lock(self) -> asyncio.Lock:
        """The lock for the running event loop. An ``asyncio.Lock`` binds to the
        first loop that waits on it, so one provider used from successive
        ``asyncio.run()`` calls gets a fresh lock per loop."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def invalidate(self) -> None:
        _log.debug("connected-app token invalidated; the next call fetches a fresh one")
        self._cached = None
        self._expires_at = 0.0

    async def _fetch(self) -> str:
        url = f"{self._base}{self._token_path}"
        require_secure_url(url, name="token endpoint")
        if self._endpoint_check is not None:
            self._endpoint_check()
        # The URL only: the form body carries the client secret (#717).
        _log.debug("fetching a connected-app token from %s", url)
        resp = await self._http.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code in (401, 403):
            raise AuthError(
                "Anypoint token request rejected. Verify the connected-app "
                "client_id/secret and that the app has the scopes the operation "
                "needs (see docs/verified-apis.md §1).",
                remediation=AuthError.connected_app_remediation,
                response=resp,
            )
        if resp.status_code >= 400:
            raise AuthError(
                f"Anypoint token endpoint returned {resp.status_code} for "
                f"{self._token_path!r}. Check that the control-plane host is reachable, "
                "that any token_path override is correct, and that connected-app "
                "requirements are met (see docs/verified-apis.md §1 and §12.1).",
                remediation=AuthError.connected_app_remediation,
                response=resp,
            )
        try:
            body = resp.json()
            token = body.get("access_token")
            expires_in = float(body.get("expires_in", 3600))
            if token is not None and not isinstance(token, str):
                raise TypeError("access_token is not a string")
        except (ValueError, TypeError, AttributeError) as exc:
            # A body that is not JSON, not an object, or carries mistyped fields
            # (#813). The response is attached; the parse error is not chained,
            # since its message can repeat the body (see DonkeyError.framework_error).
            raise AuthError(
                f"Anypoint token endpoint returned a malformed token response "
                f"({type(exc).__name__}). The expected response is a JSON object "
                "with a string access_token and a numeric expires_in (see "
                "docs/verified-apis.md §1 and §12.1); capture the unexpected "
                "response as a fixture (BG §1.5).",
                remediation=AuthError.connected_app_remediation,
                response=resp,
            ) from None
        if not token:
            raise AuthError(
                "Token endpoint returned no access_token. The expected response shape "
                "includes access_token and expires_in (see docs/verified-apis.md §1 and "
                "§12.1); capture the unexpected response as a fixture (BG §1.5).",
                remediation=AuthError.connected_app_remediation,
                response=resp,
            )
        self._cached = token
        self._expires_at = self._clock() + max(0.0, expires_in - _EXPIRY_SAFETY_MARGIN_S)
        return token


class EndpointCheckedAuth(AuthProvider):
    """Runs ``endpoint_check`` before each ``token()`` of ``provider``, so a
    :class:`ConfigError` stops the token before it is fetched or sent."""

    def __init__(self, provider: AuthProvider, endpoint_check: Callable[[], None]) -> None:
        self._provider = provider
        self._endpoint_check = endpoint_check

    async def token(self) -> str:
        self._endpoint_check()
        return await self._provider.token()

    async def invalidate(self) -> None:
        await self._provider.invalidate()


class ChainedAuth(AuthProvider):
    """Try providers in order; the first that yields a token wins."""

    def __init__(self, *providers: AuthProvider) -> None:
        if not providers:
            raise ValueError("ChainedAuth requires at least one provider.")
        self._providers = providers

    async def token(self) -> str:
        last: Exception | None = None
        for provider in self._providers:
            try:
                return await provider.token()
            # Blind on purpose: a custom provider (e.g. a vault plugin) may raise
            # anything, and the chain's contract is to try the next one. The last
            # error is surfaced in the AuthError below, so nothing is lost.
            except Exception as exc:  # noqa: BLE001 - any provider failure falls through
                # Type only: a provider's message is not ours to vet for secrets.
                _log.debug(
                    "auth provider %s failed with %s; trying the next provider",
                    type(provider).__name__,
                    type(exc).__name__,
                )
                last = exc
        raise AuthError(
            f"No auth provider yielded a token. Last error: {last}",
            remediation=AuthError.provider_chain_remediation,
        )

    async def invalidate(self) -> None:
        for provider in self._providers:
            await provider.invalidate()
