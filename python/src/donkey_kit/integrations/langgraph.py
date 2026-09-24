"""LangGraph / LangChain adapter (BG §1.8).

The one deep, conformance-gated adapter (BG §1.8, #198). Header injection is
FULL (``default_headers`` + our custom async http client, so every governed
header and transport hook is on the wire) — the best-case adapter, which is why
it is the one held to the conformance bar.

Three things hang off it, in ergonomic-lockstep with the rest of the SDK:

* :meth:`LangGraphAdapter.chat_model` / :func:`chat_model` / calling the adapter
  (``donkey.langgraph("gpt-4o")``) — three ways to get the **native**
  ``langchain_openai.ChatOpenAI`` pointed at the proxy (BG §1.8, README §2).
* :meth:`LangGraphAdapter.connection_kwargs` — the governed kwargs to spread
  into a ``ChatOpenAI`` you build yourself.
* :func:`typed_refusals` — a context manager that turns the proxy refusal a node
  raises back into the SDK's typed taxonomy (see below, #198 AC3).

Correlation IDs reach every node for free (#195): LangGraph runs nodes on
``asyncio`` tasks that copy the current context, so a run id bound with
``donkey.run(id=…)`` is visible via ``current_correlation_id()`` inside each
node with nothing threaded through graph state.

All class names / kwargs are UNVERIFIED until verification discipline — see
docs/verified-apis.md §8.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, cast

from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


class LangGraphAdapter(Adapter):
    extra = "langgraph"

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs to spread into a ``ChatOpenAI(model=…, **kwargs)`` you
        build yourself (BG §1.8). Same values the factory uses — one source of
        truth for the proxy connection."""
        conn = self._openai_connection()
        return {
            **conn,  # base_url, api_key, default_headers
            "http_async_client": self._http_client(),  # our client, our hooks
            "max_retries": 0,  # we retry in transport (BG §1.1)
            # Target the proxy's LIVE-VERIFIED endpoint: the data plane is the
            # OpenAI Responses API (``/responses``, docs/verified-apis.md §4) — the same route the
            # raw ``donkey.llm`` client uses. Left at ChatOpenAI's chat-completions
            # default, ``donkey.langgraph(...)`` would call an UNVERIFIED
            # ``/chat/completions`` route and risk a 404 in a real sandbox (verification
            # discipline).
            # Override per call (``use_responses_api=False``) if a deployment
            # exposes chat-completions instead.
            "use_responses_api": True,
        }

    def chat_model(self, model: str, **kw: Any) -> ChatOpenAI:
        """Return a native ``ChatOpenAI`` pointed at the proxy (BG §1.8)."""
        from langchain_openai import ChatOpenAI  # VERIFY name/path: docs/verified-apis.md §8

        return ChatOpenAI(model=model, **self.connection_kwargs(), **kw)

    def __call__(self, model: str, **kw: Any) -> ChatOpenAI:
        """Callable sugar: ``donkey.langgraph("gpt-4o")`` is ``chat_model(...)``.

        The third ergonomic form alongside :meth:`chat_model` and the module-level
        :func:`chat_model` (README §2). Returns the same native ``ChatOpenAI``."""
        return self.chat_model(model, **kw)

    @staticmethod
    def typed_refusals() -> AbstractContextManager[None]:
        """Adapter-bound alias for the module-level :func:`typed_refusals`.

        Lets ``with donkey.langgraph.typed_refusals(): ...`` read naturally next
        to ``donkey.langgraph("gpt-4o")``. Carries no per-adapter state — the
        bridge is pure ``classify()`` — so it is a plain delegate."""
        return typed_refusals()


def chat_model(model: str, **kw: Any) -> ChatOpenAI:
    """Module-level convenience: a native ``ChatOpenAI`` at the proxy using a
    cached default :class:`~donkey_kit.Donkey` configured from the environment.
    Equivalent to ``Donkey.from_env().langgraph.chat_model(model, **kw)``."""
    return default_adapter(LangGraphAdapter).chat_model(model, **kw)


@contextmanager
def typed_refusals() -> Iterator[None]:
    """Surface a proxy refusal raised *inside a node* as the SDK's typed
    exception, not a framework-wrapped generic error (#198 AC3).

    A node that calls the model directly gets whatever the framework raises. For
    a governed refusal that is an ``openai.APIStatusError`` — and LangChain
    re-wraps it (e.g. ``OpenAIPermissionDeniedError``) as a *subclass* of it, so
    both the raw-client and LangChain-wrapped paths are caught by the one
    ``except`` — carrying the originating ``httpx`` response. Wrap the call and
    the refusal comes back through :func:`~donkey_kit.core.errors.classify`::

        async def call_model(state: State) -> State:
            with donkey.langgraph.typed_refusals():
                reply = await model.ainvoke(state["messages"])
            return {"messages": [reply]}

    A PII block then propagates out of ``graph.ainvoke(...)`` as
    :class:`~donkey_kit.core.errors.PIIDetected`, a budget block as
    :class:`~donkey_kit.core.errors.TokenBudgetExceeded`, etc. — each with the
    correlation/call ids the client sent (``classify`` reads them back off the
    response's request), so a node's refusal joins the run like any other call.

    This is a **documented pattern plus a helper**, not a transport change: the
    transport's ``_on_refusal`` hook stays a no-op. Errors with no HTTP response
    (``APIConnectionError``/``APITimeoutError``, which are *not*
    ``APIStatusError``) are transport failures, not gateway refusals, and pass
    through untouched.
    """
    import openai  # lazy: only the framework path needs it (the layered architecture)

    from ..core.errors import classify

    try:
        yield
    except openai.APIStatusError as exc:
        # ``APIStatusError`` always carries the originating response; classify()
        # maps it (and reads back the sent correlation/call ids) into the typed
        # taxonomy. ``from exc`` keeps the framework wrapper as the cause.
        # openai>=3 vendors its own httpx, so ``exc.response`` is statically a
        # distinct-but-duck-identical Response type; cast erases it to the one
        # classify wants. `cast(Any, …)` (not `cast("httpx.Response", …)`) so this
        # typechecks clean under BOTH majors: under openai<3 ``exc.response`` is
        # already ``httpx.Response`` and a cast to it is `redundant-cast` (#597).
        raise classify(cast(Any, exc.response)) from exc
