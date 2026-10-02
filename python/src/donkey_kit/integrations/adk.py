"""Google ADK adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Two factories, one per proxy ingress Format (docs/verified-apis.md §2):

* ``model()`` — ADK's ``LiteLlm`` wrapper for a ``Format=OpenAI`` proxy (the
  SDK's default DDK proxies). LiteLLM takes ``openai/<id>`` model strings and
  speaks ``/chat/completions``. Header injection is via LiteLLM's
  ``extra_headers``. LiteLLM takes a pre-built OpenAI client (``client``), so
  we pass an ``AsyncOpenAI`` that sends through the shared client: redirects
  are not followed and credentials go only to checked endpoints. The
  conformance kit still lists ``model()`` under ``correlation_id_propagated`` /
  ``gateway_identity_observed`` and ``donkey.last_call`` stays unpopulated.
  ADK requires ``litellm>=1.84`` (floor, not ceiling). LiteLLM sets the
  client's ``max_retries`` on every call (default 2), so the kwarg goes to
  LiteLLM itself (#734).
* ``gemini()`` — ADK's native ``google.adk.models.Gemini`` for a
  ``Format=Gemini`` proxy (#691). The native route is
  ``POST <proxy>/models/<model>:generateContent`` (#540); the model travels in
  the URL only, and the ingress ignores a body ``model`` (docs/verified-apis.md §2).
  ``google-genai`` accepts ``HttpOptions.httpx_async_client``, so we hand it the
  shared :class:`~donkey_kit.core.transport.DonkeyAsyncClient`: full injection —
  per-run correlation, SDK retries, rotating JWTs and ``donkey.last_call`` all
  work, and none of ``model()``'s exemptions apply. The default DDK proxies are
  ``Format=OpenAI``, so point ``base_url`` at a ``Format=Gemini`` proxy.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..core import _verify
from ..core.masking import masked
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from google.adk.models import Gemini
    from google.adk.models.lite_llm import LiteLlm


class ADKAdapter(Adapter):
    # Kept False for ``model()`` while the conformance kit lists its
    # correlation_id_propagated exemption (#362), although its calls now go
    # through the shared client. ``gemini()`` observes (#691), recorded per
    # factory rather than set on the instance (#741).
    observes_last_call = False
    factory_observes_last_call = MappingProxyType({"gemini": True})

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for a ``LiteLlm(model="openai/<id>", **kwargs)`` you
        build yourself. LiteLLM uses ``api_base``/``extra_headers`` (not
        ``base_url``/``default_headers``). ``client`` is an ``AsyncOpenAI`` that
        sends through the SDK's shared client, which does not follow redirects
        and sends credentials only to checked endpoints; LiteLLM's OpenAI route
        uses it in place of the client it would build."""
        conn = self._openai_connection()
        return masked(
            {
                "api_base": conn["base_url"],
                "api_key": conn["api_key"],
                "extra_headers": conn["default_headers"],
                # LiteLLM sets this on ``client`` per call, default 2.
                "max_retries": 0,  # we retry in transport (BG §1.1)
                **self._proxy_openai_client_kwarg("client"),
            }
        )

    def gemini_connection_kwargs(self, *, base_url: str | None = None) -> dict[str, Any]:
        """Governed kwargs for a ``Gemini(model=<id>, **kwargs)`` you build
        yourself, bound to a ``Format=Gemini`` proxy (``base_url``, default the
        configured proxy URL).

        ADK replaces its own ``http_options`` with ``client_kwargs``, so every
        governed value rides there: the shared http client (header injection,
        retries, last_call), the consumer-auth headers, the ``api_key`` slot
        google-genai requires for the Gemini API backend, and the SDK timeout —
        google-genai otherwise sends ``timeout=None``, which disables the
        client's. ``api_version=""`` because the proxy route has no
        ``/v1beta`` segment. A ``base_url`` passed here must pass the https check."""
        self._allow_endpoints({"base_url": base_url}, "base_url")
        conn = self._openai_connection()
        url = base_url or conn["base_url"]
        return masked(
            {
                "base_url": url,
                "client_kwargs": {
                    "api_key": conn["api_key"],
                    "http_options": {
                        "base_url": url,
                        "api_version": "",
                        "headers": conn["default_headers"],
                        "timeout": int(self._cfg.timeout_s * 1000),
                        "httpx_async_client": self.http_client(),
                    },
                },
            }
        )

    def model(self, model: str, **kw: Any) -> LiteLlm:
        """Return ADK's ``LiteLlm`` at the proxy. An ``api_base``/``base_url``
        override must pass the https check."""
        self._allow_endpoints(kw, "api_base", "base_url")
        with self._native_import():
            from google.adk.models.lite_llm import (
                LiteLlm,  # VERIFY name/path: docs/verified-apis.md §8
            )

        conn = self.connection_kwargs()
        override = kw.get("api_base") or kw.get("base_url")
        if override is not None and "client" in conn:
            # LiteLLM sends to the client's own base URL, so rebuild it there.
            conn["client"] = self._proxy_openai_client(str(override))
        # LiteLLM's OpenAI-compatible route needs the ``openai/`` prefix.
        return LiteLlm(model=f"openai/{model}", **{**conn, **kw})

    def gemini(self, model: str, *, base_url: str | None = None, **kw: Any) -> Gemini:
        """Return ADK's native ``google.adk.models.Gemini`` bound to a
        ``Format=Gemini`` proxy, with the shared http client injected (#691).
        Pass the bare model id (``"gemini-2.5-flash"``) — no provider prefix."""
        conn = self.gemini_connection_kwargs(base_url=base_url)
        with self._native_import():
            from google.adk.models import Gemini  # VERIFY name/path: docs/verified-apis.md §8

        # ADK's pydantic config ignores unknown fields, so on google-adk < 2.4 (no
        # ``client_kwargs``; no ``base_url`` before 2.0) both kwargs are dropped
        # silently and the model talks to Google directly, bypassing the gateway
        # (#735). Refuse rather than return an ungoverned model.
        fields = getattr(Gemini, "model_fields", {})
        missing = sorted({"base_url", "client_kwargs"} - set(fields))
        if missing:
            raise _verify.blocked(
                "google.adk.models.Gemini lacks "
                + ", ".join(missing)
                + " (docs/verified-apis.md §8). donkey.adk.gemini() needs "
                "google-adk>=2.4, the first release with Gemini.client_kwargs; on "
                "older versions ADK drops the governed client without an error. "
                "Upgrade with: pip install -U 'donkey-kit[adk]'"
            )
        native = Gemini(model=model, **{**conn, **kw})
        self._record_factory("gemini")
        return native


def model(model: str, **kw: Any) -> LiteLlm:
    """Module-level convenience: a native ``LiteLlm`` at the proxy using a cached
    default env-configured Donkey. Equivalent to
    ``Donkey.from_env().adk.model(model, **kw)``."""
    return default_adapter(ADKAdapter).model(model, **kw)


def gemini(model: str, *, base_url: str | None = None, **kw: Any) -> Gemini:
    """Module-level convenience: a native ``Gemini`` at a ``Format=Gemini`` proxy
    using a cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().adk.gemini(model, base_url=..., **kw)``."""
    return default_adapter(ADKAdapter).gemini(model, base_url=base_url, **kw)
