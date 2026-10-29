"""Human approval for a ``@donkey.tool`` call: one vocabulary (BG §2.3, #211).

``@donkey.tool(approval="required", risk="financial")`` gates a tool behind a
human decision. Calling the tool does not run it. It records a pending
:class:`Approval` (the tool name, its bound arguments, the risk and the run's
correlation id), emits it as a span event, and raises :class:`ApprovalRequired`.
``donkey.approvals.resolve(id, approved_by=...)`` records who approved and runs
the call; ``reject(...)`` records who refused and drops it. A pending approval
expires after its timeout, and resolving it then raises
:class:`ApprovalTimedOut`, so nothing waits on a human forever.

The approval is client-side and advisory. Code that calls the function without
the decorator bypasses it, and the gateway never learns that an approval was
needed or who gave it; gateway step-up is #214. An approval never overrides a
gateway refusal either: the resumed call goes through the governed transport
like any other, and the gateway judges each model call it makes afresh, as it
judges a refusal handler's re-send (BG §2.1).

There is no queue and no approval UI (build plan, "Do not build"). Pending
approvals live in this process's memory, so a restart forgets them; a framework
checkpointer is the durable form. ``gate(natives=...)`` takes framework hooks
that pause the call with the framework's own primitive instead: inside a
LangGraph graph, the LangGraph adapter's hook calls ``interrupt()`` (#212), and
``Command(resume=...)`` carries the decision; under the OpenAI Agents SDK the
tool's ``needs_approval`` pauses ``Runner.run`` and ``RunState.approve`` /
``RunState.reject`` carry it (#213); Strands' ``ApprovalHook`` raises its
interrupt (#1234), and ADK's plugin requests a tool confirmation (#1235). A
rejection differs between them: LangGraph raises :class:`ApprovalRejected` from
the resumed tool, while the others raise nothing and hand the model the
rejection message as the tool's result. Core never imports a framework:
the hooks are passed in from the top-level ``donkey`` module.

A resumed call runs as its requester, with the user and attribution bound where
it was requested; a resolving context bound to another user is refused (#1416,
:mod:`donkey_kit.core.identity`). A native pause keeps its requester in memory.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import logging
import math
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, TypeVar

from .correlation import request_correlation_id, run_scope
from .errors import DonkeyError
from .identity import Requester, capture_requester, check_requester, requester_scope
from .reactions import Duration, _seconds

__all__ = [
    "APPROVAL_TIMEOUT",
    "Approval",
    "ApprovalMode",
    "ApprovalRejected",
    "ApprovalRequired",
    "ApprovalTimedOut",
    "Approvals",
    "NativePause",
    "approvals",
    "gate",
    "parse_timeout",
    "record_decision",
    "record_pending",
]

_log = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

#: The values ``@donkey.tool(approval=...)`` accepts.
ApprovalMode: TypeAlias = Literal["required"]

#: How long a pending approval waits for a decision unless the tool sets
#: ``approval_timeout``.
APPROVAL_TIMEOUT: Duration = "15m"

# The span and its attributes. Tool arguments never go on a span: they are
# request content, and the decorator has no config to check the capture switch.
_SPAN = "donkey.tool.approval"
_EVENT_PENDING = "donkey.approval.pending"
_EVENT_APPROVED = "donkey.approval.approved"
_EVENT_REJECTED = "donkey.approval.rejected"
_EVENT_TIMED_OUT = "donkey.approval.timed_out"


@dataclass(frozen=True)
class Approval:
    """One call of an approval-gated tool, waiting for a human (BG §2.3).

    ``arguments`` is the call's arguments bound to the tool's parameter names,
    defaults included, so the approver sees the whole call. ``correlation_id`` is
    the enclosing ``donkey.run()`` id, or a fresh one outside a run; the resumed
    call runs under it, so the gateway records join to the approval.
    ``requested_at`` and ``expires_at`` are Unix timestamps; ``expires_at`` is
    ``math.inf`` for a call a framework holds, such as a LangGraph interrupt,
    which never expires.
    """

    id: str
    tool: str
    arguments: Mapping[str, Any]
    risk: str | None
    correlation_id: str
    requested_at: float
    expires_at: float

    @property
    def expired(self) -> bool:
        """True once ``expires_at`` has passed: the approval can no longer be resolved."""
        return time.time() >= self.expires_at


class ApprovalRequired(DonkeyError):
    """A ``@donkey.tool(approval="required")`` tool was called, and was not run
    (BG §2.3).

    :attr:`approval` is the pending call. Get a decision out of band (Slack,
    ServiceNow, your own UI), then pass its id to
    ``donkey.approvals.resolve(...)`` or ``reject(...)``. ``correlation_id`` is
    the approval's run id.

    The approval is client-side and advisory: the gateway still judges every
    model call the resumed tool makes.
    """

    remediation = (
        "This tool needs a human decision before it runs. Show err.approval "
        "(tool, arguments, risk) to an approver, then call "
        "donkey.approvals.resolve(err.approval.id, approved_by=...) to run it, or "
        "donkey.approvals.reject(err.approval.id, rejected_by=...) to drop it, "
        "before the approval times out."
    )

    def __init__(self, message: str, *, approval: Approval, remediation: str | None = None) -> None:
        super().__init__(message, remediation=remediation, correlation_id=approval.correlation_id)
        self.approval = approval

    @property
    def tool(self) -> str:
        """The gated tool's name."""
        return self.approval.tool

    @property
    def arguments(self) -> Mapping[str, Any]:
        """The call's arguments, by parameter name."""
        return self.approval.arguments

    @property
    def risk(self) -> str | None:
        """The ``risk=`` the tool declared, if any."""
        return self.approval.risk


class ApprovalRejected(DonkeyError):
    """A human rejected an approval-gated call that a framework paused natively,
    so the tool was not run (BG §2.3, #212). :attr:`approval` is the call.

    Raised where the framework resumes the call and reports a rejection by
    raising: a LangGraph tool resumed with
    ``Command(resume={"approved": False, "by": ...})`` raises it. The OpenAI
    Agents SDK does not raise: it reports the rejection to the model as the
    tool's result (``rejection_message``, #213), and the run carries on. The
    raise-and-resume form needs no error either: ``donkey.approvals.reject()``
    just drops the call.
    """

    remediation = (
        "A human rejected this tool call, so it was not run. Tell the agent, or "
        "let the framework turn the error into a tool message (LangGraph: "
        "ToolNode(handle_tool_errors=True))."
    )

    def __init__(self, message: str, *, approval: Approval, remediation: str | None = None) -> None:
        super().__init__(message, remediation=remediation, correlation_id=approval.correlation_id)
        self.approval = approval


class ApprovalTimedOut(DonkeyError):
    """A pending approval passed its timeout before anyone resolved it, so the
    call was not made (BG §2.3). :attr:`approval` is the expired call."""

    remediation = (
        "Nobody resolved this approval within its timeout, so the tool was not "
        "run. Call the tool again to request a fresh approval, or give the tool a "
        "longer approval_timeout."
    )

    def __init__(self, message: str, *, approval: Approval, remediation: str | None = None) -> None:
        super().__init__(message, remediation=remediation, correlation_id=approval.correlation_id)
        self.approval = approval


#: A framework hook ``gate`` tries before raising :class:`ApprovalRequired`. It
#: takes the tool name, the bound arguments and the risk, and returns ``None``
#: when the call is not running inside its framework. Otherwise it pauses the
#: call with the framework's own primitive (raising whatever the framework needs
#: raised), raises :class:`ApprovalRejected`, or returns the approved
#: :class:`Approval`, whose decision it has recorded; ``gate`` then runs the call
#: under the approval's correlation id.
NativePause: TypeAlias = Callable[[str, Mapping[str, Any], str | None], Approval | None]


@dataclass(frozen=True)
class _Pending:
    approval: Approval
    func: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    requester: Requester


# Process-global, in request order: an approval is raised wherever a gated tool
# is called and resolved wherever the decision arrives, often another task or
# thread, so the state is module-level like the tool registry it serves.
_LOCK = threading.Lock()
_PENDING: dict[str, _Pending] = {}
# The requesters of the latest native pauses, by id (a pause may never resume).
_NATIVE_REQUESTERS: OrderedDict[str, Requester] = OrderedDict()
_NATIVE_KEPT = 1024


class Approvals:
    """``donkey.approvals``: this process's pending tool approvals (BG §2.3).

    Every ``Donkey`` returns the same registry, as every one shares the tool
    registry. It is thread-safe.
    """

    def pending(self) -> tuple[Approval, ...]:
        """The approvals still waiting for a decision, oldest first. Expired ones
        are dropped, each recorded as a ``donkey.approval.timed_out`` span event."""
        _sweep()
        with _LOCK:
            return tuple(p.approval for p in _PENDING.values())

    def get(self, approval_id: str) -> Approval:
        """The pending approval with this id.

        Raises:
            KeyError: No approval with this id is pending in this process.
        """
        with _LOCK:
            return _find(approval_id).approval

    # The tool's own return value, whatever the user's function returns.
    def resolve(self, approval_id: str, *, approved_by: str) -> Any:  # noqa: ANN401
        """Approve the call, record ``approved_by``, and run the tool.

        The tool runs as its requester, with the ``donkey.as_user(...)`` user and
        attribution bound where it was called (#1416), under the approval's
        correlation id and with its arguments. This returns what the tool
        returns: for an ``async`` tool, an awaitable. The approval is consumed:
        resolving it twice is a :class:`KeyError`. A refusal from any model call
        the tool makes propagates as usual.

        Raises:
            AuthError: This code runs inside ``donkey.as_user(...)`` for a
                different user than the requester; the approval stays pending.
            ApprovalTimedOut: The approval expired before this call.
            KeyError: No approval with this id is pending in this process.
            ValueError: ``approved_by`` is empty.
        """
        who = _identity(approved_by, "approved_by")
        with _LOCK:
            found = _find(approval_id)
        check_requester(found.requester, what=_what(found.approval))
        pending = _take(approval_id)
        record_decision(pending.approval, approved=True, by=who)
        return _resume(pending)

    def reject(self, approval_id: str, *, rejected_by: str) -> Approval:
        """Refuse the call and record ``rejected_by``. The tool is not run.

        Returns the rejected :class:`Approval`, so the caller can tell the agent.

        Raises:
            ApprovalTimedOut: The approval expired before this call.
            KeyError: No approval with this id is pending in this process.
            ValueError: ``rejected_by`` is empty.
        """
        who = _identity(rejected_by, "rejected_by")
        pending = _take(approval_id)
        record_decision(pending.approval, approved=False, by=who)
        return pending.approval


#: The registry behind ``donkey.approvals``.
approvals = Approvals()


def _add(pending: _Pending) -> None:
    _sweep()
    with _LOCK:
        _PENDING[pending.approval.id] = pending


def _find(approval_id: str) -> _Pending:
    try:
        return _PENDING[approval_id]
    except KeyError:
        raise KeyError(
            f"no pending approval {approval_id!r} in this process: it was already "
            "resolved or rejected, or the process restarted (pending approvals "
            "are kept in memory)"
        ) from None


def _take(approval_id: str) -> _Pending:
    with _LOCK:
        pending = _find(approval_id)
        del _PENDING[approval_id]
    if pending.approval.expired:
        _expire(pending.approval)
        raise ApprovalTimedOut(
            f"approval {approval_id} for tool {pending.approval.tool!r} timed out",
            approval=pending.approval,
        )
    return pending


def _sweep() -> None:
    with _LOCK:
        expired = [p.approval for p in _PENDING.values() if p.approval.expired]
        for approval in expired:
            del _PENDING[approval.id]
    for approval in expired:
        _expire(approval)


def _reset_for_tests() -> None:
    """Forget every pending approval. Called only from the autouse fixture in
    ``tests/conftest.py``."""
    with _LOCK:
        _PENDING.clear()
        _NATIVE_REQUESTERS.clear()


def parse_timeout(value: Duration) -> float:
    """``approval_timeout`` in seconds: a number, a ``timedelta`` or a string
    such as ``"15m"``.

    Raises:
        ValueError: It does not parse, or is not positive.
    """
    try:
        return _seconds(value)
    except ValueError:
        raise ValueError(
            f"approval_timeout must be a positive duration such as '15m', not {value!r}"
        ) from None


def gate(func: _F, *, risk: str | None, timeout: float, natives: Sequence[NativePause] = ()) -> _F:
    """Wrap ``func`` so a call requests approval instead of running it.

    The wrapper keeps ``func``'s name, docstring and signature, and is ``async``
    when ``func`` is, so a framework that introspects tools sees the original.
    It binds the call's arguments first, so a call that could never run raises
    :class:`TypeError` at once instead of waiting for an approver. Each of
    ``natives`` is tried next (see :data:`NativePause`); when none applies, the
    call is recorded as pending and :class:`ApprovalRequired` is raised. A call
    a native hook approves runs as the requester :func:`record_pending` kept
    for it, when this process paused it (#1416).
    """
    signature = inspect.signature(func)

    def ask(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Approval, Requester | None]:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = MappingProxyType(dict(bound.arguments))
        for native in natives:
            approved = native(func.__name__, arguments, risk)
            if approved is not None:
                with _LOCK:
                    return approved, _NATIVE_REQUESTERS.get(approved.id)
        now = time.time()
        approval = Approval(
            id=uuid.uuid4().hex,
            tool=func.__name__,
            arguments=arguments,
            risk=risk,
            correlation_id=request_correlation_id(),
            requested_at=now,
            expires_at=now + timeout,
        )
        _add(_Pending(approval, func, args, dict(kwargs), capture_requester()))
        _record_pending(approval)
        raise ApprovalRequired(
            f"tool {approval.tool!r} needs human approval (id {approval.id})",
            approval=approval,
        )

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_gated(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            with _as_requested(*ask(args, kwargs)):
                return await func(*args, **kwargs)

        return async_gated  # type: ignore[return-value]

    @functools.wraps(func)
    def gated(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        with _as_requested(*ask(args, kwargs)):
            return func(*args, **kwargs)

    return gated  # type: ignore[return-value]


def record_pending(approval: Approval) -> None:
    """Record ``approval`` as a call a framework paused, where it was requested:
    a ``donkey.approval.pending`` span event, and the requester bound here, which
    the gate runs the approved call as (#1416). The latest 1024 are kept, in memory."""
    with _LOCK:
        _NATIVE_REQUESTERS[approval.id] = capture_requester()
        _NATIVE_REQUESTERS.move_to_end(approval.id)
        while len(_NATIVE_REQUESTERS) > _NATIVE_KEPT:
            _NATIVE_REQUESTERS.popitem(last=False)
    _record_pending(approval)


def _record_pending(approval: Approval) -> None:
    expiry = {"donkey.approval.expires_at": approval.expires_at}
    _record(approval, _EVENT_PENDING, expiry if math.isfinite(approval.expires_at) else {})


def record_decision(approval: Approval, *, approved: bool, by: str) -> None:
    """Record who approved or rejected ``approval`` as a span event and a log
    record: the audit trail of who decided which call (BG §1.7)."""
    if approved:
        _record(approval, _EVENT_APPROVED, {"donkey.approval.approved_by": by})
    else:
        _record(approval, _EVENT_REJECTED, {"donkey.approval.rejected_by": by})
    verdict = "approved" if approved else "rejected"
    _log.info("approval %s for tool %s: %s by %s", approval.id, approval.tool, verdict, by)


def _identity(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must name who decided, not {value!r}")
    return value.strip()


def _expire(approval: Approval) -> None:
    _record(approval, _EVENT_TIMED_OUT, {})
    _log.info("approval %s for tool %s timed out", approval.id, approval.tool)


def _what(approval: Approval) -> str:
    return f"approval {approval.id} for tool {approval.tool!r}"


@contextlib.contextmanager
def _as_requested(approval: Approval, requester: Requester | None) -> Iterator[None]:
    """Run an approved call as its requester, when known, under its correlation id."""
    with requester_scope(requester, what=_what(approval)), run_scope(approval.correlation_id):
        yield


def _resume(pending: _Pending) -> object:
    if inspect.iscoroutinefunction(pending.func):

        async def resumed() -> object:
            with _as_requested(pending.approval, pending.requester):
                return await pending.func(*pending.args, **pending.kwargs)

        return resumed()
    with _as_requested(pending.approval, pending.requester):
        return pending.func(*pending.args, **pending.kwargs)


def _record(approval: Approval, event: str, extra: Mapping[str, Any]) -> None:
    """One short ``donkey.tool.approval`` span, a child of whatever span is
    current, carrying ``event``. A no-op without OpenTelemetry or a provider."""
    from .telemetry import DONKEY_CORRELATION_ID, _tracer

    tracer = _tracer()
    if tracer is None:
        return
    attributes: dict[str, Any] = {
        "gen_ai.tool.name": approval.tool,
        "donkey.approval.id": approval.id,
        DONKEY_CORRELATION_ID: approval.correlation_id,
    }
    if approval.risk is not None:
        attributes["donkey.approval.risk"] = approval.risk
    span = tracer.start_span(_SPAN, attributes=attributes)
    try:
        span.add_event(event, attributes={**attributes, **extra})
    finally:
        span.end()
