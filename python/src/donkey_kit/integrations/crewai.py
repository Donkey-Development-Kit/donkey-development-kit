"""CrewAI adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

CrewAI reaches models through its own ``crewai.LLM`` class, which wraps LiteLLM.
As with ADK, an OpenAI-compatible proxy is addressed with the ``openai/`` model
prefix plus ``base_url``.

Header injection: via LiteLLM's ``extra_headers``. We CANNOT inject our httpx
client — LiteLLM owns the transport. Consequence: transport retries and
correlation-ID-per-run degrade to per-client, the same documented, asserted
conformance exemption as ADK (the conformance kit's ``correlation_id_propagated``).

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from crewai import LLM


class CrewAIAdapter(Adapter):
    extra = "crewai"
    # LiteLLM owns the transport, so no response reaches donkey.last_call (#362,
    # the same reason as the conformance kit's correlation_id_propagated exemption).
    observes_last_call = False

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for a ``crewai.LLM(model="openai/<id>", **kwargs)`` you
        build yourself. ``crewai.LLM`` forwards to LiteLLM, which uses
        ``base_url``/``extra_headers`` and owns its own transport, so the shared
        http client is not injected here (BG §1.8 exemption; the conformance kit)."""
        conn = self._openai_connection()
        return {
            "base_url": conn["base_url"],
            "api_key": conn["api_key"],
            "extra_headers": conn["default_headers"],
        }

    def llm(self, model: str, **kw: Any) -> LLM:
        """Return a native ``crewai.LLM`` pointed at the proxy (BG §1.8)."""
        from crewai import LLM  # VERIFY name/path: docs/verified-apis.md §8

        # LiteLLM's OpenAI-compatible route needs the ``openai/`` prefix.
        return LLM(model=f"openai/{model}", **{**self.connection_kwargs(), **kw})


def llm(model: str, **kw: Any) -> LLM:
    """Module-level convenience: a native ``crewai.LLM`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().crewai.llm(model, **kw)``."""
    return default_adapter(CrewAIAdapter).llm(model, **kw)
