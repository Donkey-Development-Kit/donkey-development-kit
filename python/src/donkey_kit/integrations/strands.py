"""Strands Agents adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

``client_args`` is forwarded to the underlying OpenAI client, so header AND
transport injection are both available (full injection). Strands also has
lifecycle hooks (``BeforeToolCallEvent`` and friends) — used elsewhere for the
policy-termination pattern (BG §1.2, BG §1.8).

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from strands.models.openai import OpenAIModel


class StrandsAdapter(Adapter):
    extra = "strands"

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIModel(model_id=…, **kwargs)`` you build
        yourself. Strands forwards ``client_args`` to the underlying OpenAI
        client, so header AND transport injection are both available."""
        conn = self._openai_connection()
        return {
            "client_args": {
                **conn,  # base_url, api_key, default_headers
                "http_client": self._http_client(),
            },
        }

    def model(self, model: str, **kw: Any) -> OpenAIModel:
        from strands.models.openai import OpenAIModel  # VERIFY name/path: docs/verified-apis.md §8

        return OpenAIModel(model_id=model, **self.connection_kwargs(), **kw)


def model(model: str, **kw: Any) -> OpenAIModel:
    """Module-level convenience: a native ``OpenAIModel`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().strands.model(model, **kw)``."""
    return default_adapter(StrandsAdapter).model(model, **kw)
