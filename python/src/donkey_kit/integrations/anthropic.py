"""Anthropic SDK adapter (``donkey.anthropic``) (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Returns a native ``anthropic.AsyncAnthropic`` client bound to the proxy. Because
we construct the client ourselves and hand it our shared http client, header AND
transport injection are both available (full injection).

Divergence, by design (BG §1.8 — the framework wins): Anthropic's native surface
is a *client*, and the model id is a per-call argument, not a constructor one.
So this adapter exposes ``client()`` rather than the ``model(...)`` factory the
OpenAI-compatible adapters use.

UNVERIFIED DEPENDENCY (docs/verified-apis.md §8): the Omni Gateway LLM proxy is
OpenAI-compatible; whether it also exposes an **Anthropic-native Messages API
route** is an open M0 verification item (verification discipline). If it does not, this adapter's
requests will not reach a working upstream — override ``base_url`` via ``**kw``
to point at a real Anthropic-native route once confirmed. The first ``client()``
call emits a one-time :class:`~donkey_kit.core._verify.UnverifiedValueWarning`.

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
                "The Omni Gateway LLM proxy is verified OpenAI-compatible; its "
                "Anthropic-native Messages API route is UNVERIFIED (docs/"
                "verified-apis.md §8, an open M0 item). If the proxy does not "
                "serve Anthropic's API, override base_url via **kw once a real "
                "route is confirmed.",
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
