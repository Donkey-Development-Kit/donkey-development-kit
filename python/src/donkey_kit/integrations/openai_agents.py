"""OpenAI Agents SDK adapter (``donkey.openai_agents``) (BG §1.8).

A deep, conformance-gated adapter (BG §1.8), promoted next to LangGraph by
Phase 1 demand (#223).

An approval-gated tool (``@donkey.tool(approval="required")``) pauses a
``Runner.run`` with the SDK's own tool approval (BG §2.3, #213): build its
``FunctionTool`` with :func:`function_tool`, which sets ``needs_approval``, so the
run stops with the call in ``result.interruptions``. :func:`pending_approvals`
records each paused call as a pending approval; :func:`approve` and
:func:`reject` record the decision and hand it to the run state
(``RunState.approve`` / ``RunState.reject``), and ``Runner.run(agent, state)``
resumes. A gateway refusal the tool's own model call gets is raised out of the
run typed, not handed to the model as a tool-result string.

The OpenAI Agents SDK (pip ``openai-agents``, import ``agents``) models a
provider as an ``OpenAIChatCompletionsModel`` wrapping an ``AsyncOpenAI`` client.
Because we construct that client ourselves, header AND transport injection are
both available (full injection) — the preferred pattern anywhere a framework
accepts a pre-built OpenAI client (BG §1.8).

Point the SDK's *model* at the proxy per-agent rather than mutating the global
default client, so one process can mix governed and ungoverned models.

Correlation IDs reach every tool for free: ``Runner.run`` runs dynamic
instructions and ``function_tool`` bodies inside the caller's context, so a run
id bound with ``donkey.run(id=…)`` is visible via ``current_correlation_id()``
there, and every model call the run makes carries it on the shared transport
(``tests/unit/test_openai_agents_correlation.py``).

Class names / kwargs: docs/verified-apis.md §8.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import math
import threading
import time
import weakref
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..core.approvals import Approval, record_decision, record_pending
from ..core.correlation import current_correlation_id
from ..core.errors import DonkeyError
from ..core.masking import masked
from ..core.refusals import translate
from ..core.toolspec import TOOL_MARKER, ToolSpec, tool_spec
from . import AdapterCapabilities, missing_framework_error
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from agents import (
        FunctionTool,
        OpenAIChatCompletionsModel,
        RunContextWrapper,
        RunResult,
        RunResultStreaming,
        RunState,
        ToolApprovalItem,
    )
    from agents.tool_context import ToolContext

__all__ = [
    "OpenAIAgentsAdapter",
    "approve",
    "approved_call",
    "function_tool",
    "model",
    "pending_approvals",
    "refusal_translator",
    "reject",
]


class OpenAIAgentsAdapter(Adapter):
    """Governed OpenAI Agents SDK objects, reached as ``donkey.openai_agents``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``model(model)`` builds an
    ``OpenAIChatCompletionsModel``. ``connection_kwargs()`` returns the same
    settings for building it yourself.

    A deep, conformance-tested adapter (`BG §1.8`, #223): its factory and
    ``connection_kwargs()`` are held to the conformance suite in CI.

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


# --- human approval (BG §2.3, #213) -------------------------------------------
# The approved call the SDK is running now, set by the invoker function_tool()
# installs and read by approved_call(), the gate's native hook.
_APPROVED: ContextVar[Approval | None] = ContextVar("donkey_agents_approved", default=None)


def function_tool(tool: Callable[..., Any], **kw: Any) -> FunctionTool:
    """A native ``FunctionTool`` for a ``@donkey.tool``, ready for
    ``agents.Agent(tools=[...])`` (BG §2.3, #213).

    Takes the same keyword arguments as ``agents.function_tool``. For an
    approval-gated tool (``@donkey.tool(approval="required")``) it sets the SDK's
    ``needs_approval=True``, so ``Runner.run`` stops before the tool runs and
    returns the call in ``result.interruptions``; see :func:`pending_approvals`.
    Once the run state approves the call, the tool runs under the approval, and
    a call the SDK has not approved still raises ``ApprovalRequired``.

    The default ``failure_error_function`` hands the model the SDK's fixed error
    text for an exception the tool raises, except a
    :class:`~donkey_kit.core.errors.DonkeyError` or a gateway refusal: that one
    leaves ``Runner.run``, and inside ``donkey.run()`` it is raised typed, so a
    refusal is never turned into a tool result. Pass ``failure_error_function``
    to choose yourself.

    Raises:
        TypeError: ``tool`` is not a ``@donkey.tool``, or ``needs_approval`` was
            passed for an approval-gated one (the decorator decides it).
        ImportError: The ``openai-agents`` extra is not installed.
    """
    spec = tool_spec(tool)
    if spec is None:
        raise TypeError(
            f"function_tool() takes a @donkey.tool, not {tool!r}; use agents.function_tool "
            "for a plain function"
        )
    gated = spec.approval is not None
    if gated and "needs_approval" in kw:
        raise TypeError(
            f"{spec.name!r} is approval-gated by @donkey.tool(approval=...), so it always "
            "needs approval: drop needs_approval="
        )
    try:
        import agents  # VERIFY name/path: docs/verified-apis.md §8
    except ModuleNotFoundError as exc:
        raise missing_framework_error("openai-agents", exc.name) from exc

    kw.setdefault("failure_error_function", _raise_governed_errors)
    if not gated:
        native: FunctionTool = agents.function_tool(tool, **kw)
        return native
    native = agents.function_tool(tool, needs_approval=True, **kw)
    # Re-class the SDK's own tool as its public FunctionTool subclass, the way the
    # SDK's FunctionTool.__copy__ rebuilds one, so __wrapped__ still reaches tool.
    native.__class__ = _gated_tool_class()
    native.on_invoke_tool = _GatedInvoker(native.on_invoke_tool, spec, tool, native)
    return native


class _GatedInvoker:
    """The ``on_invoke_tool`` of a gated :func:`function_tool`: it runs the SDK's
    own invoker under the approval of a call the run state approved.

    It honours the SDK's invoker protocol, which ``FunctionTool.__post_init__``
    and ``FunctionTool.__copy__`` read with ``getattr`` (in every supported
    release): ``__agents_bind_function_tool__`` rebinds the SDK invoker to a
    copied tool (``copy.copy``, ``agents.tool_namespace()``,
    ``dataclasses.replace``), so the copy keeps the gate and its own failure
    handling. Other attributes the SDK reads off an invoker, such as its
    sync-tool marker, come from the SDK invoker.
    """

    def __init__(
        self,
        inner: Callable[[ToolContext[Any], str], Awaitable[Any]],
        spec: ToolSpec,
        wrapped: Callable[..., Any],
        native: FunctionTool,
    ) -> None:
        self._inner = inner
        self._spec = spec
        self._native = native
        self.__wrapped__ = wrapped
        setattr(self, TOOL_MARKER, spec)  # how pending_approvals() finds the tool

    async def __call__(self, ctx: ToolContext[Any], arguments: str) -> Any:
        from agents import ToolApprovalItem

        # The execution query needs the invocation's agent as well as its name
        # (docs/verified-apis.md §8). Deferred calls may already be normalized.
        deferred = self._native.defer_loading and self._native.qualified_name == self._native.name
        invocation = ToolApprovalItem(
            agent=ctx.agent,
            raw_item=ctx.tool_call,
            tool_name=ctx.tool_name,
            tool_namespace=ctx.tool_name if deferred else ctx.tool_namespace,
            tool_lookup_key=("deferred_top_level", ctx.tool_name) if deferred else None,
        )
        approved = None
        if (
            ctx.get_approval_status(
                ctx.tool_name,
                ctx.tool_call_id,
                tool_namespace=invocation.tool_namespace,
                tool_lookup_key=invocation.tool_lookup_key,
                current_invocation=invocation,
            )
            is True
        ):
            approved = _approval(self._spec, ctx.tool_call_id, arguments)
        token = _APPROVED.set(approved)
        try:
            return await self._inner(ctx, arguments)
        finally:
            _APPROVED.reset(token)

    def __agents_bind_function_tool__(self, function_tool: FunctionTool) -> _GatedInvoker:
        bind = getattr(self._inner, "__agents_bind_function_tool__", None)
        if not callable(bind):
            return self
        inner = bind(function_tool)
        return _GatedInvoker(inner, self._spec, self.__wrapped__, function_tool)

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":  # not set yet, e.g. while copying: no recursion
            raise AttributeError(name)
        return getattr(self._inner, name)


@functools.cache
def _gated_tool_class() -> type[FunctionTool]:
    """``agents.FunctionTool`` with ``__wrapped__`` read from a gated invoker.

    The SDK's own ``__wrapped__`` only answers for its own invoker class, which
    the gate wraps. Built on first use, so the module imports without
    ``openai-agents``.
    """
    from agents import FunctionTool

    class _GatedFunctionTool(FunctionTool):
        @property  # replaces the SDK's read-only __wrapped__ descriptor
        def __wrapped__(self) -> Callable[..., Any]:
            wrapped: Callable[..., Any] | None = getattr(self.on_invoke_tool, "__wrapped__", None)
            if wrapped is None:
                raise AttributeError("FunctionTool has no wrapped Python callable")
            return wrapped

    return _GatedFunctionTool


def approved_call(tool: str, arguments: Mapping[str, Any], risk: str | None) -> Approval | None:  # noqa: ARG001
    """The gate's native hook for the Agents SDK (BG §2.3, #213).

    Returns the approval for ``tool`` when the SDK is running a call of it that
    the run state approved, through a :func:`function_tool`; the gate then runs
    the tool under that approval's correlation id. Returns ``None`` anywhere
    else, so the gate falls back to its other hooks and ``ApprovalRequired``.
    The approver was recorded by :func:`approve`.
    """
    approval = _APPROVED.get()
    if approval is None or approval.tool != tool:
        return None
    _APPROVED.set(None)  # one approval runs one call
    return approval


def pending_approvals(
    run: RunResult | RunResultStreaming | RunState[Any, Any],
) -> tuple[Approval, ...]:
    """The approval-gated calls a paused run is waiting on, each recorded as a
    pending approval (BG §2.3, #213).

    Pass the ``RunResult`` (or its ``to_state()``) of a ``Runner.run`` that
    stopped on :func:`function_tool` calls. Each call becomes an
    :class:`~donkey_kit.core.approvals.Approval` (tool, arguments with defaults,
    risk) and a ``donkey.approval.pending`` span event; other interruptions are
    left out. Call it once per pause, inside the ``donkey.run()`` that paused, so
    the event carries the run's correlation id.

    The run state holds the pause, so ``approval_timeout`` does not apply and
    ``expires_at`` is ``math.inf``. The id is derived from the SDK's call id, the
    tool and its arguments, so the pending and decision events share it across a
    ``RunState.to_string()`` / ``from_string()`` round trip.
    """
    interruptions = getattr(run, "interruptions", None)
    items = interruptions if interruptions is not None else run.get_interruptions()
    found = []
    for item in items:
        spec = _gated_spec(item)
        if spec is not None:
            approval = _approval(spec, item.call_id, item.arguments)
            record_pending(approval)
            found.append(approval)
    return tuple(found)


def approve(state: RunState[Any, Any], approval: Approval | str, *, by: str) -> Approval:
    """Approve a paused call: record ``by`` as the approver and approve it on
    ``state`` with ``RunState.approve`` (BG §2.3, #213).

    ``approval`` is one of :func:`pending_approvals` or its id. Resume with
    ``Runner.run(agent, state)``; the tool runs then, and a refusal its model
    calls get still propagates. Resume inside
    ``donkey.run(id=approval.correlation_id)`` to keep the decision and the
    resumed calls on the pause's correlation id. Approving through
    ``RunState.approve`` directly also runs the tool, but records no approver.

    A call is decided once: a second :func:`approve` or :func:`reject` of it on
    the same ``state`` raises ``KeyError`` and records nothing. Given an
    :class:`~donkey_kit.core.approvals.Approval`, the decision keeps its
    ``correlation_id`` and ``requested_at``, so it joins the pending event even
    when decided outside the ``donkey.run()`` that paused.

    The SDK keeps a decided call in ``state.get_interruptions()`` and offers no
    public way to ask whether it was decided, so DDK remembers the calls it
    decided on each ``state`` object, for as long as that object lives. It cannot
    see a decision made on another object: each ``result.to_state()`` builds a
    new state, as does ``RunState.from_string()``, so decide on the state you
    resume. Nor can it see a decision made with ``RunState.approve`` or
    ``RunState.reject`` directly.

    Raises:
        KeyError: ``state`` has no paused :func:`function_tool` call with this id,
            or the call was already decided on ``state``.
        ValueError: ``by`` is empty.
    """
    who = _identity(by)
    item, decided = _paused(state, approval)
    with _decision(state, decided):
        state.approve(item)
    record_decision(decided, approved=True, by=who)
    return decided


def reject(
    state: RunState[Any, Any], approval: Approval | str, *, by: str, message: str | None = None
) -> Approval:
    """Reject a paused call: record ``by`` as the rejecter and reject it on
    ``state`` with ``RunState.reject`` (BG §2.3, #213).

    The tool does not run. On ``Runner.run(agent, state)`` the SDK sends the
    model ``message`` as the call's result, by default a line saying a human
    rejected it (never the approver's name), and the run carries on: the SDK's
    native rejection, where LangGraph raises ``ApprovalRejected``. Returns the
    rejected approval.

    A call is decided once, and an :class:`~donkey_kit.core.approvals.Approval`
    keeps its correlation, as for :func:`approve` (with the same limits).

    Raises:
        KeyError: ``state`` has no paused :func:`function_tool` call with this id,
            or the call was already decided on ``state``.
        ValueError: ``by`` is empty.
    """
    who = _identity(by)
    item, decided = _paused(state, approval)
    text = message if message is not None else _rejected(decided)
    with _decision(state, decided):
        state.reject(item, rejection_message=text)
    record_decision(decided, approved=False, by=who)
    return decided


def refusal_translator(exc: BaseException) -> DonkeyError | None:
    """See through the Agents SDK's tool-error wrapper for the typed-refusal
    bridge (#724, #213).

    ``Runner.run`` re-raises an exception that leaves a ``function_tool`` as
    ``agents.exceptions.UserError`` ``from`` the original, with the original's
    text in its message. This hands the original back to
    :func:`~donkey_kit.core.refusals.translate`; the classification stays in
    core. Registered as the adapter's ``AdapterSpec.refusal_translator`` and only
    consulted once ``agents`` is imported.
    """
    from agents.exceptions import UserError

    if isinstance(exc, UserError) and exc.__cause__ is not None:
        from . import refusal_translators

        return translate(exc.__cause__, refusal_translators())
    return None


def _raise_governed_errors(ctx: RunContextWrapper[Any], error: Exception) -> str:
    """A ``failure_error_function`` that lets a DDK error or a gateway refusal
    leave the run, typed, and gives the model the SDK's own text for anything
    else."""
    from agents import default_tool_error_function

    from . import refusal_translators

    typed = translate(error, refusal_translators())
    if typed is not None:
        raise typed from None
    text: str = default_tool_error_function(ctx, error)
    return text


def _gated_spec(item: ToolApprovalItem) -> ToolSpec | None:
    """The ``@donkey.tool`` behind a paused call, when :func:`function_tool`
    built the agent's tool for it."""
    for tool in getattr(item.agent, "tools", ()):
        if getattr(tool, "qualified_name", None) == item.qualified_name:
            # Deferred top-level calls use a synthetic namespace equal to the
            # name, but their public qualified_name is still the bare name (§8).
            if tool.qualified_name == tool.name and bool(tool.defer_loading) != (
                item.tool_namespace == item.name
            ):
                continue
            spec = tool_spec(getattr(tool, "on_invoke_tool", None))  # type: ignore[arg-type]
            if spec is not None and spec.approval is not None:
                return spec
    return None


def _approval(spec: ToolSpec, call_id: str | None, raw: str | None) -> Approval:
    """The approval for one SDK call of ``spec``'s tool."""
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {}
    arguments = parsed if isinstance(parsed, dict) else {}
    try:
        bound = spec.signature.bind(**arguments)
        bound.apply_defaults()
        arguments = dict(bound.arguments)
    except TypeError:  # the model sent arguments the tool does not take
        pass
    # The SDK's raw argument string, not the bound arguments: a default's repr
    # can hold a memory address, which would change the id between processes.
    digest = hashlib.sha256(repr((call_id, spec.name, raw or "")).encode())
    approval_id = digest.hexdigest()[:32]
    return Approval(
        id=approval_id,
        tool=spec.name,
        arguments=MappingProxyType(arguments),
        risk=spec.risk,
        correlation_id=current_correlation_id() or approval_id,
        requested_at=time.time(),
        expires_at=math.inf,
    )


def _paused(
    state: RunState[Any, Any], approval: Approval | str
) -> tuple[ToolApprovalItem, Approval]:
    """The interruption on ``state`` for ``approval``, with the approval to
    record: the one given, or for an id the approval as of now."""
    wanted = approval if isinstance(approval, str) else approval.id
    for item in state.get_interruptions():
        spec = _gated_spec(item)
        if spec is not None:
            found = _approval(spec, item.call_id, item.arguments)
            if found.id == wanted:
                return item, found if isinstance(approval, str) else approval
    raise KeyError(
        f"no paused call for approval {wanted!r} in this run state: it was already "
        "decided, or the run did not stop on a donkey function_tool"
    )


# The approval ids decided on each live RunState, keyed by id(state) because a
# RunState is an unhashable dataclass; the weakref drops the entry with the state.
_DECIDED: dict[int, tuple[weakref.ref[Any], set[str]]] = {}
_DECIDED_LOCK = threading.RLock()


@contextlib.contextmanager
def _decision(state: object, approval: Approval) -> Iterator[None]:
    """Claim ``approval`` as decided on ``state`` for the ``with`` body: raise
    ``KeyError`` if it was already decided there, and release the claim if the
    body raises."""
    key = id(state)
    with _DECIDED_LOCK:
        entry = _DECIDED.get(key)
        if entry is None or entry[0]() is not state:  # new, or a reused id
            entry = (weakref.ref(state, functools.partial(_forget, key)), set())
            _DECIDED[key] = entry
        if approval.id in entry[1]:
            raise KeyError(
                f"approval {approval.id!r} was already decided on this run state; "
                "a call is approved or rejected once"
            )
        entry[1].add(approval.id)
    try:
        yield
    except BaseException:
        with _DECIDED_LOCK:
            entry[1].discard(approval.id)
        raise


def _forget(key: int, ref: weakref.ref[Any]) -> None:
    """Drop a collected state's entry, unless a newer state reused its id."""
    with _DECIDED_LOCK:  # re-entrant: a collection can run while this thread holds it
        entry = _DECIDED.get(key)
        if entry is not None and entry[0] is ref:
            del _DECIDED[key]


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"by must name who decided, not {value!r}")
    return value.strip()


def _rejected(approval: Approval) -> str:
    return f"A human rejected this call of {approval.tool!r}, so it was not run."
