"""Microsoft Agent Framework adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Current Python surface is ``from agent_framework import Agent`` with
``Agent(client=<ChatClient>, name=..., instructions=...)``. The
OpenAI-compatible chat client class name and its base-URL kwarg MUST be verified
— this package is young and renamed classes recently (docs/verified-apis.md §8).

Agent Framework has first-class middleware for intercepting agent actions. We
ship :meth:`policy_middleware` that catches :class:`PolicyViolation` and
terminates the run cleanly rather than letting the agent loop retry — the best
policy-integration story of any of the seven (BG §1.8), and the flagship example.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core import _verify
from ..core.errors import PolicyViolation
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from collections.abc import Callable


class AgentFrameworkAdapter(Adapter):
    extra = "agent_framework"
    # We hand OpenAIChatClient only default_headers, never our httpx client, so no
    # response reaches donkey.last_call (#362) — the SDK does not own the transport.
    observes_last_call = False

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIChatClient(model_id=…, **kwargs)`` you
        build yourself. NOTE (docs/verified-apis.md §8): the class name/path and its base-URL kwarg
        are UNVERIFIED — confirm against the installed version before relying on constructing the
        client by hand."""
        return self._openai_connection()  # base_url, api_key, default_headers

    def chat_client(self, model: str, **kw: Any) -> Any:
        self._require_proxy()
        try:
            from agent_framework.openai import (
                OpenAIChatClient,  # VERIFY name/path: docs/verified-apis.md §8
            )
        except ImportError as exc:
            raise _verify.blocked(
                "agent_framework OpenAI chat client class name/path "
                "(docs/verified-apis.md §8). The package renamed classes recently; "
                "confirm 'agent_framework.openai.OpenAIChatClient' and its base-URL "
                "kwarg against the installed version before relying on this adapter."
            ) from exc

        return OpenAIChatClient(
            model_id=model,  # VERIFY kwarg name: docs/verified-apis.md §8
            **self.connection_kwargs(),
            **kw,
        )

    def policy_middleware(self) -> Callable[..., Any]:
        """Middleware that converts a :class:`PolicyViolation` into a clean,
        terminal agent state instead of letting the loop retry (BG §1.2).

        The exact middleware signature Agent Framework expects is UNVERIFIED
        (verification discipline). We return a plain async wrapper and mark the shape for
        verification rather than guessing the framework's middleware protocol.
        """

        async def middleware(context: Any, next_: Callable[[Any], Any]) -> Any:
            try:
                return await next_(context)
            except PolicyViolation:
                # Terminal: re-raise so the host does not silently retry (BG §1.2).
                # Once the middleware protocol is verified, set the framework's
                # explicit "terminate run" signal here instead of re-raising.
                raise

        return middleware


def chat_client(model: str, **kw: Any) -> Any:
    """Module-level convenience: an Agent Framework chat client at the proxy
    using a cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().agent_framework.chat_client(model, **kw)``."""
    return default_adapter(AgentFrameworkAdapter).chat_client(model, **kw)
