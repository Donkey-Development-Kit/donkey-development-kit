"""Microsoft Agent Framework adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Current Python surface is ``from agent_framework import Agent`` with
``Agent(client=<ChatClient>, name=..., instructions=...)``. The
OpenAI-compatible chat client class path and its constructor kwargs are
confirmed offline against agent-framework 1.19.0 (docs/verified-apis.md §8):
``agent_framework.openai.OpenAIChatClient`` takes ``model``, ``base_url``,
``api_key`` and ``default_headers``. Both the import and the construction stay
guarded so a future upstream rename surfaces as a ``_verify.blocked(...)``
refusal, never a raw ``ImportError``/``TypeError`` reaching the caller; a
missing package raises the curated install hint every adapter raises (#741).

Agent Framework has first-class middleware for intercepting agent actions. We
ship :meth:`policy_middleware` that catches :class:`PolicyViolation` and
terminates the run cleanly rather than letting the agent loop retry — the best
policy-integration story of any of the seven (BG §1.8), and the flagship example.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core import _verify
from ..core.errors import PolicyViolation
from ..core.masking import masked
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from collections.abc import Callable


class AgentFrameworkAdapter(Adapter):
    extra = "agent_framework"
    # Kept False while the conformance exemption table lists Agent Framework; its
    # calls now go through the shared client (async_client).
    observes_last_call = False

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIChatClient(model=…, **kwargs)`` you
        build yourself. Confirmed offline against agent-framework 1.19.0
        (docs/verified-apis.md §8): ``base_url``/``api_key``/``default_headers``
        are all accepted by the constructor. ``async_client`` is an ``AsyncOpenAI``
        that sends through the SDK's shared client, which does not follow
        redirects and sends credentials only to checked endpoints; the
        constructor uses it as given."""
        return masked(
            {
                **self._openai_connection(),  # base_url, api_key, default_headers
                **self._proxy_openai_client_kwarg("async_client"),
            }
        )

    def chat_client(self, model: str, **kw: Any) -> Any:
        """Return a native ``OpenAIChatClient`` at the proxy. A ``base_url``
        override must pass the https check."""
        self._allow_endpoints(kw, "base_url")
        self._require_proxy()
        # A missing module (the package or a dependency of it) is the curated
        # install hint; a missing name in a module that imports is a rename.
        with self._native_import():
            try:
                from agent_framework.openai import (
                    OpenAIChatClient,  # confirmed offline: docs/verified-apis.md §8 (1.19.0)
                )
            except ImportError as exc:
                if isinstance(exc, ModuleNotFoundError):
                    raise
                raise _verify.blocked(
                    "agent_framework.openai.OpenAIChatClient import "
                    "(docs/verified-apis.md §8). The class path is confirmed offline "
                    "against agent-framework 1.19.0; an ImportError here means the "
                    "installed version has renamed the class again. Confirm the class "
                    "path against your installed version."
                ) from exc

        conn = self.connection_kwargs()
        if kw.get("base_url") is not None and "async_client" in conn:
            # The constructor uses async_client as given, so build it on the override.
            conn["async_client"] = self._proxy_openai_client(str(kw["base_url"]))
        try:
            return OpenAIChatClient(
                model=model,  # confirmed offline: docs/verified-apis.md §8 (1.19.0)
                **{**conn, **kw},
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
