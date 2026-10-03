"""Non-owning views of the shared clients (#733).

Every adapter and ``donkey.llm`` share one client per plane, but several
frameworks own the lifecycle of the client they are given: Strands runs
``async with AsyncOpenAI(**client_args)`` per request, and ``async with
donkey.openai()`` closes its ``http_client`` on exit. Handing them the shared
client itself lets one framework close it for the whole ``Donkey``. A view sends
through the shared client, so every hook, retry, span, ``simulate()`` swap and
``donkey.last_call`` still applies, but closing it never closes the pool; only
``Donkey.aclose()``/``close()`` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from ..config import TOKEN_AUTH_MODES
from .failures import sync_token_auth_error

if TYPE_CHECKING:
    from httpx._client import UseClientDefault
    from httpx._types import AuthTypes

    from .async_client import DonkeyAsyncClient
    from .sync_client import DonkeyClient

__all__ = ["DonkeyAsyncClientView", "DonkeyClientView", "built_on_httpx2"]


def built_on_httpx2(default_client: object) -> bool:
    """Whether a framework's default async client class is an ``httpx2`` one.

    ``anthropic>=1.0`` and ``openai>=3`` export ``DefaultAsyncHttpxClient``: an
    ``httpx.AsyncClient`` subclass on the older majors, an ``httpx2.AsyncClient``
    on the newer. The caller passes that attribute (or ``None`` when the framework
    lacks it), so core stays framework-free; a framework built on ``httpx2`` is
    handed the core ``httpx2`` bridge rather than a view (#701, #728).
    """
    return isinstance(default_client, type) and not issubclass(default_client, httpx.AsyncClient)


class _NoTransport(httpx.AsyncBaseTransport, httpx.BaseTransport):
    """A view's own transport. Never used, because a view's ``send()`` delegates;
    passing it keeps httpx from building a connection pool for the view."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:  # noqa: ARG002
        raise RuntimeError("a DonkeyClientView sends through its shared client")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # noqa: ARG002
        raise RuntimeError("a DonkeyAsyncClientView sends through its shared client")


class DonkeyAsyncClientView(httpx.AsyncClient):
    """A non-owning ``httpx.AsyncClient`` over a shared :class:`DonkeyAsyncClient`.

    ``send()`` hands each request to the shared client. ``aclose()`` and leaving
    ``async with`` do nothing, and :attr:`is_closed` reports the shared client's
    state. Event hooks added to the view run for requests sent through it, around
    the shared client's own. Get one from :meth:`DonkeyAsyncClient.view`."""

    def __init__(self, shared: DonkeyAsyncClient) -> None:
        self._shared = shared
        super().__init__(
            timeout=shared.timeout,
            follow_redirects=shared.follow_redirects,
            trust_env=False,
            transport=_NoTransport(),
        )

    @property
    def is_closed(self) -> bool:
        """Whether the shared client is closed (closing this view does not close it)."""
        return self._shared.is_closed

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: AuthTypes | UseClientDefault | None = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: bool | UseClientDefault = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        """Run this view's own event hooks around a send through the shared client."""
        for hook in self.event_hooks["request"]:
            await hook(request)
        response = await self._shared.send(
            request, stream=stream, auth=auth, follow_redirects=follow_redirects
        )
        for hook in self.event_hooks["response"]:
            await hook(response)
        return response

    async def aclose(self) -> None:
        """Leave the shared client open; ``Donkey.aclose()`` owns it."""

    async def __aenter__(self) -> DonkeyAsyncClientView:
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Leave the shared client open (see :meth:`aclose`)."""


class DonkeyClientView(httpx.Client):
    """The blocking twin of :class:`DonkeyAsyncClientView`, over a shared
    :class:`DonkeyClient`. Get one from :meth:`DonkeyClient.view`."""

    def __init__(self, shared: DonkeyClient) -> None:
        self._shared = shared
        super().__init__(
            timeout=shared.timeout,
            follow_redirects=shared.follow_redirects,
            trust_env=False,
            transport=_NoTransport(),
        )

    @property
    def is_closed(self) -> bool:
        """Whether the shared client is closed (closing this view does not close it)."""
        return self._shared.is_closed

    def build_request(self, *args: Any, **kwargs: Any) -> httpx.Request:
        """Build a request, refusing in a token auth mode as the shared client does.

        Raises:
            ConfigError: ``llm_proxy_auth`` is ``jwt`` or ``bearer``.
        """
        # The shared client refuses here in a token mode; a view must refuse too.
        # Same-package collaborator: a view reads the config of the client it wraps.
        mode = self._shared._cfg.llm_proxy_auth  # noqa: SLF001
        if mode in TOKEN_AUTH_MODES:
            raise sync_token_auth_error(mode)
        return super().build_request(*args, **kwargs)

    def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: AuthTypes | UseClientDefault | None = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: bool | UseClientDefault = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        """Run this view's own event hooks around a send through the shared client."""
        for hook in self.event_hooks["request"]:
            hook(request)
        response = self._shared.send(
            request, stream=stream, auth=auth, follow_redirects=follow_redirects
        )
        for hook in self.event_hooks["response"]:
            hook(response)
        return response

    def close(self) -> None:
        """Leave the shared client open; ``Donkey.close()`` owns it."""

    def __enter__(self) -> DonkeyClientView:
        return self

    def __exit__(self, *exc: object) -> None:
        """Leave the shared client open (see :meth:`close`)."""
