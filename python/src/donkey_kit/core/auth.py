"""Auth (BG §1.1).

``AuthProvider`` is a tiny protocol so a customer can plug in their own vault.
We ship three implementations: :class:`AnypointConnectedApp` (OAuth2
client_credentials), :class:`StaticToken` (CI, token injected), and
:class:`ChainedAuth`.

The control-plane credential and the LLM-proxy credential are SEPARATE and must
not be conflated (BG §1.1).

VERIFICATION NOTE: the default token endpoint path from
``_verify.OAUTH_TOKEN_PATH`` is VERIFIED (plugin); see
``docs/verified-apis.md`` §1 and §12.1. The scopes each operation needs and the
operations that require an *admin* connected app with user context remain
UNVERIFIED. The latter path is not yet implemented and raises a
verification-blocked error where it is needed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from . import _verify
from .errors import AuthError

if TYPE_CHECKING:
    import httpx

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
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base = control_plane_url.rstrip("/")
        self._http = http_client
        # The default is VERIFIED (plugin); callers may override it for their environment.
        self._token_path = token_path or _verify.OAUTH_TOKEN_PATH.get()
        self._clock = clock
        self._cached: str | None = None
        self._expires_at: float = 0.0

    async def token(self) -> str:
        if self._cached is not None and self._clock() < self._expires_at:
            return self._cached
        return await self._fetch()

    async def invalidate(self) -> None:
        self._cached = None
        self._expires_at = 0.0

    async def _fetch(self) -> str:
        url = f"{self._base}{self._token_path}"
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
        body = resp.json()
        token: str | None = body.get("access_token")
        if not token:
            raise AuthError(
                "Token endpoint returned no access_token. The expected response shape "
                "includes access_token and expires_in (see docs/verified-apis.md §1 and "
                "§12.1); capture the unexpected response as a fixture (BG §1.5).",
                remediation=AuthError.connected_app_remediation,
                response=resp,
            )
        expires_in = float(body.get("expires_in", 3600))
        self._cached = token
        self._expires_at = self._clock() + max(0.0, expires_in - _EXPIRY_SAFETY_MARGIN_S)
        return token


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
            except Exception as exc:  # noqa: BLE001 - fall through to next provider
                last = exc
        raise AuthError(
            f"No auth provider yielded a token. Last error: {last}",
            remediation=AuthError.provider_chain_remediation,
        )

    async def invalidate(self) -> None:
        for provider in self._providers:
            await provider.invalidate()
