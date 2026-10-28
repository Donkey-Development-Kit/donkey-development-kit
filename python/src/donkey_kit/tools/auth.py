"""The consumer credential a ``ToolSet`` binding sends to a governed MCP server (#221).

A governed MCP server is a gateway endpoint, so it takes the same consumer
credential as the LLM proxy: the ``client_id``/``client_secret`` pair Client ID
Enforcement reads, or a bearer token a JWT Validation policy reads. Which one an
instance wants is a gateway policy choice, so the source is pluggable: any object
with :meth:`McpAuth.headers` and :meth:`McpAuth.invalidate` is one, and
``donkey.tools.discover(auth=...)`` takes it. The default, :func:`gateway_auth`,
follows ``llm_proxy_auth``.

The headers are read when a binding is built and handed to the framework's own
MCP client as static headers, which is the only header argument every framework
has (docs/verified-apis.md §9). A bearer token is therefore the one current at
bind time: bind again after it expires.
"""

from __future__ import annotations

from typing import Protocol

from ..core import _verify
from ..core.auth import AuthProvider
from ..core.config import TOKEN_AUTH_MODES, DonkeyConfig

__all__ = ["BearerAuth", "ClientIdAuth", "McpAuth", "gateway_auth"]


class McpAuth(Protocol):
    """Where a binding's gateway credential headers come from (#221).

    Docs: https://docs.donkey-kit.dev/tool-access/binding#auth
    """

    async def headers(self) -> dict[str, str]:
        """Return the credential headers for one bind.

        Raises:
            AuthError: No credential could be obtained.
        """
        ...

    async def invalidate(self) -> None:
        """Drop any cached credential, so the next :meth:`headers` fetches a fresh one."""
        ...


class ClientIdAuth:
    """The ``client_id``/``client_secret`` pair Client ID Enforcement reads.

    Args:
        client_id: The consumer application's client ID.
        client_secret: Its client secret.
    """

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._id = client_id
        self._secret = client_secret

    async def headers(self) -> dict[str, str]:
        """The ``client_id`` and ``client_secret`` request headers."""
        return {
            _verify.LLM_PROXY_CLIENT_ID_HEADER: self._id,
            _verify.LLM_PROXY_CLIENT_SECRET_HEADER: self._secret,
        }

    async def invalidate(self) -> None:
        """Nothing to drop: the pair never rotates."""
        return None


class BearerAuth:
    """``Authorization: Bearer <token>`` from an :class:`~donkey_kit.core.auth.AuthProvider`,
    for an instance behind a JWT Validation policy.

    Args:
        provider: Where the token comes from.
    """

    def __init__(self, provider: AuthProvider) -> None:
        self._provider = provider

    async def headers(self) -> dict[str, str]:
        """``Authorization`` with the provider's current token."""
        return {"Authorization": f"Bearer {await self._provider.token()}"}

    async def invalidate(self) -> None:
        """Drop the provider's cached token."""
        await self._provider.invalidate()


def gateway_auth(cfg: DonkeyConfig, llm_auth: AuthProvider | None) -> McpAuth | None:
    """The data-plane consumer credential as an :class:`McpAuth`, or ``None`` when
    none is configured.

    ``client-id`` mode sends the ``llm_proxy_client_id``/``llm_proxy_client_secret``
    pair; ``jwt`` and ``bearer`` mode send ``llm_auth``'s token as a bearer token.
    Never the provider key, the wallet selector or the attribution headers: those
    belong to the LLM proxy.
    """
    if cfg.llm_proxy_auth in TOKEN_AUTH_MODES:
        return BearerAuth(llm_auth) if llm_auth is not None else None
    if cfg.llm_proxy_client_id and cfg.llm_proxy_client_secret:
        return ClientIdAuth(cfg.llm_proxy_client_id, cfg.llm_proxy_client_secret)
    return None
