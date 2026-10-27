"""On-behalf-of identity: run governed calls as a user (BG §2.4, #215).

``donkey.as_user(id_token=...)`` trades the user's OIDC ``id_token`` for a
gateway-scoped access token with an RFC 8693 token-exchange grant, then binds
that token to a :class:`~contextvars.ContextVar` for the block. The transport
reads the binding per send and stamps the token on data-plane requests, so the
gateway sees the user, not the service identity::

    async with donkey.as_user(id_token=request.id_token):
        await agent.run(task)        # every governed model call is the user's

The binding is a contextvar like ``donkey.run(...)``: it reaches the
``asyncio`` tasks the block spawns, which copy the current context, and never
leaks into a concurrent task that entered no scope. Nested scopes restore the
outer user on exit.

**Unconfirmed wire contract.** Which Anypoint endpoint serves the grant and
which request header the gateway reads the exchanged token from are both
placeholders (docs/verified-apis.md §13, #255): ``token_exchange_path`` and
``user_token_header`` override them.

**No authorisation here.** The SDK exchanges and forwards a token; what the
user may do is decided by the gateway and the platform. Nothing in this module
inspects the token's claims or allows or refuses a call on its strength.

The same documented degradation as ``donkey.run(...)`` applies (BG §1.8): a
``connection_kwargs()``-only adapter that does not route through the shared
transport never sees the binding.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from . import _verify
from .config import TOKEN_AUTH_MODES
from .endpoints import require_secure_url
from .errors import AuthError, ConfigError

if TYPE_CHECKING:
    import httpx

    from .config import DonkeyConfig

__all__ = ["TokenExchange", "UserScope", "current_user", "user_token_header"]

_log = logging.getLogger(__name__)

# RFC 8693 §2.1 / §3: the grant and the subject token type for an OIDC id_token.
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
_ID_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:id_token"

# The same margin the connected-app token keeps before its expiry (core/auth).
_EXPIRY_SAFETY_MARGIN_S = 60.0
# Distinct users whose exchanged tokens are kept; the oldest is dropped first.
_MAX_CACHED_USERS = 1024

_REMEDIATION = (
    "Check that the id_token is a current OIDC id_token for this user, that the "
    "connected app (ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET) may run the "
    "token-exchange grant, and that token_exchange_path names the endpoint that "
    "serves it (docs/verified-apis.md §13)."
)


def user_token_header(cfg: DonkeyConfig) -> str:
    """The request header the exchanged user token rides in: the
    ``user_token_header`` override, else the placeholder default."""
    return cfg.user_token_header or _verify.USER_TOKEN_HEADER.get()


def _cache_key(id_token: str) -> str:
    # A digest, so the cache never holds the id_token itself.
    return hashlib.sha256(id_token.encode()).hexdigest()


class TokenExchange:
    """The RFC 8693 client: ``id_token`` in, gateway-scoped access token out.

    Posts the grant to the control-plane host at ``token_exchange_path``,
    authenticated with the connected-app ``client_id`` / ``client_secret``.
    Only the path is configurable, so the user's token goes only to the
    control-plane host the credentials are bound to
    (:meth:`DonkeyConfig.check_endpoints`).

    Exchanged tokens are cached per user, keyed by a digest of the id_token,
    until ``expires_in`` minus a 60 s margin. A response without
    ``expires_in`` is not cached: the next call exchanges again rather than
    guess a lifetime. Concurrent misses share one exchange.
    """

    def __init__(
        self,
        cfg: DonkeyConfig,
        *,
        http_client: httpx.AsyncClient,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = cfg
        self._http = http_client
        self._clock = clock
        self._cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def check(self) -> None:
        """Refuse a config the exchange can't run under, before any request.

        Raises:
            ConfigError: No connected-app credentials, a ``token_exchange_path``
                that is not a path, or the default ``Authorization`` header in
                the ``jwt`` / ``bearer`` modes, where the data-plane token
                already rides in it.
        """
        cfg = self._cfg
        if not (cfg.client_id and cfg.client_secret):
            raise ConfigError(
                "donkey.as_user() needs the connected-app client_id and client_secret "
                "(env ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET) to run the token exchange."
            )
        path = cfg.token_exchange_path
        if path is not None and not path.startswith("/"):
            raise ConfigError(
                f"token_exchange_path must be a path starting with '/', got {path!r}. "
                "The host is always the control-plane host."
            )
        header = user_token_header(cfg)
        if (
            cfg.llm_proxy_auth in TOKEN_AUTH_MODES
            and header.lower() == _verify.LLM_PROXY_WALLET_JWT_HEADER.lower()
        ):
            raise ConfigError(
                f"donkey.as_user() can't put the user token in {header!r} in "
                f"{cfg.llm_proxy_auth!r} mode: the data-plane token already rides there. "
                "Set user_token_header (env DONKEY_USER_TOKEN_HEADER) to the header "
                "the gateway reads the user token from."
            )

    def cached(self, id_token: str) -> str | None:
        """The exchanged token for ``id_token`` if one is cached and current."""
        key = _cache_key(id_token)
        entry = self._cache.get(key)
        if entry is None:
            return None
        token, expires_at = entry
        if self._clock() >= expires_at:
            del self._cache[key]
            return None
        return token

    async def token(self, id_token: str) -> str:
        """Return the gateway-scoped token for ``id_token``, exchanging on a miss.

        Raises:
            AuthError: The exchange endpoint refused the id_token or returned an
                unexpected response.
            ConfigError: See :meth:`check`, or the control-plane endpoint is not
                one these credentials may be sent to.
        """
        cached = self.cached(id_token)
        if cached is not None:
            return cached
        async with self._exchange_lock():
            cached = self.cached(id_token)
            if cached is not None:
                return cached
            return await self._exchange(id_token)

    def _exchange_lock(self) -> asyncio.Lock:
        """One lock per event loop, as in :class:`~donkey_kit.core.auth.AnypointConnectedApp`."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _exchange(self, id_token: str) -> str:
        self.check()
        cfg = self._cfg
        path = cfg.token_exchange_path or _verify.TOKEN_EXCHANGE_PATH.get()
        url = f"{cfg.control_plane_url.rstrip('/')}{path}"
        require_secure_url(url, name="token exchange endpoint")
        cfg.check_endpoints(need="control_plane")
        # The URL only: the form body carries the id_token and the client secret.
        _log.debug("exchanging a user id_token at %s", url)
        resp = await self._http.post(
            url,
            data={
                "grant_type": _GRANT_TYPE,
                "subject_token": id_token,
                "subject_token_type": _ID_TOKEN_TYPE,
                "client_id": cfg.client_id or "",
                "client_secret": cfg.client_secret or "",
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code >= 400:
            raise AuthError(
                f"The token exchange for the user's id_token returned {resp.status_code} "
                f"at {path!r}; no request was sent as the service identity instead.",
                remediation=_REMEDIATION,
                response=resp,
            )
        try:
            body = resp.json()
            token = body.get("access_token")
            expires_in = body.get("expires_in")
            if not isinstance(token, str) or not token:
                raise TypeError("access_token is missing or not a string")
            ttl = None if expires_in is None else float(expires_in)
            if ttl is not None and not math.isfinite(ttl):
                raise ValueError("expires_in is not finite")
        except (ValueError, TypeError, AttributeError) as exc:
            # Not chained: the parse error's message can repeat the body.
            raise AuthError(
                f"The token exchange returned a malformed response ({type(exc).__name__}). "
                "RFC 8693 expects a JSON object with a string access_token and a "
                "numeric expires_in; capture the response as a fixture (BG §1.5).",
                remediation=_REMEDIATION,
                response=resp,
            ) from None
        if ttl is not None:
            self._store(_cache_key(id_token), token, ttl)
        return token

    def _store(self, key: str, token: str, ttl: float) -> None:
        self._cache[key] = (token, self._clock() + max(0.0, ttl - _EXPIRY_SAFETY_MARGIN_S))
        self._cache.move_to_end(key)
        while len(self._cache) > _MAX_CACHED_USERS:
            self._cache.popitem(last=False)


class _UserBinding:
    """The user a block runs as: the exchange and the id_token it trades."""

    __slots__ = ("_exchange", "_id_token", "_unexpiring")

    def __init__(self, exchange: TokenExchange, id_token: str, unexpiring: str | None) -> None:
        self._exchange = exchange
        self._id_token = id_token
        # A token the exchange gave no expiry for, so it was not cached; the
        # blocking client stamps it as is.
        self._unexpiring = unexpiring

    async def token(self) -> str:
        """The current exchanged token, exchanging again once it has expired."""
        return await self._exchange.token(self._id_token)

    def cached_token(self) -> str:
        """The current exchanged token, for a send that can't await an exchange.

        Raises:
            AuthError: The token from the scope's entry has expired.
        """
        token = self._exchange.cached(self._id_token) or self._unexpiring
        if token is not None:
            return token
        raise AuthError(
            "The user's exchanged token expired, and a blocking client can't run a "
            "new exchange; no request was sent as the service identity instead.",
            remediation=(
                "Re-enter donkey.as_user(...), or make the call on the async "
                "client, which exchanges again on expiry."
            ),
        )


_current_user: ContextVar[_UserBinding | None] = ContextVar("donkey_current_user", default=None)


def current_user() -> _UserBinding | None:
    """The user bound by the enclosing ``donkey.as_user(...)`` block, or ``None``."""
    return _current_user.get()


class UserScope:
    """The async context manager ``donkey.as_user(...)`` returns (BG §2.4).

    Entry exchanges the id_token, so a refused or malformed exchange raises
    :class:`~donkey_kit.core.errors.AuthError` before the block runs, and then
    binds the user. Exit restores whatever was bound before, so nested scopes
    shadow and restore like ``donkey.run(...)``.
    """

    __slots__ = ("_exchange", "_id_token", "_reset")

    def __init__(self, exchange: TokenExchange, id_token: str) -> None:
        if not isinstance(id_token, str) or not id_token.strip():
            raise ConfigError("donkey.as_user() needs a non-empty id_token string.")
        exchange.check()
        self._exchange = exchange
        self._id_token = id_token
        self._reset: Token[_UserBinding | None] | None = None

    async def __aenter__(self) -> None:
        token = await self._exchange.token(self._id_token)
        unexpiring = None if self._exchange.cached(self._id_token) else token
        self._reset = _current_user.set(_UserBinding(self._exchange, self._id_token, unexpiring))

    async def __aexit__(self, *exc: object) -> None:
        if self._reset is not None:
            _current_user.reset(self._reset)
            self._reset = None
