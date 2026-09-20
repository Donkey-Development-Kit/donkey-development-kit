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

from typing import TYPE_CHECKING, Any

from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from agents import OpenAIChatCompletionsModel


class OpenAIAgentsAdapter(Adapter):
    extra = "openai-agents"

    def _proxy_openai_client(self) -> Any:
        """A native ``AsyncOpenAI`` client bound to the proxy: our shared http
        client + the verified consumer-auth headers. One source of truth for the
        governed connection (BG §1.8)."""
        conn = self._openai_connection()
        from openai import AsyncOpenAI

        # openai 3.x retyped http_client to httpx2.AsyncClient (a distinct class from a
        # separate distribution); our DonkeyAsyncClient is an httpx subclass, duck-typed
        # at runtime. Typecheck-only mismatch — docs/verified-apis.md (openai >=3.0 row).
        return AsyncOpenAI(
            base_url=conn["base_url"],
            api_key=conn["api_key"],
            default_headers=conn["default_headers"],
            http_client=self._http_client(),  # type: ignore[arg-type]
            max_retries=0,  # we retry in transport (BG §1.1)
        )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIChatCompletionsModel(model=…, **kwargs)``
        you build yourself. Unlike the OpenAI-compatible adapters this returns a
        single ``openai_client`` key holding a pre-built native ``AsyncOpenAI``
        bound to the proxy — the Agents SDK takes a ready-made client, not loose
        connection kwargs, so header AND transport injection travel as one object
        (BG §1.8). Same client the factory uses — one source of truth for the
        proxy connection."""
        return {"openai_client": self._proxy_openai_client()}

    def model(self, model: str, **kw: Any) -> OpenAIChatCompletionsModel:
        """Return a native ``OpenAIChatCompletionsModel`` pointed at the proxy,
        ready to pass into ``agents.Agent(model=...)`` (BG §1.8)."""
        from agents import OpenAIChatCompletionsModel  # verified: docs/verified-apis.md §8

        return OpenAIChatCompletionsModel(model=model, **self.connection_kwargs(), **kw)


def model(model: str, **kw: Any) -> OpenAIChatCompletionsModel:
    """Module-level convenience: a native ``OpenAIChatCompletionsModel`` at the
    proxy using a cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().openai_agents.model(model, **kw)``."""
    return default_adapter(OpenAIAgentsAdapter).model(model, **kw)
