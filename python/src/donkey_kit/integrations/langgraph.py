"""LangGraph / LangChain adapter (BG §1.8).

The first deep, conformance-gated adapter (BG §1.8, #198); the OpenAI Agents
SDK joined it in #223. Header injection is FULL (``default_headers`` + our
custom async http client, so every governed header and transport hook is on the
wire) — the best-case adapter, which is why it is held to the conformance bar.

Three things hang off it, in ergonomic-lockstep with the rest of the SDK:

* :meth:`LangGraphAdapter.chat_model` / :func:`chat_model` / calling the adapter
  (``donkey.langgraph("gpt-4o")``) — three ways to get the **native**
  ``langchain_openai.ChatOpenAI`` pointed at the proxy (BG §1.8, README §2).
* :meth:`LangGraphAdapter.connection_kwargs` — the governed kwargs to spread
  into a ``ChatOpenAI`` you build yourself.
* :func:`typed_refusals` — the SDK-wide typed-refusal bridge, which turns the
  proxy refusal a node raises back into the SDK's typed taxonomy (#198 AC3,
  #724). ``donkey.run()`` and ``@donkey.governed`` apply it on their own.

An approval-gated tool (``@donkey.tool(approval="required")``) pauses the graph
with LangGraph's native ``interrupt()``, and ``Command(resume=...)`` carries the
decision into the audit trail (BG §2.3, #212). :func:`pending_approvals` reads the
paused calls off the result as ``Approval`` objects, and :func:`approve` /
:func:`reject` build the ``Command`` to resume on, as their ``native`` (#1422).

Correlation IDs reach every node for free (#195): LangGraph runs nodes on
``asyncio`` tasks that copy the current context, so a run id bound with
``donkey.run(id=…)`` is visible via ``current_correlation_id()`` inside each
node with nothing threaded through graph state.

All class names / kwargs are UNVERIFIED until verification discipline — see
docs/verified-apis.md §8.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import math
import time
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ..core.approvals import Approval, ApprovalRejected, record_decision, record_pending
from ..core.correlation import current_correlation_id
from ..core.masking import masked
from . import AdapterCapabilities, missing_framework_error
from . import typed_refusals as _bridge
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI

    from ..core.refusals import TypedRefusals

__all__ = [
    "APPROVAL_INTERRUPT",
    "LangGraphAdapter",
    "approve",
    "chat_model",
    "pending_approvals",
    "reject",
    "typed_refusals",
]


class LangGraphAdapter(Adapter):
    """Governed LangChain objects, reached as ``donkey.langgraph``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``chat_model(model)`` builds a
    ``ChatOpenAI``. ``connection_kwargs()`` returns the same settings for building
    it yourself.

    A deep, conformance-tested adapter (`BG §1.8`): its factories and
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


#: The ``type`` of the value a gated tool passes to ``interrupt()``, so a caller
#: reading ``result["__interrupt__"]`` can tell a DDK approval from other pauses.
APPROVAL_INTERRUPT = "donkey.approval"


def approval_interrupt(
    tool: str, arguments: Mapping[str, Any], risk: str | None
) -> Approval | None:
    """Pause a ``@donkey.tool(approval="required")`` call with LangGraph's own
    ``interrupt()`` when it runs inside a graph (BG §2.3, #212).

    The gate calls this before it falls back to raising ``ApprovalRequired``. It
    returns ``None`` outside a graph, or in a graph run without a checkpointer
    thread, which LangGraph cannot resume. Inside one, the first pass records the
    pending approval and lets ``interrupt()`` pause the graph; the interrupt's
    value is a dict with ``type`` :data:`APPROVAL_INTERRUPT`, the
    ``approval_id``, ``tool``, ``arguments``, ``risk``, ``correlation_id`` and
    ``requested_at``. Resuming on :func:`approve`'s ``Command`` (or
    ``Command(resume={"approved": True, "by": "m.keller"})``) re-runs the node,
    records the approver and returns the approval, and the gate runs the tool. A
    rejection records it and raises
    :class:`~donkey_kit.core.approvals.ApprovalRejected`.

    The approval id is derived from the thread, the tool and its arguments, so
    the pending and the decision events share it across a process restart. The
    checkpointer holds the pause, so ``approval_timeout`` does not apply.

    Raises:
        ApprovalRejected: The resume value rejected the call.
        ValueError: The resume value is not ``{"approved": bool, "by": str}``.
        ImportError: ``langgraph`` is not installed.
    """
    # Loaded by name, so mypy sees them as Any with or without LangGraph
    # installed (#763). A static import would need langgraph in the mypy skip
    # list, which also erases the real LangGraph types the langgraph-demo job
    # checks examples/langgraph against.
    try:
        lg_config = importlib.import_module("langgraph.config")
        lg_errors = importlib.import_module("langgraph.errors")
        lg_types = importlib.import_module("langgraph.types")
    except ModuleNotFoundError as exc:
        raise missing_framework_error("langgraph", exc.name) from exc

    try:
        configurable = lg_config.get_config().get("configurable") or {}
    except RuntimeError:  # not inside a runnable: no graph to pause
        return None
    thread = configurable.get("thread_id")
    if thread is None:
        return None
    digest = hashlib.sha256(repr((str(thread), tool, sorted(arguments.items()))).encode())
    approval_id = digest.hexdigest()[:32]
    approval = Approval(
        id=approval_id,
        tool=tool,
        arguments=arguments,
        risk=risk,
        correlation_id=current_correlation_id() or approval_id,
        requested_at=time.time(),
        expires_at=math.inf,
    )
    try:
        decision = lg_types.interrupt(
            {
                "type": APPROVAL_INTERRUPT,
                "approval_id": approval.id,
                "tool": tool,
                "arguments": dict(arguments),
                "risk": risk,
                "correlation_id": approval.correlation_id,
                "requested_at": approval.requested_at,
            }
        )
    except lg_errors.GraphInterrupt:
        record_pending(approval)
        raise
    approved, by = _decision(decision)
    record_decision(approval, approved=approved, by=by)
    if not approved:
        raise ApprovalRejected(
            f"tool {tool!r} was rejected by {by} (approval {approval.id})", approval=approval
        )
    return approval


def pending_approvals(paused: Any) -> tuple[Approval, ...]:
    """The approval-gated calls a paused graph waits on, in order (BG §2.3, #1422).

    Pass what ``graph.invoke()`` / ``ainvoke()`` returned on the pause (its
    ``"__interrupt__"`` key), or ``graph.get_state(config)`` (its
    ``interrupts``). Each is an :class:`~donkey_kit.core.approvals.Approval` read
    from the interrupt's value, and ``native`` is LangGraph's own ``Interrupt``;
    other interrupts are left out. The pending approval was recorded when the
    graph paused, so this records nothing. The checkpointer holds the pause, so
    ``expires_at`` is ``math.inf``. Pass each to :func:`approve` or :func:`reject`.
    """
    if isinstance(paused, Mapping):
        interrupts = paused.get("__interrupt__") or ()
    else:
        interrupts = getattr(paused, "interrupts", None) or ()
    return tuple(_from_interrupt(i) for i in interrupts if _approval_value(i) is not None)


def approve(approval: Approval, *, by: str) -> Approval:
    """Approve a paused call from :func:`pending_approvals`, recording ``by`` as
    the approver once the graph resumes.

    Returns the approval with ``native`` set to the ``Command`` to resume the graph
    on: ``graph.invoke(approve(a, by="m.keller").native, config)``. The resume
    value is keyed by the interrupt's id, so for several pauses at once merge the
    ``native.resume`` mappings into one ``Command(resume={...})``.

    Raises:
        ValueError: ``by`` is empty, or ``approval`` is not one of
            :func:`pending_approvals`.
        ImportError: ``langgraph`` is not installed.
    """
    return _resume(approval, approved=True, by=by)


def reject(approval: Approval, *, by: str) -> Approval:
    """Reject a paused call from :func:`pending_approvals`, recording ``by`` as
    the rejecter once the graph resumes. The resumed tool raises
    :class:`~donkey_kit.core.approvals.ApprovalRejected` and is not run.

    Returns the approval with ``native`` set to the ``Command`` to resume on, as
    :func:`approve` does.

    Raises:
        ValueError: ``by`` is empty, or ``approval`` is not one of
            :func:`pending_approvals`.
        ImportError: ``langgraph`` is not installed.
    """
    return _resume(approval, approved=False, by=by)


def _approval_value(interrupt: object) -> Mapping[str, Any] | None:
    """The value of a DDK approval interrupt, or ``None`` for any other object."""
    value = getattr(interrupt, "value", None)
    if isinstance(value, Mapping) and value.get("type") == APPROVAL_INTERRUPT:
        return value
    return None


def _from_interrupt(interrupt: Any) -> Approval:
    value = _approval_value(interrupt) or {}
    approval_id = str(value.get("approval_id") or "")
    arguments, risk = value.get("arguments"), value.get("risk")
    requested_at = value.get("requested_at")
    return Approval(
        id=approval_id,
        tool=str(value.get("tool") or ""),
        arguments=MappingProxyType(dict(arguments) if isinstance(arguments, Mapping) else {}),
        risk=risk if isinstance(risk, str) else None,
        correlation_id=str(value.get("correlation_id") or approval_id),
        requested_at=(
            float(requested_at)
            if isinstance(requested_at, (int, float)) and not isinstance(requested_at, bool)
            else time.time()
        ),
        expires_at=math.inf,
        native=interrupt,
    )


def _resume(approval: Approval, *, approved: bool, by: str) -> Approval:
    interrupt: Any = getattr(approval, "native", None)
    if _approval_value(interrupt) is None:
        raise ValueError(
            "not a paused LangGraph approval; pass one of pending_approvals(result), "
            f"not {approval!r}"
        )
    if not isinstance(by, str) or not by.strip():
        raise ValueError(f"by must name who decided, not {by!r}")
    decision = {"approved": approved, "by": by.strip()}
    # Interrupt.id from langgraph 0.6, interrupt_id before it.
    interrupt_id = getattr(interrupt, "id", None) or getattr(interrupt, "interrupt_id", None)
    try:
        lg_types = importlib.import_module("langgraph.types")
    except ModuleNotFoundError as exc:
        raise missing_framework_error("langgraph", exc.name) from exc
    command = lg_types.Command(resume={interrupt_id: decision} if interrupt_id else decision)
    return dataclasses.replace(approval, native=command)


def _decision(value: object) -> tuple[bool, str]:
    """``(approved, by)`` from a ``Command(resume=...)`` value."""
    if isinstance(value, Mapping):
        approved, by = value.get("approved"), value.get("by")
        if isinstance(approved, bool) and isinstance(by, str) and by.strip():
            return approved, by.strip()
    raise ValueError(
        "resume a DDK approval with Command(resume={'approved': True, 'by': '<approver>'}); "
        f"got a {type(value).__name__} without a bool 'approved' and a non-empty 'by'"
    )
