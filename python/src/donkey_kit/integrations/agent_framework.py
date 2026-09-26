"""Microsoft Agent Framework adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Current Python surface is ``from agent_framework import Agent`` with
``Agent(client=<ChatClient>, name=..., instructions=...)``. The
OpenAI-compatible chat client class path and its constructor kwargs are
VERIFIED against agent-framework 1.19.0 (docs/verified-apis.md §8):
``agent_framework.openai.OpenAIChatClient`` takes ``model``, ``base_url``,
``api_key`` and ``default_headers``. Both the import and the construction stay
guarded so a future upstream rename surfaces as a ``_verify.blocked(...)``
refusal, never a raw ``ImportError``/``TypeError`` reaching the caller.

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
        """Governed kwargs for an ``OpenAIChatClient(model=…, **kwargs)`` you
        build yourself. VERIFIED against agent-framework 1.19.0
        (docs/verified-apis.md §8): ``base_url``/``api_key``/``default_headers``
        are all accepted by the constructor."""
        return self._openai_connection()  # base_url, api_key, default_headers

    def chat_client(self, model: str, **kw: Any) -> Any:
        self._require_proxy()
        try:
            from agent_framework.openai import (
                OpenAIChatClient,  # verified: docs/verified-apis.md §8 (1.19.0)
            )
        except ImportError as exc:
            raise _verify.blocked(
                "agent_framework.openai.OpenAIChatClient import "
                "(docs/verified-apis.md §8). The class path is VERIFIED against "
                "agent-framework 1.19.0; an ImportError here means the package is "
                "absent or has renamed the class again. Install 'agent-framework' "
                "or confirm the class path against your installed version."
            ) from exc

        try:
            return OpenAIChatClient(
                model=model,  # verified: docs/verified-apis.md §8 (1.19.0)
                **{**self.connection_kwargs(), **kw},
            )
        except TypeError as exc:
            # A TypeError from the constructor means a kwarg this adapter relies on
            # was renamed upstream. Surface it as a verification refusal, not a raw
            # TypeError leaking out of the SDK (§0.3, BG §1.8).
            raise _verify.blocked(
                "agent_framework.openai.OpenAIChatClient constructor signature "
                "(docs/verified-apis.md §8). Verified against agent-framework 1.19.0 "
                "(model=, base_url=, api_key=, default_headers=); a TypeError here "
                "means the installed version renamed a kwarg. Confirm the signature "
                "against your installed version and update the adapter."
            ) from exc

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
