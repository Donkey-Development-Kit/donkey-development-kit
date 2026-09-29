"""Google ADK adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Two factories, one per proxy ingress Format (docs/verified-apis.md §2):

* ``model()`` — ADK's ``LiteLlm`` wrapper for a ``Format=OpenAI`` proxy (the
  SDK's default DDK proxies). LiteLLM takes ``openai/<id>`` model strings and
  speaks ``/chat/completions``. Header injection is via LiteLLM's
  ``extra_headers``; we CANNOT inject our httpx client — LiteLLM owns the
  transport. Consequence: transport retries and correlation-ID-per-run degrade
  to per-client, and ``donkey.last_call`` is not populated. These are
  documented, asserted conformance exemptions (the conformance kit's
  ``correlation_id_propagated`` / ``gateway_identity_observed``). ADK requires
  ``litellm>=1.84`` (floor, not ceiling).
* ``gemini()`` — ADK's native ``google.adk.models.Gemini`` for a
  ``Format=Gemini`` proxy (#691). The LIVE-verified native route is
  ``POST <proxy>/models/<model>:generateContent`` (#540); the model travels in
  the URL only, and the ingress ignores a body ``model`` (probed 2026-09-29).
  ``google-genai`` accepts ``HttpOptions.httpx_async_client``, so we hand it the
  shared :class:`~donkey_kit.core.transport.DonkeyAsyncClient`: full injection —
  per-run correlation, SDK retries, rotating JWTs and ``donkey.last_call`` all
  work, and none of ``model()``'s exemptions apply. The default DDK proxies are
  ``Format=OpenAI``, so point ``base_url`` at a ``Format=Gemini`` proxy.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from google.adk.models import Gemini
    from google.adk.models.lite_llm import LiteLlm


class ADKAdapter(Adapter):
    extra = "adk"
    # LiteLLM owns the transport, so no response from ``model()`` reaches
    # donkey.last_call (#362, the same reason as the conformance kit's
    # correlation_id_propagated exemption). ``gemini()`` routes through our
    # transport and flips this on the instance that built it (#691).
    observes_last_call = False

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for a ``LiteLlm(model="openai/<id>", **kwargs)`` you
        build yourself. LiteLLM uses ``api_base``/``extra_headers`` (not
        ``base_url``/``default_headers``) and owns its own transport, so the
        shared http client is not injected here (BG §1.8 exemption; the conformance kit)."""
        conn = self._openai_connection()
        return {
            "api_base": conn["base_url"],
            "api_key": conn["api_key"],
            "extra_headers": conn["default_headers"],
        }

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
        ``/v1beta`` segment."""
        conn = self._openai_connection()
        url = base_url or conn["base_url"]
        return {
            "base_url": url,
            "client_kwargs": {
                "api_key": conn["api_key"],
                "http_options": {
                    "base_url": url,
                    "api_version": "",
                    "headers": conn["default_headers"],
                    "timeout": int(self._cfg.timeout_s * 1000),
                    "httpx_async_client": self._http_client(),
                },
            },
        }

    def model(self, model: str, **kw: Any) -> LiteLlm:
        from google.adk.models.lite_llm import LiteLlm  # VERIFY name/path: docs/verified-apis.md §8

        # LiteLLM's OpenAI-compatible route needs the ``openai/`` prefix.
        return LiteLlm(model=f"openai/{model}", **{**self.connection_kwargs(), **kw})

    def gemini(self, model: str, *, base_url: str | None = None, **kw: Any) -> Gemini:
        """Return ADK's native ``google.adk.models.Gemini`` bound to a
        ``Format=Gemini`` proxy, with the shared http client injected (#691).
        Pass the bare model id (``"gemini-2.5-flash"``) — no provider prefix."""
        from google.adk.models import Gemini  # VERIFY name/path: docs/verified-apis.md §8

        native = Gemini(model=model, **{**self.gemini_connection_kwargs(base_url=base_url), **kw})
        self.observes_last_call = True
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
