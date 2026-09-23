"""Anthropic SDK adapter (``donkey.anthropic``) (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Returns a native ``anthropic.AsyncAnthropic`` client bound to the proxy. Because
we construct the client ourselves and hand it our shared http client, header AND
transport injection are both available (full injection).

Divergence, by design (BG §1.8 — the framework wins): Anthropic's native surface
is a *client*, and the model id is a per-call argument, not a constructor one.
So this adapter exposes ``client()`` rather than the ``model(...)`` factory the
OpenAI-compatible adapters use.

UNVERIFIED DEPENDENCY (docs/verified-apis.md §2, #304): MuleSoft Model Proxy
*does* offer a native **Anthropic** ingress Format — it is one of three
selectable ingress Formats (OpenAI / Gemini / Anthropic), fixed at proxy
creation (docs.mulesoft.com/general/model-proxy). So the Anthropic-native route
is a real product capability, **but** two things keep it UNVERIFIED for this
adapter: (1) it must be *provisioned* that way — every DDK proxy is
``Format=OpenAI``, so pointing this client at them reaches Claude only as an
*upstream provider*, not via Anthropic's native surface; (2) the exact ingress
path (likely ``/v1/messages``) and its live behavior have not been captured
against a real ``Format=Anthropic`` proxy. Until that capture lands, point
``base_url`` (via ``**kw``) at a proxy provisioned with ``Format=Anthropic``.
The first ``client()`` call emits a one-time
:class:`~donkey_kit.core._verify.UnverifiedValueWarning`.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

from ..core import _verify
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

_ROUTE_KEY = "anthropic.proxy_messages_route"


class AnthropicAdapter(Adapter):
    extra = "anthropic"

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``AsyncAnthropic(**kwargs)`` you build yourself:
        proxy ``base_url``, the verified consumer-auth ``default_headers``, the
        shared http client, and the ``api_key`` slot. Warns once that the proxy's
        Anthropic-native route is unverified (see the module docstring)."""
        if _ROUTE_KEY not in _verify._warned:
            _verify._warned.add(_ROUTE_KEY)
            warnings.warn(
                "MuleSoft Model Proxy offers a native Anthropic ingress Format, but every "
                "DDK proxy is provisioned Format=OpenAI, and the Anthropic ingress path + "
                "live behavior are UNVERIFIED (docs/verified-apis.md §2, #304). Pointed at a "
                "Format=OpenAI proxy this reaches Claude only as an upstream provider, not "
                "natively; override base_url via **kw to target a Format=Anthropic proxy.",
                _verify.UnverifiedValueWarning,
                stacklevel=3,
            )
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

        return AsyncAnthropic(**self.connection_kwargs(), **kw)


def client(**kw: Any) -> AsyncAnthropic:
    """Module-level convenience: a native ``AsyncAnthropic`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().anthropic.client(**kw)``."""
    return default_adapter(AnthropicAdapter).client(**kw)
