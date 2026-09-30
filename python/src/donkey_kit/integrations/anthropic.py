"""Anthropic SDK adapter (``donkey.anthropic``) (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Returns a native ``anthropic.AsyncAnthropic`` client bound to the proxy. Because
we construct the client ourselves and hand it an http client that sends through
our shared transport, header AND transport injection are both available (full
injection).

HTTP STACK (#701, docs/verified-apis.md §8.1): ``anthropic<1`` is built on
``httpx`` and is handed the shared ``DonkeyAsyncClient`` itself. ``anthropic>=1.0``
is built on ``httpx2`` and rejects any ``httpx`` client, so it is handed an
``httpx2.AsyncClient`` whose transport forwards every request through the same
shared client (``_httpx2_bridge``). Both stacks get the same governed headers,
retries, span, budget and ``donkey.last_call``; the installed release decides
which one ``connection_kwargs()`` returns.

Divergence, by design (BG §1.8 — the framework wins): Anthropic's native surface
is a *client*, and the model id is a per-call argument, not a constructor one.
So this adapter exposes ``client()`` rather than the ``model(...)`` factory the
OpenAI-compatible adapters use.

PROXY ROUTE (docs/verified-apis.md §2, #304): MuleSoft Model Proxy offers a
native **Anthropic** ingress Format — one of three selectable ingress Formats
(OpenAI / Gemini / Anthropic), fixed at proxy creation. That route is now
**LIVE-verified**: a ``Format=Anthropic`` proxy serves the Anthropic Messages API
natively at ``POST /<base-path>/v1/messages`` (200 with a native Anthropic body;
an OpenAI-shaped ``/chat/completions`` request 404s). Captured in
``python/tests/fixtures/anypoint/anthropic_inbound/``.

Usage caveat (not a verification gap): the route requires a proxy *provisioned*
``Format=Anthropic``. The SDK's own default DDK proxies are ``Format=OpenAI``, so
pointing this client at them reaches Claude only as an *upstream provider*, not
via Anthropic's native surface (``/v1/messages`` 404s there). Point ``base_url``
(via ``**kw``) at a ``Format=Anthropic`` proxy to use the native ingress.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8 (#34).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from ..core.config import DonkeyConfig
from ..core.transport import DonkeyAsyncClient
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    import httpx2
    from anthropic import AsyncAnthropic


def _anthropic_uses_httpx2() -> bool:
    """True when the installed ``anthropic`` is built on ``httpx2`` (1.0 and
    later). Its public ``DefaultAsyncHttpxClient`` is the client class it builds
    when given none: an ``httpx.AsyncClient`` subclass before 1.0, an
    ``httpx2.AsyncClient`` from 1.0. Without ``anthropic`` (or without that name)
    this is False, so ``connection_kwargs()`` keeps working framework-free."""
    try:
        import anthropic
    except ImportError:
        return False
    default = getattr(anthropic, "DefaultAsyncHttpxClient", None)
    return isinstance(default, type) and not issubclass(default, httpx.AsyncClient)


class AnthropicAdapter(Adapter):
    extra = "anthropic"

    def __init__(self, cfg: DonkeyConfig, http_client: DonkeyAsyncClient) -> None:
        super().__init__(cfg, http_client)
        self._bridged: httpx2.AsyncClient | None = None

    def _anthropic_http_client(self) -> DonkeyAsyncClient | httpx2.AsyncClient:
        """The ``http_client`` for the installed ``anthropic``: the shared client
        on ``anthropic<1``, one bridged ``httpx2`` client per adapter on 1.0 and
        later. The bridged client is rebuilt once closed, because
        ``async with AsyncAnthropic(...)`` closes its ``http_client`` on exit."""
        if not _anthropic_uses_httpx2():
            return self._http_client()
        if self._bridged is None or self._bridged.is_closed:
            from ._httpx2_bridge import bridged_client

            self._bridged = bridged_client(self._http_client())
        return self._bridged

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``AsyncAnthropic(**kwargs)`` you build yourself:
        proxy ``base_url``, the verified consumer-auth ``default_headers``, an
        ``http_client`` that sends through the shared transport, and the
        ``api_key`` slot. ``http_client`` is the shared ``httpx`` client on
        ``anthropic<1`` and a bridged ``httpx2`` client on ``anthropic>=1.0`` (see
        the module docstring). The proxy's Anthropic-native route is
        LIVE-verified but requires a ``Format=Anthropic`` proxy."""
        conn = self._openai_connection()  # base_url, api_key, default_headers
        return {
            "base_url": conn["base_url"],
            "api_key": conn["api_key"],
            "default_headers": conn["default_headers"],
            "http_client": self._anthropic_http_client(),
            "max_retries": 0,  # we retry in transport (BG §1.1)
        }

    def client(self, **kw: Any) -> AsyncAnthropic:
        """Return a native ``anthropic.AsyncAnthropic`` pointed at the proxy. Pass
        the model id per call (``messages.create(model=..., ...)``), per the
        Anthropic SDK's own surface (BG §1.8)."""
        from anthropic import AsyncAnthropic  # VERIFY name/path: docs/verified-apis.md §8

        return AsyncAnthropic(**{**self.connection_kwargs(), **kw})


def client(**kw: Any) -> AsyncAnthropic:
    """Module-level convenience: a native ``AsyncAnthropic`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().anthropic.client(**kw)``."""
    return default_adapter(AnthropicAdapter).client(**kw)
