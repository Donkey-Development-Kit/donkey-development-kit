"""Anthropic SDK adapter (``donkey.anthropic``) (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Returns a native ``anthropic.AsyncAnthropic`` client bound to the proxy. Because
we construct the client ourselves and hand it our shared http client, header AND
transport injection are both available (full injection).

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

from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


class AnthropicAdapter(Adapter):
    extra = "anthropic"

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``AsyncAnthropic(**kwargs)`` you build yourself:
        proxy ``base_url``, the verified consumer-auth ``default_headers``, the
        shared http client, and the ``api_key`` slot. The proxy's Anthropic-native
        route is LIVE-verified but requires a ``Format=Anthropic`` proxy (see the
        module docstring)."""
        conn = self._openai_connection()  # base_url, api_key, default_headers
        return {
            "base_url": conn["base_url"],
            "api_key": conn["api_key"],
            "default_headers": conn["default_headers"],
            "http_client": self._http_client(),
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
