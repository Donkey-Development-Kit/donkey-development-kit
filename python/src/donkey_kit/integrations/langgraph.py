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

from contextlib import AbstractContextManager
from types import TracebackType
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from ..core.masking import masked
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


class LangGraphAdapter(Adapter):
    """Governed LangChain objects, reached as ``donkey.langgraph``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``chat_model(model)`` builds a
    ``ChatOpenAI``. ``connection_kwargs()`` returns the same settings for building
    it yourself.

    The one deep, conformance-tested adapter (`BG §1.8`): its factories and
    ``connection_kwargs()`` are held to the conformance suite in CI.

    ``typed_refusals()`` re-raises a gateway refusal as the typed
    :class:`~donkey_kit.core.errors.DonkeyError` subclass.

    Raises:
        ImportError: ``donkey.langgraph`` was read without the ``langgraph`` extra
            installed; the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/langgraph
    """

    extra = "langgraph"

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs to spread into a ``ChatOpenAI(model=…, **kwargs)`` you
        build yourself (BG §1.8). Same values the factory uses — one source of
        truth for the proxy connection. Both ``ainvoke`` and ``invoke`` send
        through the SDK's clients (``http_async_client`` / ``http_client``), which
        do not follow redirects and send credentials only to checked endpoints."""
        conn = self._openai_connection()
        return masked(
            {
                **conn,  # base_url, api_key, default_headers
                "http_async_client": self.http_client(),  # our client, our hooks
                "http_client": self.sync_http_client(),  # the same, for invoke()
                "max_retries": 0,  # we retry in transport (BG §1.1)
                # Use the OpenAI Responses API (``/responses``) rather than
                # ChatOpenAI's chat-completions default. An OpenAI-format proxy
                # serves both (docs/verified-apis.md §2); ``/responses`` is the
                # route the raw ``donkey.llm`` client uses and the only one the
                # local simulator serves, and this is the adapter the
                # conformance suite runs against it. Override per call
                # (``use_responses_api=False``) to use ``/chat/completions``.
                "use_responses_api": True,
            }
        )

    def chat_model(self, model: str, **kw: Any) -> ChatOpenAI:
        """Return a native ``ChatOpenAI`` pointed at the proxy (BG §1.8). A
        ``base_url``/``openai_api_base`` override must pass the https check."""
        self._allow_endpoints(kw, "base_url", "openai_api_base")
        with self._native_import():
            from langchain_openai import ChatOpenAI  # VERIFY name/path: docs/verified-apis.md §8

        kw["callbacks"] = _with_last_call_handler(kw.get("callbacks"))
        return ChatOpenAI(model=model, **{**self.connection_kwargs(), **kw})

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


def _last_call_handler() -> Any:
    """A LangChain callback handler that brings ``donkey.last_call`` back to the
    caller of ``ainvoke()`` (#850).

    ``BaseChatModel.agenerate`` sends the request from a task that
    ``asyncio.gather`` spawns with a copy of the caller's context, so the record
    the transport sets never reaches the caller. LangChain awaits a
    ``run_inline`` handler's ``on_chat_model_start`` in the caller's own context,
    before that gather, so the handler opens a
    :class:`~donkey_kit.core.lastcall.LastCallBridge` there and closes it when
    the call ends. A batch (``batch_size > 1``) sends its requests side by side,
    so it gets no bridge (hazard #2): each request's record stays in its own
    task."""
    from langchain_core.callbacks import BaseCallbackHandler

    from ..core.lastcall import LastCallBridge, open_last_call_bridge

    class _LastCallHandler(BaseCallbackHandler):
        run_inline = True

        def __init__(self) -> None:
            self._bridges: dict[UUID, LastCallBridge] = {}

        def on_chat_model_start(
            self,
            serialized: dict[str, Any],
            messages: list[list[Any]],
            *,
            run_id: UUID,
            **kwargs: Any,
        ) -> None:
            if kwargs.get("batch_size", 1) == 1:
                self._bridges[run_id] = open_last_call_bridge()

        def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
            self._close(run_id)

        def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
            self._close(run_id)

        def _close(self, run_id: UUID) -> None:
            bridge = self._bridges.pop(run_id, None)
            if bridge is not None:
                bridge.close()

    return _LastCallHandler()


def _with_last_call_handler(callbacks: Any) -> Any:
    """``callbacks`` (a list, a callback manager or ``None``) plus the
    :func:`_last_call_handler`, without mutating the caller's object."""
    handler = _last_call_handler()
    if callbacks is None:
        return [handler]
    if isinstance(callbacks, (list, tuple)):
        return [*callbacks, handler]
    manager = callbacks.copy()
    manager.add_handler(handler, inherit=False)
    return manager


def chat_model(model: str, **kw: Any) -> ChatOpenAI:
    """Module-level convenience: a native ``ChatOpenAI`` at the proxy using a
    cached default :class:`~donkey_kit.Donkey` configured from the environment.
    Equivalent to ``Donkey.from_env().langgraph.chat_model(model, **kw)``."""
    return default_adapter(LangGraphAdapter).chat_model(model, **kw)


class _TypedRefusals(AbstractContextManager[None]):
    """The context manager behind :func:`typed_refusals`.

    A class, not ``@contextmanager``: a generator-based manager leaves
    ``contextlib``'s ``__exit__`` frame, whose locals hold the framework error,
    in the typed error's traceback, and reporters that render frame locals
    (Sentry, ``pytest -l``) would print its message. This ``__exit__`` drops its
    own references before raising.
    """

    def __enter__(self) -> None:
        import openai  # noqa: F401  # lazy: only the framework path needs it

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        import openai

        from ..core.errors import classify

        if not isinstance(exc, openai.APIStatusError):
            return None
        # ``APIStatusError`` always carries the originating response; classify()
        # maps it (and reads back the sent correlation/call ids) into the typed
        # taxonomy. openai>=3 vendors its own httpx, so ``exc.response`` is
        # statically a distinct-but-duck-identical Response type; cast erases it
        # to the one classify wants. `cast(Any, …)` (not `cast("httpx.Response", …)`)
        # so this typechecks clean under BOTH majors: under openai<3
        # ``exc.response`` is already ``httpx.Response`` and a cast to it is
        # `redundant-cast` (#597).
        typed = classify(cast(Any, exc.response))
        typed.framework_error = exc
        del exc, exc_type, tb
        raise typed from None


def typed_refusals() -> AbstractContextManager[None]:
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

    The typed error is raised without a chained cause: the framework error's
    message repeats the gateway's rejection text, which for a PII block holds
    the blocked values, and a traceback or ``logger.exception()`` renders every
    chained exception. It stays reachable on ``exc.framework_error``, and no
    frame in the typed error's traceback holds it as a local variable.
    """
    return _TypedRefusals()
