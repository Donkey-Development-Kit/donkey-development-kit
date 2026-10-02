"""Microsoft Agent Framework adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Current Python surface is ``from agent_framework import Agent`` with
``Agent(client=<ChatClient>, name=..., instructions=...)``. agent-framework-openai
ships two OpenAI-compatible chat clients, confirmed offline against
agent-framework 1.19.0 (docs/verified-apis.md §8):
``agent_framework.openai.OpenAIChatCompletionClient`` (Chat Completions,
``POST /chat/completions``) and ``agent_framework.openai.OpenAIChatClient``
(Responses API, ``POST /responses``). Both take ``model``, ``base_url``,
``api_key``, ``default_headers`` and ``async_client``. :meth:`chat_client`
builds the Responses client by default: ``/responses`` is the data-plane route
in docs/verified-apis.md §2, the same one ``donkey.llm`` and the LangGraph
adapter use. Not every upstream serves it (an Azure OpenAI route answers with a
404), so ``api="chat_completions"`` builds the Chat Completions client for such
routes (#826). Both the
import and the construction stay guarded so a future upstream rename surfaces
as a ``_verify.blocked(...)`` refusal, never a raw ``ImportError``/``TypeError``
reaching the caller; a missing package raises the curated install hint every
adapter raises (#741).

Agent Framework has first-class middleware for intercepting chat calls.
:meth:`AgentFrameworkAdapter.policy_middleware` is a chat middleware that turns
a proxy refusal into the SDK's typed exception (for example
:class:`~donkey_kit.core.errors.PIIDetected`), so the run ends on the typed
refusal instead of a generic ``ChatClientException`` (BG §1.2).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import TracebackType
from typing import TYPE_CHECKING, Any, Literal, cast, overload

from ..core import _verify
from ..core.masking import masked
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

__all__ = ["AgentFrameworkAdapter", "ChatAPI", "chat_client"]

ChatAPI = Literal["responses", "chat_completions"]
# The agent-framework.openai class each ``api=`` value builds (docs/verified-apis.md §8).
_CHAT_CLIENT_CLASSES: dict[str, str] = {
    "responses": "OpenAIChatClient",
    "chat_completions": "OpenAIChatCompletionClient",
}


class AgentFrameworkAdapter(Adapter):
    """Governed Microsoft Agent Framework objects, reached as ``donkey.agent_framework``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``chat_client(model)`` builds an
    ``OpenAIChatClient``. ``connection_kwargs()`` returns the same settings for
    building it yourself.

    Supported at ``connection_kwargs()`` only (`BG §1.8`): that accessor is the
    supported surface, and the factories are conveniences over it.

    Raises:
        ImportError: ``donkey.agent_framework`` was read without the
            ``agent_framework`` extra installed; the message carries the install
            command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/agent-framework
    """

    # Kept False while the conformance exemption table lists Agent Framework; its
    # calls now go through the shared client (async_client).
    observes_last_call = False

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIChatClient(model=…, **kwargs)`` (or
        ``OpenAIChatCompletionClient``) you build yourself. Confirmed offline against
        agent-framework 1.19.0 (docs/verified-apis.md §8): both constructors
        accept ``base_url``/``api_key``/``default_headers``. ``async_client`` is
        an ``AsyncOpenAI`` that sends through the SDK's shared client, which does
        not follow redirects and sends credentials only to checked endpoints;
        the constructor uses it as given."""
        return masked(
            {
                **self._openai_connection(),  # base_url, api_key, default_headers
                **self._proxy_openai_client_kwarg("async_client"),
            }
        )

    @overload
    def chat_client(
        self, model: str, *, api: Literal["responses"] = ..., **kw: Any
    ) -> OpenAIChatClient: ...

    @overload
    def chat_client(
        self, model: str, *, api: Literal["chat_completions"], **kw: Any
    ) -> OpenAIChatCompletionClient: ...

    def chat_client(
        self, model: str, *, api: ChatAPI = "responses", **kw: Any
    ) -> OpenAIChatClient | OpenAIChatCompletionClient:
        """Return a native Agent Framework chat client at the proxy.

        ``api="responses"`` (the default) returns an ``OpenAIChatClient``, which
        sends ``POST /responses`` (docs/verified-apis.md §2). An Azure OpenAI
        route answers that with a 404 (#826); for such a route pass
        ``api="chat_completions"`` to get an ``OpenAIChatCompletionClient``,
        which sends ``POST /chat/completions``. A ``base_url`` override must
        pass the https check."""
        if api not in _CHAT_CLIENT_CLASSES:
            raise ValueError(f"api must be 'responses' or 'chat_completions', not {api!r}")
        cls_name = _CHAT_CLIENT_CLASSES[api]
        self._allow_endpoints(kw, "base_url")
        self._require_proxy()
        # A missing module (the package or a dependency of it) is the curated
        # install hint; a missing name in a module that imports is a rename.
        with self._native_import():
            try:
                # Both class paths confirmed offline: docs/verified-apis.md §8 (1.19.0).
                if api == "chat_completions":
                    from agent_framework.openai import OpenAIChatCompletionClient as _completions

                    client_cls: type[Any] = _completions
                else:
                    from agent_framework.openai import OpenAIChatClient as _responses

                    client_cls = _responses
            except ImportError as exc:
                if isinstance(exc, ModuleNotFoundError):
                    raise
                raise _verify.blocked(
                    f"agent_framework.openai.{cls_name} import "
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
            return cast(
                "OpenAIChatClient | OpenAIChatCompletionClient",
                client_cls(
                    model=model,  # confirmed offline: docs/verified-apis.md §8 (1.19.0)
                    **{**conn, **kw},
                ),
            )
        except TypeError as exc:
            # A TypeError from the constructor means a kwarg this adapter relies on
            # was renamed upstream. Surface it as a verification refusal, not a raw
            # TypeError leaking out of the SDK (§0.3, BG §1.8).
            raise _verify.blocked(
                f"agent_framework.openai.{cls_name} constructor signature "
                "(docs/verified-apis.md §8). Verified against agent-framework 1.19.0 "
                "(model=, base_url=, api_key=, default_headers=); a TypeError here "
                "means the installed version renamed a kwarg. Confirm the signature "
                "against your installed version and update the adapter."
            ) from exc

    def policy_middleware(self) -> Callable[..., Any]:
        """A chat middleware for ``Agent(..., middleware=[...])`` that raises a
        proxy refusal as the SDK's typed exception (BG §1.2).

        Both chat clients wrap every openai error in a ``ChatClientException``
        (``OpenAIContentFilterException`` for a content-filter 400).
        This middleware finds the ``openai.APIStatusError`` behind it and raises
        :func:`~donkey_kit.core.errors.classify` of its response instead, so a
        PII block ends ``agent.run()`` as
        :class:`~donkey_kit.core.errors.PIIDetected` with the correlation and
        call ids that were sent. The original is kept on ``.framework_error``.
        Streaming runs are covered too: the conversion is attached to each pull
        of the response stream, where a streamed refusal surfaces.

        The client sends through the shared client with retries off, so the
        refused request is sent once. Marked with ``@chat_middleware``, confirmed
        offline against agent-framework 1.19.0 (docs/verified-apis.md §8).
        """
        try:
            from agent_framework import (
                chat_middleware,  # confirmed offline: docs/verified-apis.md §8 (1.19.0)
            )
        except ImportError as exc:
            raise _verify.blocked(
                "agent_framework.chat_middleware import (docs/verified-apis.md §8). "
                "The decorator is confirmed offline against agent-framework 1.19.0; an "
                "ImportError here means the package is absent or has renamed it. "
                "Install 'agent-framework' or confirm the name against your installed version."
            ) from exc

        async def donkey_policy_middleware(
            context: Any, call_next: Callable[[], Awaitable[None]]
        ) -> None:
            with _TypedRefusals():
                await call_next()
            if context.stream and context.result is not None:
                # A streamed refusal surfaces when the caller pulls the stream,
                # after this middleware has returned.
                context.result.with_pull_context_manager(_TypedRefusals)

        # Called, not applied with @: the decorator is untyped without the package.
        return cast("Callable[..., Any]", chat_middleware(donkey_policy_middleware))


class _TypedRefusals(AbstractContextManager[None]):
    """Re-raise an error caused by an ``openai.APIStatusError`` as the typed
    refusal :func:`~donkey_kit.core.errors.classify` maps its response to.

    A class, not ``@contextmanager``, for the reason given on LangGraph's
    ``_TypedRefusals``: a generator frame would keep the framework error, whose
    message repeats the gateway text, in the typed error's traceback.
    """

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is None:
            return None
        import openai  # lazy: only reached once the framework has raised

        from ..core.errors import classify

        # Agent Framework chains the openai error as __cause__ of its own
        # ChatClientException; walk the chain rather than one level.
        cause: BaseException | None = exc
        while cause is not None and not isinstance(cause, openai.APIStatusError):
            cause = cause.__cause__
        if cause is None:
            return None
        # openai>=3 vendors its own httpx; cast for the reason in langgraph.py.
        typed = classify(cast(Any, cause.response))
        typed.framework_error = exc
        del exc, exc_type, tb, cause
        raise typed from None


@overload
def chat_client(model: str, *, api: Literal["responses"] = ..., **kw: Any) -> OpenAIChatClient: ...


@overload
def chat_client(
    model: str, *, api: Literal["chat_completions"], **kw: Any
) -> OpenAIChatCompletionClient: ...


def chat_client(
    model: str, *, api: ChatAPI = "responses", **kw: Any
) -> OpenAIChatClient | OpenAIChatCompletionClient:
    """Module-level convenience: an Agent Framework chat client at the proxy
    using a cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().agent_framework.chat_client(model, api=api, **kw)``."""
    return default_adapter(AgentFrameworkAdapter).chat_client(model, api=api, **kw)
