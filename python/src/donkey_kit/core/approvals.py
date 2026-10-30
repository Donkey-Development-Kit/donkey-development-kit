"""Human approval for a ``@donkey.tool`` call: one vocabulary (BG §2.3, #211).

``@donkey.tool(approval="required", risk="financial")`` gates a tool behind a
human decision. Calling the tool does not run it. It records a pending
:class:`Approval` (the tool name, its bound arguments, the risk and the run's
correlation id), emits it as a span event, and raises :class:`ApprovalRequired`.
``donkey.approvals.resolve(id, approved_by=...)`` records who approved and runs
the call; ``reject(...)`` records who refused and drops it. A pending approval
expires after its timeout, and resolving it then raises
:class:`ApprovalTimedOut`, so nothing waits on a human forever.

Each ``Donkey`` owns its own :class:`Approvals` (ADR 0018, #1423). A gated call
records its approval on the client whose ``donkey.run()`` or ``donkey.as_user()``
block runs it; outside one, on the client the tool was declared on
(``@donkey.tool`` read from an instance, or the client whose discovery built the
``ToolSet``); with neither, it raises :class:`~donkey_kit.core.errors.ConfigError`.

The approval is client-side and advisory. Code that calls the function without
the decorator bypasses it, and the gateway never learns that an approval was
needed or who gave it. The gateway's own step-up is separate: its policy asks the
user, and ``core/step_up`` types the answer (#214). An approval never overrides a
gateway refusal either: the resumed call goes through the governed transport
like any other, and the gateway judges each model call it makes afresh, as it
judges a refusal handler's re-send (BG §2.1).

There is no queue and no approval UI (build plan, "Do not build"). Pending
approvals live in memory, so a restart forgets them; a framework checkpointer is
the durable form. ``gate(natives=...)`` takes framework hooks that pause the call
with the framework's own primitive instead: LangGraph's ``interrupt()`` (#212),
the OpenAI Agents SDK's ``needs_approval`` (#213), Strands' interrupt (#1234) and
ADK's tool confirmation (#1235). Each adapter's ``pending_approvals`` /
``approve`` / ``reject`` speak :class:`Approval`, with the framework's object on
``native`` (#1422). A rejection differs: LangGraph raises :class:`ApprovalRejected`
from the resumed tool, the others hand the model the rejection message as the
tool's result. Core never imports a framework: the hooks are passed in from the
top-level ``donkey`` module.

A resumed call runs as its requester, with the user and attribution bound where it
was requested, and a resolver bound to another user is refused (#1416, ``identity``).
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

from .approval_types import (
    Approval,
    ApprovalError,
    ApprovalNotFound,
    ApprovalRejected,
    ApprovalRequired,
    ApprovalTimedOut,
)
from .correlation import current_approvals_client, request_correlation_id, run_scope
from .errors import AuthError, ConfigError
from .identity import Requester, capture_requester, check_requester, requester_scope
from .reactions import Duration, _seconds

__all__ = [
    "APPROVAL_TIMEOUT",
    "Approval",
    "ApprovalError",
    "ApprovalMode",
    "ApprovalNotFound",
    "ApprovalRejected",
    "ApprovalRequired",
    "ApprovalTimedOut",
    "Approvals",
    "NativePause",
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
_EVENT_RESUME_REFUSED = "donkey.approval.resume_refused"


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


# The requesters of the latest framework pauses, by the framework's id (a pause
# may never resume). Per process, not per client: an adapter's
# ``pending_approvals()`` records a pause with no client to hand (ADR 0018).
_LOCK = threading.Lock()
_NATIVE_REQUESTERS: OrderedDict[str, Requester] = OrderedDict()
_NATIVE_KEPT = 1024


class Approvals:
    """``donkey.approvals``: one client's pending tool approvals (BG §2.3).

    Each ``Donkey`` owns one (ADR 0018), so a client resolves only the approvals
    its own calls recorded. It is thread-safe. ``Approvals()`` built by hand is an
    empty registry that no gated call records on.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, _Pending] = {}

    def pending(self) -> tuple[Approval, ...]:
        """The approvals still waiting for a decision, oldest first. Expired ones
        are dropped, each recorded as a ``donkey.approval.timed_out`` span event."""
        self._sweep()
        with self._lock:
            return tuple(p.approval for p in self._pending.values())

    def get(self, approval_id: str) -> Approval:
        """The pending approval with this id.

        Raises:
            ApprovalNotFound: No approval with this id is pending on this client.
        """
        with self._lock:
            return self._find(approval_id).approval

    # The tool's own return value, whatever the user's function returns.
    def resolve(self, approval_id: str, *, approved_by: str) -> Any:  # noqa: ANN401
        """Approve the call, record ``approved_by``, and run the tool.

        The tool runs as its requester, with the ``donkey.as_user(...)`` user and
        attribution bound where it was called (#1416), under the approval's
        correlation id and with its arguments. This returns what the tool
        returns: for an ``async`` tool, an awaitable. The approval is consumed:
        resolving it twice raises :class:`ApprovalNotFound`. A refusal from any
        model call the tool makes propagates as usual.

        Raises:
            AuthError: This code runs inside ``donkey.as_user(...)`` for a
                different user than the requester; the approval stays pending.
            ApprovalTimedOut: The approval expired before this call.
            ApprovalNotFound: No approval with this id is pending on this client.
            ValueError: ``approved_by`` is empty.
        """
        who = _identity(approved_by, "approved_by")
        with self._lock:
            found = self._find(approval_id)
        check_requester(found.requester, what=_what(found.approval))
        pending = self._take(approval_id)
        record_decision(pending.approval, approved=True, by=who)
        return _resume(pending)

    def reject(self, approval_id: str, *, rejected_by: str) -> Approval:
        """Refuse the call and record ``rejected_by``. The tool is not run.

        Returns the rejected :class:`Approval`, so the caller can tell the agent.

        Raises:
            ApprovalTimedOut: The approval expired before this call.
            ApprovalNotFound: No approval with this id is pending on this client.
            ValueError: ``rejected_by`` is empty.
        """
        who = _identity(rejected_by, "rejected_by")
        pending = self._take(approval_id)
        record_decision(pending.approval, approved=False, by=who)
        return pending.approval

    def _add(self, pending: _Pending) -> None:
        self._sweep()
        with self._lock:
            self._pending[pending.approval.id] = pending

    def _find(self, approval_id: str) -> _Pending:
        try:
            return self._pending[approval_id]
        except KeyError:
            raise ApprovalNotFound(
                f"no pending approval {approval_id!r} on this client: it was already "
                "resolved or rejected, it was recorded by another Donkey, or the process "
                "restarted (pending approvals are kept in memory)",
                approval_id=approval_id,
            ) from None

    def _take(self, approval_id: str) -> _Pending:
        with self._lock:
            pending = self._find(approval_id)
            del self._pending[approval_id]
        if pending.approval.expired:
            _expire(pending.approval)
            raise ApprovalTimedOut(
                f"approval {approval_id} for tool {pending.approval.tool!r} timed out",
                approval=pending.approval,
            )
        return pending

    def _sweep(self) -> None:
        with self._lock:
            expired = [p.approval for p in self._pending.values() if p.approval.expired]
            for approval in expired:
                del self._pending[approval.id]
        for approval in expired:
            _expire(approval)


def _reset_for_tests() -> None:
    """Forget every framework pause's requester (only the autouse fixture in
    ``tests/conftest.py``; pending approvals live on each client)."""
    with _LOCK:
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


def gate(
    func: _F,
    *,
    risk: str | None,
    timeout: float,
    natives: Sequence[NativePause] = (),
    declared_on: Approvals | None = None,
) -> _F:
    """Wrap ``func`` so a call requests approval instead of running it.

    The wrapper keeps ``func``'s name, docstring and signature, and is ``async``
    when ``func`` is, so a framework that introspects tools sees the original.
    It binds the call's arguments first, so a call that could never run raises
    :class:`TypeError` at once instead of waiting for an approver. Each of
    ``natives`` is tried next (see :data:`NativePause`); when none applies, the
    call is recorded as pending on the registry of the client scope running it,
    else on ``declared_on`` (ADR 0018), and :class:`ApprovalRequired` is raised.

    A call a native hook approves runs as the requester :func:`record_pending`
    kept for it when this process paused it (#1416). Only the gated tool is
    re-bound: the framework's other model calls in the resumed run keep the
    resuming code's binding. A pause from another process, or past the latest
    1024, has no recorded requester: the call logs a warning and runs as the
    resuming code's binding. A resume refused for running as another user records
    a ``donkey.approval.resume_refused`` span event, so the audit trail does not
    end on an approval whose tool never ran.

    Raises:
        ConfigError: (from the gated call) No client owns it: it ran outside any
            ``donkey.run()`` / ``donkey.as_user()`` block and ``declared_on`` is
            ``None``.
    """
    signature = inspect.signature(func)

    def ask(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Approval, Requester | None]:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = MappingProxyType(dict(bound.arguments))
        for native in natives:
            approved = native(func.__name__, arguments, risk)
            if approved is not None:
                return approved, _native_requester(approved)
        registry = _owner(func.__name__, declared_on)
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
        # gate() is this module's own seam onto the registry it records on.
        registry._add(_Pending(approval, func, args, dict(kwargs), capture_requester()))  # noqa: SLF001
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


def _owner(tool: str, declared_on: Approvals | None) -> Approvals:
    """The registry a gated call records on: the client scope's, else the
    declaring client's (ADR 0018)."""
    scoped = current_approvals_client()
    if isinstance(scoped, Approvals):
        return scoped
    if declared_on is not None:
        return declared_on
    raise ConfigError(
        f"tool {tool!r} needs human approval, but no Donkey owns this call: it runs "
        "outside any donkey.run() or donkey.as_user() block, and the tool was declared "
        "with Donkey.tool, which names no client. The tool was not run.",
        remediation=(
            "Call the tool inside donkey.run() or donkey.as_user(), so the approval is "
            "recorded on that client's donkey.approvals, or declare it on a client: "
            "@donkey.tool(approval='required') on your Donkey instance."
        ),
    )


def _native_requester(approved: Approval) -> Requester | None:
    """The requester this process recorded for a framework pause, checked against
    this context; ``None`` (with a warning) when the pause was not seen here."""
    with _LOCK:
        requester = _NATIVE_REQUESTERS.get(approved.id)
    if requester is None:
        _log.warning(
            "approval %s for tool %s resumes with no recorded requester (paused in another "
            "process, before a restart, or past the latest %d pauses): it runs as this "
            "code's own binding. Resume it inside the requester's donkey.as_user() block.",
            approved.id,
            approved.tool,
            _NATIVE_KEPT,
        )
        return None
    try:
        check_requester(requester, what=_what(approved))
    except AuthError:
        _record(approved, _EVENT_RESUME_REFUSED, {})
        _log.info("approval %s for tool %s: resume refused", approved.id, approved.tool)
        raise
    return requester


def record_pending(approval: Approval) -> None:
    """Record ``approval`` as a call a framework paused, where it was requested:
    a ``donkey.approval.pending`` span event, and the requester bound here, which
    the gate runs the approved call as (#1416). The latest 1024 are kept, in this
    process's memory, by the framework's id (ADR 0018)."""
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
