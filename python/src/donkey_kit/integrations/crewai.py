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
correlation-ID-per-run degrade to per-client, the same documented, asserted
conformance exemption as ADK (the conformance kit's ``correlation_id_propagated``).

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.masking import masked
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
        own client, so the shared http client is not injected here (BG §1.8
        exemption; the conformance kit)."""
        conn = self._openai_connection()
        return masked(
            {
                "base_url": conn["base_url"],
                "api_key": conn["api_key"],
                "extra_headers": conn["default_headers"],
            }
        )

    def llm(self, model: str, **kw: Any) -> BaseLLM:
        """Return a native CrewAI LLM pointed at the proxy (BG §1.8).

        Typed ``-> BaseLLM``, not ``-> LLM``: the ``openai/`` prefix routes
        ``crewai.LLM``'s factory to a provider subclass (docs/verified-apis.md
        §8, #640/#684)."""
        from crewai import LLM  # VERIFY name/path: docs/verified-apis.md §8

        # The ``openai/`` prefix (with ``base_url``) routes CrewAI's factory to its
        # native OpenAI provider, which strips it before the request.
        return LLM(model=f"openai/{model}", **{**self.connection_kwargs(), **kw})


def llm(model: str, **kw: Any) -> BaseLLM:
    """Module-level convenience: a native CrewAI LLM at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().crewai.llm(model, **kw)``."""
    return default_adapter(CrewAIAdapter).llm(model, **kw)
