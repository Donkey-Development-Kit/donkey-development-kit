"""OpenAI Agents SDK adapter (``donkey.openai_agents``) (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

The OpenAI Agents SDK (pip ``openai-agents``, import ``agents``) models a
provider as an ``OpenAIChatCompletionsModel`` wrapping an ``AsyncOpenAI`` client.
Because we construct that client ourselves, header AND transport injection are
both available (full injection) — the preferred pattern anywhere a framework
accepts a pre-built OpenAI client (BG §1.8).

Point the SDK's *model* at the proxy per-agent rather than mutating the global
default client, so one process can mix governed and ungoverned models.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..core.masking import masked
from . import AdapterCapabilities
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from agents import OpenAIChatCompletionsModel

__all__ = ["OpenAIAgentsAdapter", "model"]


class OpenAIAgentsAdapter(Adapter):
    """Governed OpenAI Agents SDK objects, reached as ``donkey.openai_agents``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``model(model)`` builds an
    ``OpenAIChatCompletionsModel``. ``connection_kwargs()`` returns the same
    settings for building it yourself.

    Supported at ``connection_kwargs()`` only (`BG §1.8`): that accessor is the
    supported surface, and the factories are conveniences over it.

    Raises:
        ImportError: ``donkey.openai_agents`` was read without the ``openai-agents``
            extra installed; the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/openai
    """

    # An AsyncOpenAI on the shared client (#726).
    factories = MappingProxyType(
        {
            "model": AdapterCapabilities(
                transport="shared",
                sync=False,
                streaming=True,
                typed_refusals=True,
                observes_last_call=True,
            ),
        }
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIChatCompletionsModel(model=…, **kwargs)``
        you build yourself. Unlike the OpenAI-compatible adapters this returns a
        single ``openai_client`` key holding a pre-built native ``AsyncOpenAI``
        bound to the proxy — the Agents SDK takes a ready-made client, not loose
        connection kwargs, so header AND transport injection travel as one object
        (BG §1.8). Same client the factory uses — one source of truth for the
        proxy connection."""
        return masked({"openai_client": self._proxy_openai_client()})

    def model(self, model: str, **kw: Any) -> OpenAIChatCompletionsModel:
        """Return a native ``OpenAIChatCompletionsModel`` pointed at the proxy,
        ready to pass into ``agents.Agent(model=...)`` (BG §1.8)."""
        with self._native_import():
            from agents import (
                OpenAIChatCompletionsModel,  # VERIFY name/path: docs/verified-apis.md §8
            )

        return OpenAIChatCompletionsModel(model=model, **{**self.connection_kwargs(), **kw})


def model(model: str, **kw: Any) -> OpenAIChatCompletionsModel:
    """Module-level convenience: a native ``OpenAIChatCompletionsModel`` at the
    proxy using a cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().openai_agents.model(model, **kw)``."""
    return default_adapter(OpenAIAgentsAdapter).model(model, **kw)
