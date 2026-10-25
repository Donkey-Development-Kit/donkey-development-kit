"""Google ADK adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Two factories, one per proxy ingress Format (docs/verified-apis.md §2):

* ``model()`` — ADK's ``LiteLlm`` wrapper for a ``Format=OpenAI`` proxy (the
  SDK's default DDK proxies). LiteLLM takes ``openai/<id>`` model strings and
  speaks ``/chat/completions``. Header injection is via LiteLLM's
  ``extra_headers``. LiteLLM takes a pre-built OpenAI client (``client``), so
  we pass an ``AsyncOpenAI`` that sends through the shared client: redirects
  are not followed, credentials go only to checked endpoints, and each call
  carries the run's correlation id and populates ``donkey.last_call`` (#740).
  ADK requires ``litellm>=1.84`` (floor, not ceiling). LiteLLM sets the
  client's ``max_retries`` on every call (default 2), so the kwarg goes to
  LiteLLM itself (#734). LiteLLM re-raises a gateway refusal around a response
  it rebuilt, so ``model()`` also passes ADK's ``llm_client`` hook: a
  ``LiteLLMClient`` subclass that tags the error with the typed refusal the
  transport recorded for that call, which the typed-refusal bridge then raises
  (#969). A ``LiteLlm`` built from ``connection_kwargs()`` does not get it.
* ``gemini()`` — ADK's native ``google.adk.models.Gemini`` for a
  ``Format=Gemini`` proxy (#691). The native route is
  ``POST <proxy>/models/<model>:generateContent`` (#540); the model travels in
  the URL only, and the ingress ignores a body ``model`` (docs/verified-apis.md §2).
  ``google-genai`` accepts ``HttpOptions.httpx_async_client``, so we hand it the
  shared :class:`~donkey_kit.core.transport.DonkeyAsyncClient`: full injection —
  per-run correlation, SDK retries, rotating JWTs and ``donkey.last_call`` all
  work. The default DDK proxies are
  ``Format=OpenAI``, so point ``base_url`` at a ``Format=Gemini`` proxy.

Both factories return a subclass of the ADK class that carries
``donkey.last_call`` back to the context that started the model call (#1207).
google-adk 2.13.0 runs each step of the model's response generator in a copy of
the flow's context, so without it the record stays in that copy and an
``after_model_callback`` reads ``UNOBSERVED``.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

import functools
from contextlib import aclosing
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..core import _verify
from ..core.lastcall import LastCallBridge, open_last_call_bridge
from ..core.masking import masked
from ..core.refusals import capture_refusals
from . import AdapterCapabilities
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from google.adk.models import Gemini
    from google.adk.models.lite_llm import LiteLlm, LiteLLMClient

__all__ = ["ADKAdapter", "gemini", "model"]


class ADKAdapter(Adapter):
    """Governed Google ADK objects, reached as ``donkey.adk``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``model(model)`` builds a
    ``LiteLlm`` and ``gemini(model)`` a ``Gemini``. ``connection_kwargs()`` returns
    the same settings for building it yourself.

    Supported at ``connection_kwargs()`` only (`BG §1.8`): that accessor is the
    supported surface, and the factories are conveniences over it.

    Raises:
        ImportError: ``donkey.adk`` was read without the ``adk`` extra installed;
            the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/adk
    """

    # Both send through the shared client and observe donkey.last_call (#691,
    # #946). model()'s typed refusals come from its llm_client, which tags
    # LiteLLM's error with the refusal the transport recorded for the call
    # (#969). Each factory has its own capabilities (#741, #726).
    factories = MappingProxyType(
        {
            "model": AdapterCapabilities(
                transport="shared",
                sync=False,
                streaming=True,
                typed_refusals=True,
                observes_last_call=True,
            ),
            "gemini": AdapterCapabilities(
                transport="shared",
                sync=False,
                streaming=True,
                typed_refusals=True,
                observes_last_call=True,
            ),
        }
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for a ``LiteLlm(model="openai/<id>", **kwargs)`` you
        build yourself. LiteLLM uses ``api_base``/``extra_headers`` (not
        ``base_url``/``default_headers``). ``client`` is an ``AsyncOpenAI`` that
        sends through the SDK's shared client, which does not follow redirects
        and sends credentials only to checked endpoints; LiteLLM's OpenAI route
        uses it in place of the client it would build."""
        conn = self._connection()
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

        ADK builds its client from ``client_kwargs`` (from google-adk 2.13.0 it
        adds its tracking headers and fills only the fields left unset), so every
        governed value rides there: the shared http client (header injection,
        retries, last_call), the consumer-auth headers, the ``api_key`` slot
        google-genai requires for the Gemini API backend, and the SDK timeout —
        google-genai otherwise sends ``timeout=None``, which disables the
        client's. ``api_version=""`` because the proxy route has no
        ``/v1beta`` segment. A ``base_url`` passed here must pass the https check."""
        self._allow_endpoints({"base_url": base_url}, "base_url")
        conn = self._connection("gemini")
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
        override must pass the https check.

        Its ``llm_client`` is a ``LiteLLMClient`` subclass that lets a gateway
        refusal reach ``donkey.run()`` as its typed class (#969, see the module
        docstring). An ``llm_client`` you pass replaces it, and the refusal
        then stays LiteLLM's error.

        The object is a ``LiteLlm`` subclass that carries ``donkey.last_call``
        back to the context that started each model call, so an
        ``after_model_callback`` reads it (#1207)."""
        self._allow_endpoints(kw, "api_base", "base_url")
        with self._native_import():
            from google.adk.models.lite_llm import (  # VERIFY name/path: docs/verified-apis.md §8
                LiteLlm,
                LiteLLMClient,
            )

        conn = self.connection_kwargs()
        override = kw.get("api_base") or kw.get("base_url")
        if override is not None and "client" in conn:
            # LiteLLM sends to the client's own base URL, so rebuild it there.
            conn["client"] = self._proxy_openai_client(str(override))
        conn["llm_client"] = _governed_llm_client_class(LiteLLMClient)()
        governed: type[LiteLlm] = _last_call_bridged_class(LiteLlm)
        # LiteLLM's OpenAI-compatible route needs the ``openai/`` prefix.
        return governed(model=f"openai/{model}", **{**conn, **kw})

    def gemini(self, model: str, *, base_url: str | None = None, **kw: Any) -> Gemini:
        """Return ADK's native ``google.adk.models.Gemini`` bound to a
        ``Format=Gemini`` proxy, with the shared http client injected (#691).
        Pass the bare model id (``"gemini-2.5-flash"``) — no provider prefix.

        The object is a ``Gemini`` subclass that carries ``donkey.last_call``
        back to the context that started each model call, so an
        ``after_model_callback`` reads it (#1207, see the module docstring)."""
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
        governed: type[Gemini] = _last_call_bridged_class(Gemini)
        return governed(model=model, **{**conn, **kw})


@functools.cache
def _last_call_bridged_class(base: Any) -> Any:
    """The subclass of an ADK model class (``base``: ``Gemini`` or ``LiteLlm``)
    that :meth:`ADKAdapter.gemini` and :meth:`ADKAdapter.model` build, defined
    on first use so ``google.adk`` is imported lazily (§1.1).

    Only the public ``BaseLlm.generate_content_async`` is overridden. ADK's flow
    calls it in its own context and then iterates the generator it returns.
    From google-adk 2.13.0 it iterates that generator in a copy of the context
    (``_stream_in_caller_scope``), so the record the transport sets stays in the
    copy, and the ``after_model_callback`` that ADK runs in the flow's context
    reads ``UNOBSERVED`` (docs/verified-apis.md §8). The override is a plain
    ``def``, so it opens a :class:`~donkey_kit.core.lastcall.LastCallBridge`
    in the flow's context before ADK takes the copy. The copy inherits the
    bridge, and the transport records through it.

    The bridge serves one model call in one task, so hazard #2 holds: a
    ``ParallelAgent`` branch runs in a task of its own and opens its own bridge.
    It closes before the generator yields a complete (non-partial) response,
    because ADK runs that response's function calls before it resumes the
    generator, and a tool that ADK runs in a task of its own must not write
    through the bridge. A partial response keeps it open, since ADK runs no
    function calls for one."""

    class LastCallBridged(base):
        """An ADK model whose ``donkey.last_call`` reaches the context that
        started the call (#1207)."""

        def generate_content_async(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
            bridge = open_last_call_bridge()
            return _bridged_responses(super().generate_content_async(*args, **kwargs), bridge)

    LastCallBridged.__name__ = LastCallBridged.__qualname__ = f"Governed{base.__name__}"
    return LastCallBridged


async def _bridged_responses(
    responses: AsyncGenerator[Any, None], bridge: LastCallBridge
) -> AsyncGenerator[Any, None]:
    """Yield ``responses`` and close ``bridge`` before the first complete
    response, or when the call ends early (see :func:`_last_call_bridged_class`)."""
    try:
        async with aclosing(responses):
            async for response in responses:
                if not getattr(response, "partial", False):
                    bridge.close()
                yield response
    finally:
        bridge.close()


@functools.cache
def _governed_llm_client_class(base: Any) -> type[LiteLLMClient]:
    """The subclass of ADK's ``LiteLLMClient`` (``base``) that
    :meth:`ADKAdapter.model` passes as ``llm_client``, defined on first use so
    ``google.adk`` is imported lazily (§1.1).

    ``LiteLlm`` awaits ``llm_client.acompletion`` for every model request,
    streamed or not (docs/verified-apis.md §8). LiteLLM raises a gateway refusal
    as its own error around a response it rebuilt (no gateway headers, no
    cause), so the override opens a refusal capture around the call and tags
    the error it raised with the typed error the transport recorded. The error
    itself is re-raised unchanged; ``donkey.run()`` and ``typed_refusals()``
    raise the tagged refusal in its place."""

    class GovernedLiteLLMClient(base):
        """ADK's ``LiteLLMClient``, with LiteLLM's errors tagged with the typed
        refusal behind them (#969)."""

        async def acompletion(self, *args: Any, **kwargs: Any) -> Any:
            with capture_refusals() as capture:
                try:
                    return await super().acompletion(*args, **kwargs)
                except Exception as exc:
                    capture.tag(exc)
                    raise

    return GovernedLiteLLMClient


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
