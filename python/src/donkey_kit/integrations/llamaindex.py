"""LlamaIndex adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

GOTCHA (BG §1.8): ``OpenAILike`` defaults ``is_chat_model=False``, which silently
routes to the completions endpoint and fails against a chat-only proxy. We
always set it True. This is the single most common LlamaIndex-with-a-gateway
bug.

GOTCHA (#829): LlamaIndex keys its reasoning-model handling (``O1_MODELS``) and
its context-window table on the exact bare OpenAI name. The
``<provider>/<model>`` names a model-based proxy routes on miss both, and
``OpenAILike`` gives every model a 3,900-token window. ``llm()`` resolves the
bare name after one provider prefix and applies both. ``connection_kwargs()``
carries no model, so an ``OpenAILike`` you build yourself gets neither.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..core.masking import masked
from . import AdapterCapabilities
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from llama_index.llms.openai_like import OpenAILike

__all__ = ["LlamaIndexAdapter", "llm"]


class LlamaIndexAdapter(Adapter):
    """Governed LlamaIndex objects, reached as ``donkey.llamaindex``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``llm(model)`` builds an
    ``OpenAILike``. ``connection_kwargs()`` returns the same settings for building
    it yourself.

    Supported at ``connection_kwargs()`` only (`BG §1.8`): that accessor is the
    supported surface, and the factories are conveniences over it.

    Raises:
        ImportError: ``donkey.llamaindex`` was read without the ``llamaindex`` extra
            installed; the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/llamaindex
    """

    # Both shared clients (http_client/async_http_client). observes_last_call is
    # kept False while the conformance exemption table lists LlamaIndex (#740).
    factories = MappingProxyType(
        {
            "llm": AdapterCapabilities(
                transport="shared",
                sync=True,
                streaming=True,
                typed_refusals=True,
                observes_last_call=False,
            ),
        }
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAILike(model=…, **kwargs)`` you build
        yourself. Includes ``is_chat_model=True`` — never omit it (see the module
        docstring for the completions-endpoint gotcha). LlamaIndex uses
        ``api_base`` rather than ``base_url``. Sync and async calls send through
        the SDK's clients, which do not follow redirects and send credentials
        only to checked endpoints."""
        conn = self._connection()
        return masked(
            {
                "api_base": conn["base_url"],
                "api_key": conn["api_key"],
                "default_headers": conn["default_headers"],
                "http_client": self.sync_http_client(),
                "async_http_client": self.http_client(),
                "max_retries": 0,  # we retry in transport (BG §1.1)
                "is_chat_model": True,  # never omit — see module docstring
                "is_function_calling_model": True,
            }
        )

    def llm(self, model: str, **kw: Any) -> OpenAILike:
        """Return a native ``OpenAILike`` at the proxy. An ``api_base`` override
        must pass the https check.

        Model defaults come from the bare name after one ``<provider>/`` prefix
        (#829). ``context_window`` comes from LlamaIndex's own table, or stays at
        its 3,900 default for a name the table doesn't know. A prefixed reasoning
        model gets ``temperature=1.0``, and ``max_tokens`` / ``reasoning_effort``
        are sent the way LlamaIndex sends them for the bare name. Caller kwargs
        win."""
        self._allow_endpoints(kw, "api_base")
        with self._native_import():
            from llama_index.llms.openai_like import (
                OpenAILike,  # VERIFY name/path: docs/verified-apis.md §8
            )

        kw = _model_defaults(model, kw)
        return OpenAILike(model=model, **{**self.connection_kwargs(), **kw})


def _model_defaults(model: str, kw: dict[str, Any]) -> dict[str, Any]:
    """``kw`` plus the defaults LlamaIndex would pick for the bare name after one
    ``<provider>/`` prefix (#829). ``llama-index-llms-openai`` is a dependency of
    ``-openai-like``, so its tables are present in any real install; without
    them, ``kw`` is returned as is."""
    try:
        from llama_index.llms.openai.utils import (  # VERIFY: docs/verified-apis.md §8
            O1_MODELS,
            openai_modelname_to_contextsize,
        )
    except ImportError:
        return kw
    _, sep, rest = model.partition("/")
    bare = rest if sep else model
    if "context_window" not in kw:
        try:
            kw["context_window"] = openai_modelname_to_contextsize(bare)
        except ValueError:
            pass  # not an OpenAI name: keep OpenAILike's default
    if sep and bare in O1_MODELS:
        kw = _reasoning_model_kwargs(kw)
    return kw


def _reasoning_model_kwargs(kw: dict[str, Any]) -> dict[str, Any]:
    """Apply to a prefixed reasoning-model name what LlamaIndex already does for
    the bare name. ``temperature`` becomes 1.0, because LlamaIndex otherwise
    sends 0.1, which gpt-5 rejects. ``max_tokens`` is sent as
    ``max_completion_tokens``, and ``reasoning_effort`` is sent instead of
    dropped. Both go through ``additional_kwargs``, which LlamaIndex merges into
    the request body. An explicit entry there wins."""
    kw = {"temperature": 1.0, **kw}
    extra = dict(kw.get("additional_kwargs") or {})
    max_tokens = kw.pop("max_tokens", None)
    if max_tokens is not None:
        extra.setdefault("max_completion_tokens", max_tokens)
    if kw.get("reasoning_effort") is not None:
        extra.setdefault("reasoning_effort", kw["reasoning_effort"])
    if extra:
        kw["additional_kwargs"] = extra
    return kw


def llm(model: str, **kw: Any) -> OpenAILike:
    """Module-level convenience: a native ``OpenAILike`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().llamaindex.llm(model, **kw)``."""
    return default_adapter(LlamaIndexAdapter).llm(model, **kw)
