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
* :func:`typed_refusals` — the SDK-wide typed-refusal bridge, which turns the
  proxy refusal a node raises back into the SDK's typed taxonomy (#198 AC3,
  #724). ``donkey.run()`` and ``@donkey.governed`` apply it on their own.

Correlation IDs reach every node for free (#195): LangGraph runs nodes on
``asyncio`` tasks that copy the current context, so a run id bound with
``donkey.run(id=…)`` is visible via ``current_correlation_id()`` inside each
node with nothing threaded through graph state.

All class names / kwargs are UNVERIFIED until verification discipline — see
docs/verified-apis.md §8.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ..core.masking import masked
from . import AdapterCapabilities
from . import typed_refusals as _bridge
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

    from ..core.refusals import TypedRefusals

__all__ = ["LangGraphAdapter", "chat_model", "typed_refusals"]


class LangGraphAdapter(Adapter):
    """Governed LangChain objects, reached as ``donkey.langgraph``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``chat_model(model)`` builds a
    ``ChatOpenAI``. ``connection_kwargs()`` returns the same settings for building
    it yourself.

    The one deep, conformance-tested adapter (`BG §1.8`): its factories and
    ``connection_kwargs()`` are held to the conformance suite in CI.

    ``typed_refusals()`` (shared by every adapter) re-raises a gateway refusal as
    the typed :class:`~donkey_kit.core.errors.DonkeyError` subclass.

    Raises:
        ImportError: ``donkey.langgraph`` was read without the ``langgraph`` extra
            installed; the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/langgraph
    """

    # ChatOpenAI takes both shared clients, so invoke() and ainvoke() are governed (#726).
    factories = MappingProxyType(
        {
            "chat_model": AdapterCapabilities(
                transport="shared",
                sync=True,
                streaming=True,
                typed_refusals=True,
                observes_last_call=True,
            ),
        }
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs to spread into a ``ChatOpenAI(model=…, **kwargs)`` you
        build yourself (BG §1.8). Same values the factory uses — one source of
        truth for the proxy connection. Both ``ainvoke`` and ``invoke`` send
        through the SDK's clients (``http_async_client`` / ``http_client``), which
        do not follow redirects and send credentials only to checked endpoints."""
        conn = self._connection()
        return masked(
            {
                **conn,  # base_url, api_key, default_headers
                "http_async_client": self._openai_kwarg_http_client(),  # our client, our hooks
                "http_client": self._openai_kwarg_sync_http_client(),  # the same, for invoke()
                "max_retries": 0,  # we retry in transport (BG §1.1)
                # Chat Completions (``/chat/completions``), ChatOpenAI's own
                # default: the only route every upstream behind an OpenAI-format
                # proxy serves (docs/verified-apis.md §2, per-upstream route
                # matrix, #894; #1043). Override per call
                # (``use_responses_api=True``) to use ``/responses`` on an
                # OpenAI-routed proxy.
                "use_responses_api": False,
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
            serialized: dict[str, Any],  # noqa: ARG002 - langchain callback API
            messages: list[list[Any]],  # noqa: ARG002
            *,
            run_id: UUID,
            **kwargs: Any,
        ) -> None:
            if kwargs.get("batch_size", 1) == 1:
                self._bridges[run_id] = open_last_call_bridge()

        def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ARG002
            self._close(run_id)

        def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:  # noqa: ARG002
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


def typed_refusals() -> TypedRefusals:
    """Surface a proxy refusal raised *inside a node* as the SDK's typed
    exception, not a framework-wrapped generic error (#198 AC3).

    Kept for compatibility: this is the SDK-wide typed-refusal bridge,
    :func:`donkey_kit.typed_refusals` (#724, ADR 0002), which every adapter
    exposes as ``donkey.<framework>.typed_refusals()``. ``donkey.run()`` and
    ``@donkey.governed`` already apply it, so a graph run inside one needs no
    wrapper. On its own it works per node::

        async def call_model(state: State) -> State:
            with donkey.langgraph.typed_refusals():
                reply = await model.ainvoke(state["messages"])
            return {"messages": [reply]}

    A PII block then propagates out of ``graph.ainvoke(...)`` as
    :class:`~donkey_kit.core.errors.PIIDetected`, a budget block as
    :class:`~donkey_kit.core.errors.TokenBudgetExceeded`, an unreachable gateway
    as :class:`~donkey_kit.core.errors.GatewayUnavailable` (not LangChain's
    ``APIConnectionError``), and so on, each with the correlation and call ids the
    client sent. Anything else passes through untouched. The typed error is
    raised without a chained cause, so a PII block's blocked values are not
    rendered in a traceback; the framework error stays on
    ``exc.framework_error``.
    """
    return _bridge()
