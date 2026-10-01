"""LlamaIndex adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

GOTCHA (BG §1.8): ``OpenAILike`` defaults ``is_chat_model=False``, which silently
routes to the completions endpoint and fails against a chat-only proxy. We
always set it True. This is the single most common LlamaIndex-with-a-gateway
bug.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.masking import masked
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from llama_index.llms.openai_like import OpenAILike


class LlamaIndexAdapter(Adapter):
    extra = "llamaindex"
    # Kept False while the conformance exemption table lists LlamaIndex; its
    # calls now go through the shared clients (http_client/async_http_client).
    observes_last_call = False

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAILike(model=…, **kwargs)`` you build
        yourself. Includes ``is_chat_model=True`` — never omit it (see the module
        docstring for the completions-endpoint gotcha). LlamaIndex uses
        ``api_base`` rather than ``base_url``. Sync and async calls send through
        the SDK's clients, which do not follow redirects and send credentials
        only to checked endpoints."""
        conn = self._openai_connection()
        return masked(
            {
                "api_base": conn["base_url"],
                "api_key": conn["api_key"],
                "default_headers": conn["default_headers"],
                "http_client": self._sync_http_client(),
                "async_http_client": self._http_client(),
                "max_retries": 0,  # we retry in transport (BG §1.1)
                "is_chat_model": True,  # never omit — see module docstring
                "is_function_calling_model": True,
            }
        )

    def llm(self, model: str, **kw: Any) -> OpenAILike:
        """Return a native ``OpenAILike`` at the proxy. An ``api_base`` override
        must pass the https check."""
        self._allow_endpoints(kw, "api_base")
        with self._native_import():
            from llama_index.llms.openai_like import (
                OpenAILike,  # VERIFY name/path: docs/verified-apis.md §8
            )

        return OpenAILike(model=model, **{**self.connection_kwargs(), **kw})


def llm(model: str, **kw: Any) -> OpenAILike:
    """Module-level convenience: a native ``OpenAILike`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().llamaindex.llm(model, **kw)``."""
    return default_adapter(LlamaIndexAdapter).llm(model, **kw)
