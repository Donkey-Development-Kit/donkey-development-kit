"""Strands Agents adapter (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

``client_args`` is forwarded to the underlying OpenAI client, so header AND
transport injection are both available (full injection). Strands builds and
closes a fresh ``AsyncOpenAI(**client_args)`` for every request, so the
``http_client`` it gets never closes: the shared client's non-owning view on
``openai<3``, a reusable bridge onto it (core ``httpx2`` bridge) on ``openai>=3``.
Closing it leaves the client usable for the next call and the shared client open
for every other surface (#733, #728).

STREAMING (#830): the governed connection sets ``stream=False``. On an
OpenAI-format proxy routing to Gemini the gateway answers a streamed request with
one whole ``chat.completion`` instead of chunk deltas, and Strands, which streams
by default, fails on every turn. Pass ``stream=True`` to stream on a route that
sends deltas.

Retries (#734, #951): ``client_args`` sets ``max_retries=0``, so the transport
alone retries. A Strands ``Agent`` adds its own throttle retry on top (6 attempts
by default): its ``ModelRetryStrategy`` retries every ``ModelThrottledException``,
which ``OpenAIModel.stream`` raises for any 429. A 429 here is a budget or
request-rate-limit refusal, terminal by contract (BG §1.2), so ``model()``
builds an ``OpenAIModel`` subclass whose ``stream`` raises the typed
``TokenBudgetExceeded`` or ``RequestRateLimitExceeded`` (#974) in its place.
The strategy does not retry it and the event loop re-raises it unwrapped, so a
default ``Agent`` sends a rate-limit refusal once (docs/verified-apis.md §8). A
throttle the SDK's transport did not send passes through, and Strands still
retries it. An ``OpenAIModel`` you build yourself from ``connection_kwargs()``
gets none of this: build its ``Agent`` with ``retry_strategy=None``.

APPROVALS (BG §2.3, #1234): an approval-gated tool
(``@donkey.tool(approval="required")``, wrapped in Strands' ``tool()``) pauses
the agent with Strands' own interrupt once the agent has an
:class:`ApprovalHook`. ``Agent(hooks=[ApprovalHook()])`` raises the interrupt
from ``BeforeToolCallEvent`` and records the pending approval; the run stops with
``stop_reason == "interrupt"``. :func:`pending_approvals` reads the paused calls
off the result as ``Approval`` objects, and the agent resumes on the ``native``
responses of :func:`approve` / :func:`reject`. An approved call runs once, under the approval's
correlation id. A rejected call is not run: Strands hands the model the
rejection as an error tool result, and nothing raises ``ApprovalRejected``.
Without the hook the gate raises ``ApprovalRequired`` inside the tool, which
Strands turns into an error tool result, so nothing pauses.

TOOL REFUSALS (BG §1.2, #1237): Strands turns a tool's exception into an error
tool result for the model, a governed refusal included. A
:class:`GovernedToolHook` on the agent re-raises the refusal instead, from
``AfterToolCallEvent``, so it stops the agent typed. An ``ApprovalHook`` is a
``GovernedToolHook``. Neither factory sees the ``Agent``, so the caller adds
the hook to ``Agent(hooks=[...])``.

Class names / kwargs UNVERIFIED — docs/verified-apis.md §8.
"""

from __future__ import annotations

import contextvars
import dataclasses
import functools
import hashlib
import json
import math
import threading
import time
import weakref
from collections.abc import AsyncGenerator, Mapping, Sequence
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from ..core.approvals import Approval, record_decision, record_pending
from ..core.correlation import current_correlation_id
from ..core.errors import RequestRateLimitExceeded, TokenBudgetExceeded
from ..core.masking import masked
from ..core.refusals import TypedRefusals, translate
from ..core.toolspec import tool_spec
from . import AdapterCapabilities
from ._base import Adapter, default_adapter

if TYPE_CHECKING:
    from strands.models.openai import OpenAIModel

    from ..core.errors import DonkeyError

__all__ = [
    "APPROVAL_INTERRUPT",
    "ApprovalHook",
    "GovernedToolHook",
    "StrandsAdapter",
    "approve",
    "model",
    "pending_approvals",
    "reject",
]


class StrandsAdapter(Adapter):
    """Governed Strands Agents objects, reached as ``donkey.strands``.

    Each factory returns the framework's own native object, pointed at the governed
    LLM proxy with the SDK's headers and transport: ``model(model)`` builds an
    ``OpenAIModel``. ``connection_kwargs()`` returns the same settings for building
    it yourself.

    Supported at ``connection_kwargs()`` only (`BG §1.8`): that accessor is the
    supported surface, and the factories are conveniences over it.

    Raises:
        ImportError: ``donkey.strands`` was read without the ``strands`` extra
            installed; the message carries the install command.
        ConfigError: The LLM-proxy settings are missing or incomplete.

    Docs: https://docs.donkey-kit.dev/frameworks/strands
    """

    # Async-only; the governed connection sets stream=False (#830, #726).
    factories = MappingProxyType(
        {
            "model": AdapterCapabilities(
                transport="shared",
                sync=False,
                streaming=False,
                typed_refusals=True,
                observes_last_call=True,
            ),
        }
    )

    def connection_kwargs(self) -> dict[str, Any]:
        """Governed kwargs for an ``OpenAIModel(model_id=…, **kwargs)`` you build
        yourself. Strands forwards ``client_args`` to the underlying OpenAI
        client, so header AND transport injection are both available. ``stream``
        is ``False`` (see the module docstring, #830)."""
        conn = self._connection()
        return masked(
            {
                "client_args": {
                    **conn,  # base_url, api_key, default_headers
                    "http_client": self._openai_kwarg_http_client(),
                    "max_retries": 0,  # we retry in transport (BG §1.1)
                },
                "stream": False,
            }
        )

    def model(self, model: str, **kw: Any) -> OpenAIModel:
        """Return a native ``OpenAIModel`` at the proxy. A ``base_url`` in a
        ``client_args`` override must pass the https check. Pass ``stream=True``
        to stream (#830).

        The object is an ``OpenAIModel`` subclass that raises a rate-limit 429
        as the typed ``TokenBudgetExceeded`` or ``RequestRateLimitExceeded``, so
        a Strands ``Agent`` does not retry it (#951, #974, see the module
        docstring)."""
        client_args = kw.get("client_args")
        if isinstance(client_args, Mapping):
            self._allow_endpoints(client_args, "base_url")
        with self._native_import():
            from strands.models.openai import (
                OpenAIModel,  # VERIFY name/path: docs/verified-apis.md §8
            )

        governed = _governed_model_class(OpenAIModel)
        return governed(model_id=model, **{**self.connection_kwargs(), **kw})


@functools.cache
def _governed_model_class(base: Any) -> type[OpenAIModel]:
    """The subclass of Strands' ``OpenAIModel`` (``base``) that
    :meth:`StrandsAdapter.model` builds, defined on first use so ``strands`` is
    imported lazily (§1.1).

    Only ``stream`` is overridden, through the public ``Model.stream`` interface.
    Strands' event loop calls it for every model request, and its default
    ``ModelRetryStrategy.is_retryable`` retries only ``ModelThrottledException``
    (docs/verified-apis.md §8). A rate-limit 429 leaves as the typed refusal,
    and any other error, a throttle included, leaves unchanged."""

    class GovernedOpenAIModel(base):
        """Strands' ``OpenAIModel``, with a rate-limit 429 raised as the typed
        refusal instead of a retryable throttle (#951, #974)."""

        async def stream(self, *args: Any, **kwargs: Any) -> AsyncGenerator[Any, None]:
            from strands.types.exceptions import ModelThrottledException

            try:
                async for event in super().stream(*args, **kwargs):
                    yield event
                return
            except ModelThrottledException as exc:
                typed = _rate_limit_refusal(exc)
                if typed is None:
                    raise
            # Raised outside the handler, as the typed-refusal bridge does: no
            # implicit context, and the throttle stays on ``framework_error``.
            raise typed from typed.__cause__

    return GovernedOpenAIModel


def _rate_limit_refusal(
    exc: BaseException,
) -> TokenBudgetExceeded | RequestRateLimitExceeded | None:
    """The typed rate-limit refusal behind a Strands throttle, or ``None`` when
    ``exc`` is not a governed token-budget or request-rate-limit refusal."""
    typed = TypedRefusals(lambda: (refusal_translator,)).resolve(exc)
    if isinstance(typed, (TokenBudgetExceeded, RequestRateLimitExceeded)):
        return typed
    return None


def model(model: str, **kw: Any) -> OpenAIModel:
    """Module-level convenience: a native ``OpenAIModel`` at the proxy using a
    cached default env-configured Donkey. Equivalent to
    ``Donkey.from_env().strands.model(model, **kw)``."""
    return default_adapter(StrandsAdapter).model(model, **kw)


def refusal_translator(exc: BaseException) -> DonkeyError | None:
    """See through Strands' exception wrappers for the typed-refusal bridge (#724).

    Strands re-raises the HTTP SDK's error inside its own types, which carry no
    ``request`` or ``response``: ``ModelThrottledException`` and
    ``ContextWindowOverflowException`` from its OpenAI model, and
    ``EventLoopException`` from the event loop, each ``from`` the original. This
    hands the wrapped error back to :func:`~donkey_kit.core.refusals.translate`;
    the classification itself stays in core. Registered as the adapter's
    ``AdapterSpec.refusal_translator`` and only consulted once ``strands`` is
    imported.
    """
    from strands.types import exceptions

    wrappers = (
        exceptions.ModelThrottledException,
        exceptions.ContextWindowOverflowException,
        exceptions.EventLoopException,
    )
    if isinstance(exc, wrappers) and exc.__cause__ is not None:
        return translate(exc.__cause__, (refusal_translator,))
    return None


#: The ``name`` of the Strands interrupt an :class:`ApprovalHook` raises, so a
#: caller reading ``result.interrupts`` can tell a DDK approval from other pauses.
APPROVAL_INTERRUPT = "donkey.approval"

# The call the hook approved, for the gate inside the tool to pick up. Set in
# BeforeToolCallEvent, spent by the gate, and cleared in AfterToolCallEvent, on
# the task that runs that one tool call. Strands runs a sync tool with
# asyncio.to_thread, which copies the context, so the worker thread sees it too.
_APPROVED: contextvars.ContextVar[Approval | None] = contextvars.ContextVar(
    "donkey_strands_approved", default=None
)


class GovernedToolHook:
    """A Strands ``HookProvider`` that stops the agent on a governed refusal
    inside a tool (BG §1.2, #1237).

    Pass it to the agent: ``Agent(model=..., tools=[...],
    hooks=[GovernedToolHook()])``. A tool that hits a governance refusal (a
    ``PIIDetected`` from a model call it makes, say) raises it from
    ``AfterToolCallEvent``, before Strands can retry the tool or send its error
    text to the model. ``donkey.run()`` / ``@donkey.governed`` unwrap Strands'
    event-loop exception to recover the typed error. Ordinary tool failures
    remain error tool results.

    The adapter cannot add it for you: ``donkey.strands.model()`` builds the
    model and ``ToolSet.strands()`` the tools, and neither sees the ``Agent``.
    An :class:`ApprovalHook` is a ``GovernedToolHook``, so an agent that has one
    needs no other.
    """

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        """Register the callback: Strands' ``HookProvider`` protocol."""
        from strands.hooks import AfterToolCallEvent

        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _after_tool(self, event: Any) -> None:
        # The call is over: the approval an ApprovalHook granted for it ends here.
        _APPROVED.set(None)
        exception = getattr(event, "exception", None)
        if isinstance(exception, Exception):
            typed = translate(exception, (refusal_translator,))
            if typed is not None:
                # Strands preserves the tool exception on the after-event. It
                # catches a hook failure once and fires this hook again; the
                # second raise escapes the executor without a retry (#1237).
                del exception, event
                raise typed from typed.__cause__


class ApprovalHook(GovernedToolHook):
    """A Strands ``HookProvider`` that pauses approval-gated tool calls with
    Strands' own interrupt (BG §2.3, #1234).

    Pass it to the agent: ``Agent(model=..., tools=[tool(issue_refund)],
    hooks=[ApprovalHook()])``. Before a ``@donkey.tool(approval="required")``
    call runs, the hook raises the :data:`APPROVAL_INTERRUPT` interrupt and
    records the pending approval; its ``reason`` is a dict with the
    ``approval_id``, ``tool``, ``arguments``, ``risk``, ``correlation_id`` and
    ``requested_at``. Resume the agent with ``agent([approve(a, by="m.keller").native
    for a in pending_approvals(result)])``: the hook records the decision once, and an
    approved call runs under the approval's correlation id. A rejected call is
    cancelled, so Strands hands the model the rejection message as an error tool
    result.

    It is a :class:`GovernedToolHook`, so it also re-raises a governed tool
    refusal from ``AfterToolCallEvent`` before Strands can retry the tool or
    send its error text to the model (BG §1.2, #1237).

    The approval id is derived from Strands' tool-use id, the tool and its
    arguments. Strands holds the pause, so ``approval_timeout`` does not apply,
    and an approval resumed by a fresh hook (a restored session) is recorded
    with the resume's time and correlation id.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested: dict[str, Approval] = {}
        # Per agent: the approvals whose decision is recorded in this
        # invocation, so a re-fired BeforeToolCallEvent (a hook's retry) does
        # not record it twice.
        self._decided: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()

    def register_hooks(self, registry: Any, **kwargs: Any) -> None:
        """Register the callbacks: Strands' ``HookProvider`` protocol."""
        from strands.hooks import AfterInvocationEvent, BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        super().register_hooks(registry, **kwargs)
        registry.add_callback(AfterInvocationEvent, self._after_invocation)

    def _before_tool(self, event: Any) -> None:
        from strands.interrupt import InterruptException

        selected = event.selected_tool
        spec = tool_spec(selected) if selected is not None else None
        if spec is None or spec.approval != "required":
            return
        try:
            bound = spec.signature.bind(**dict(event.tool_use.get("input") or {}))
        except TypeError:
            return  # the gate refuses a call that could never run, unpaused
        bound.apply_defaults()
        arguments = MappingProxyType(dict(bound.arguments))
        sent = json.dumps(event.tool_use.get("input") or {}, sort_keys=True, default=repr)
        approval = self._approval(
            str(event.tool_use["toolUseId"]), spec.name, sent, arguments, spec.risk
        )
        try:
            response = event.interrupt(APPROVAL_INTERRUPT, reason=_reason(approval))
        except InterruptException:
            with self._lock:
                first = approval.id not in self._requested
                self._requested[approval.id] = approval
            if first:
                record_pending(approval)
            raise
        approved, by, message = _decision(response)
        with self._lock:
            self._requested.pop(approval.id, None)
            decided = self._decided.setdefault(event.agent, set())
            record = approval.id not in decided
            decided.add(approval.id)
        if record:
            record_decision(approval, approved=approved, by=by)
        if approved:
            _APPROVED.set(approval)
        else:
            # The rejecter's name stays out of the model's context, as on the
            # OpenAI Agents SDK (#213); the decision span event records it.
            event.cancel_tool = (
                message or f"A human rejected this call of {approval.tool!r}, so it was not run."
            )

    def _after_invocation(self, event: Any) -> None:
        with self._lock:
            self._decided.pop(event.agent, None)

    def _approval(
        self,
        tool_use_id: str,
        tool: str,
        sent: str,
        arguments: Mapping[str, Any],
        risk: str | None,
    ) -> Approval:
        # Hash the input as the model sent it, not the bound arguments: a
        # default's repr can hold a memory address, which would change the id
        # between processes (a restored session).
        digest = hashlib.sha256(repr((tool_use_id, tool, sent)).encode())
        approval_id = digest.hexdigest()[:32]
        with self._lock:
            known = self._requested.get(approval_id)
        if known is not None:
            return known
        return Approval(
            id=approval_id,
            tool=tool,
            arguments=arguments,
            risk=risk,
            correlation_id=current_correlation_id() or approval_id,
            requested_at=time.time(),
            expires_at=math.inf,
        )


def approved_call(tool: str) -> Approval | None:
    """The approval an :class:`ApprovalHook` granted for the ``tool`` call
    running now, or ``None``. The gate's Strands hook (BG §2.3): it lets the
    approved call run instead of raising ``ApprovalRequired``. One approval runs
    one call."""
    approval = _APPROVED.get()
    if approval is None or approval.tool != tool:
        return None
    _APPROVED.set(None)
    return approval


def pending_approvals(result: Any) -> tuple[Approval, ...]:
    """The approval-gated calls a paused Strands ``AgentResult`` waits on, in
    order (BG §2.3, #1422).

    Each is an :class:`~donkey_kit.core.approvals.Approval` read from the reason
    an :class:`ApprovalHook` raised its interrupt with, and ``native`` is
    Strands' own ``Interrupt``. Pass it to :func:`approve` or :func:`reject`.
    Strands holds the pause, so ``expires_at`` is ``math.inf``. Other interrupts
    are left out."""
    interrupts: Sequence[Any] = getattr(result, "interrupts", None) or ()
    return tuple(
        _from_reason(i) for i in interrupts if getattr(i, "name", None) == APPROVAL_INTERRUPT
    )


def approve(approval: Approval, *, by: str) -> Approval:
    """Approve a paused call from :func:`pending_approvals`, recording ``by`` as
    the approver once the agent resumes on it.

    Returns the approval with ``native`` set to the ``interruptResponse`` to
    resume the agent on: ``agent([a.native for a in decided])``.

    Raises:
        ValueError: ``by`` is empty, or ``approval`` is not one of
            :func:`pending_approvals`.
    """
    return _response(approval, {"approved": True, "by": _who(by)})


def reject(approval: Approval, *, by: str, message: str | None = None) -> Approval:
    """Reject a paused call from :func:`pending_approvals`. On resume the call
    is not run, and the model gets ``message`` (or a default that names the
    tool, never ``by``) as an error tool result.

    Returns the approval with ``native`` set to the ``interruptResponse``, as
    :func:`approve` does.

    Raises:
        ValueError: ``by`` is empty, or ``approval`` is not one of
            :func:`pending_approvals`.
    """
    decision: dict[str, Any] = {"approved": False, "by": _who(by)}
    if message is not None:
        decision["message"] = message
    return _response(approval, decision)


def _response(approval: Approval, decision: dict[str, Any]) -> Approval:
    interrupt: Any = getattr(approval, "native", None)
    if getattr(interrupt, "name", None) != APPROVAL_INTERRUPT:
        raise ValueError(
            "not a paused Strands approval; pass one of pending_approvals(result), "
            f"not {approval!r}"
        )
    response = {"interruptResponse": {"interruptId": interrupt.id, "response": decision}}
    return dataclasses.replace(approval, native=response)


def _from_reason(interrupt: Any) -> Approval:
    """The :class:`~donkey_kit.core.approvals.Approval` an :class:`ApprovalHook`
    interrupt describes, with the interrupt on ``native``."""
    reason = getattr(interrupt, "reason", None)
    if not isinstance(reason, Mapping):
        reason = {}
    approval_id = str(reason.get("approval_id") or interrupt.id)
    arguments, risk = reason.get("arguments"), reason.get("risk")
    requested_at = reason.get("requested_at")
    return Approval(
        id=approval_id,
        tool=str(reason.get("tool") or ""),
        arguments=MappingProxyType(dict(arguments) if isinstance(arguments, Mapping) else {}),
        risk=risk if isinstance(risk, str) else None,
        correlation_id=str(reason.get("correlation_id") or approval_id),
        requested_at=(
            float(requested_at)
            if isinstance(requested_at, (int, float)) and not isinstance(requested_at, bool)
            else time.time()
        ),
        expires_at=math.inf,
        native=interrupt,
    )


def _who(by: str) -> str:
    if not isinstance(by, str) or not by.strip():
        raise ValueError(f"by must name who decided, not {by!r}")
    return by.strip()


def _reason(approval: Approval) -> dict[str, Any]:
    return {
        "approval_id": approval.id,
        "tool": approval.tool,
        "arguments": dict(approval.arguments),
        "risk": approval.risk,
        "correlation_id": approval.correlation_id,
        "requested_at": approval.requested_at,
    }


def _decision(value: object) -> tuple[bool, str, str | None]:
    """``(approved, by, message)`` from an interrupt response."""
    if isinstance(value, Mapping):
        approved, by, message = value.get("approved"), value.get("by"), value.get("message")
        if (
            isinstance(approved, bool)
            and isinstance(by, str)
            and by.strip()
            and (message is None or isinstance(message, str))
        ):
            return approved, by.strip(), message
    raise ValueError(
        "resume a DDK approval with approve(interrupt, by=...) or reject(interrupt, by=...); "
        f"got a {type(value).__name__} without a bool 'approved' and a non-empty 'by'"
    )
