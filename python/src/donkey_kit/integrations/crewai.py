"""CrewAI adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

CrewAI reaches models through its own ``crewai.LLM`` factory, which routes to a
native provider SDK or falls back to LiteLLM depending on the model prefix. As
with ADK, an OpenAI-compatible proxy is addressed with the ``openai/`` model
prefix plus ``base_url``.

``crewai.LLM(...)`` is a ``__new__``-based factory, not a plain constructor: for
an ``openai/``-prefixed model with an explicit ``base_url`` it deliberately
returns a ``crewai.llms.providers.openai.completion.OpenAICompletion`` instance
— CrewAI's native OpenAI provider, a sibling ``crewai.BaseLLM`` subclass, not a
``crewai.LLM`` instance (``isinstance(obj, crewai.LLM)`` is false;
``isinstance(obj, crewai.BaseLLM)`` is true). That provider strips the
``openai/`` prefix and does not go through LiteLLM. This is confirmed offline
against `crewai==1.15.22` (#640, docs/verified-apis.md §8) as crewai's own
documented routing behavior, not an incompatibility — so :meth:`llm` is typed
against ``crewai.BaseLLM``, the actual common return type, rather than the
``crewai.LLM`` factory's own name.

Header injection: via ``extra_headers``, which ``OpenAICompletion`` has no named
field for — CrewAI collects it into ``additional_params`` and merges that into
its request parameters (docs/verified-apis.md §8). Our httpx client is not
injected: the provider builds its own OpenAI client. Consequence: transport retries and
correlation-ID-per-run degrade to per-client, a documented, asserted conformance
exemption (the conformance kit's ``correlation_id_propagated``). The provider does
take an ``interceptor``, which :meth:`CrewAIAdapter.connection_kwargs` supplies:
with one set, it builds ``httpx`` clients that do not follow redirects, and the
interceptor keeps credentials to checked endpoints.

Retries (#734): ``max_retries=0`` turns the provider's OpenAI client retries
off, so a 5xx is not retried at all (the transport is not in the path). CrewAI
itself wraps every ``BaseLLM.call``/``acall`` in a rate-limit retry (3
attempts) that takes any 429 for a throttle and has no setting to turn it off,
so a budget refusal is sent 3 times: an asserted exemption in
``tests/unit/test_framework_retries.py``.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any

import httpx

from ..core.masking import masked
from ..core.transport import Origin, origin_of, strip_credential_headers
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from crewai import BaseLLM


class CrewAIAdapter(Adapter):
    extra = "crewai"
    # CrewAI's provider owns the transport, so no response reaches donkey.last_call
    # (#362, the same reason as the conformance kit's correlation_id_propagated exemption).
    observes_last_call = False

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for a ``crewai.LLM(model="openai/<id>", **kwargs)`` you
        build yourself. With ``base_url`` set, ``crewai.LLM`` routes to its native
        OpenAI provider, which takes ``base_url``/``extra_headers`` and builds its
        own client from one set of params for both its sync and async clients,
        so the shared http client cannot be injected (BG §1.8 exemption; the
        conformance kit).

        ``interceptor`` (present when CrewAI is installed) makes that provider
        build plain ``httpx`` clients around a transport hook, so redirects are
        not followed, and the hook removes the credential headers from any
        request to an origin the shared client was not checked for."""
        conn = self._openai_connection()
        return masked(
            {
                "base_url": conn["base_url"],
                "api_key": conn["api_key"],
                "extra_headers": conn["default_headers"],
                "max_retries": 0,
                **self._interceptor_kwarg(),
            }
        )

    def _interceptor_kwarg(self) -> dict[str, Any]:
        try:
            cls = _interceptor_class()
        except ImportError:
            return {}
        return {"interceptor": cls(self._http.checked_origins)}

    def llm(self, model: str, **kw: Any) -> BaseLLM:
        """Return a native CrewAI LLM pointed at the proxy (BG §1.8).

        Typed ``-> BaseLLM``, not ``-> LLM``: the ``openai/`` prefix routes
        ``crewai.LLM``'s factory to a provider subclass (docs/verified-apis.md
        §8, #640/#684). A ``base_url``/``api_base`` override must pass the https
        check."""
        self._allow_endpoints(kw, "base_url", "api_base")
        from crewai import LLM  # VERIFY name/path: docs/verified-apis.md §8

        # The ``openai/`` prefix (with ``base_url``) routes CrewAI's factory to its
        # native OpenAI provider, which strips it before the request.
        return LLM(model=f"openai/{model}", **{**self.connection_kwargs(), **kw})


@functools.cache
def _interceptor_class() -> type:
    """Built on first use: the base class is CrewAI's, an optional dependency."""
    from crewai.llms.hooks.base import BaseInterceptor

    class CheckedEndpointInterceptor(BaseInterceptor[httpx.Request, httpx.Response]):
        """Removes credential headers from requests to origins outside ``origins``
        (the shared client's live set of checked endpoints)."""

        def __init__(self, origins: set[Origin]) -> None:
            self._origins = origins

        def __eq__(self, other: object) -> bool:
            if not isinstance(other, CheckedEndpointInterceptor):
                return NotImplemented
            return self._origins == other._origins

        __hash__ = object.__hash__

        def on_outbound(self, message: httpx.Request) -> httpx.Request:
            if origin_of(message.url) not in self._origins:
                strip_credential_headers(message)
            return message

        def on_inbound(self, message: httpx.Response) -> httpx.Response:
            return message

        async def aon_outbound(self, message: httpx.Request) -> httpx.Request:
            return self.on_outbound(message)

        async def aon_inbound(self, message: httpx.Response) -> httpx.Response:
            return message

    return CheckedEndpointInterceptor


def llm(model: str, **kw: Any) -> BaseLLM:
    """Module-level convenience: a native CrewAI LLM at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().crewai.llm(model, **kw)``."""
    return default_adapter(CrewAIAdapter).llm(model, **kw)
