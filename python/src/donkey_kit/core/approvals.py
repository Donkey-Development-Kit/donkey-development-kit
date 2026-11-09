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
needed or who gave it. The gateway's own step-up is separate: its policy asks the
user, and ``core/step_up`` types the answer (#214). An approval never overrides a
gateway refusal either: the resumed call goes through the governed transport
like any other, and the gateway judges each model call it makes afresh, as it
judges a refusal handler's re-send (BG §2.1).

Pending approvals live in memory; framework hooks hold durable pauses (BG §2.3),
with objects on ``Approval.native`` (#1422). Resume uses the requester's identity
(#1416). Core imports no framework: ``donkey`` supplies the hooks.
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
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Literal, TypeAlias, TypeVar

from .approval_types import (
    Approval,
    ApprovalError,
    ApprovalNotFound,
    ApprovalRejected,
    ApprovalRequired,
    ApprovalTimedOut,
    _bound_snapshots,
    _matches,
    _snapshot,
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
    "native_approval",
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


#: A framework hook: decline with ``None``, pause/reject natively, or return an
#: approved call. ``gate`` runs that call under its original correlation id.
NativePause: TypeAlias = Callable[[str, Mapping[str, Any], str | None], Approval | None]


@dataclass(frozen=True)
class _Pending:
    approval: Approval
    func: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    requester: Requester


# The requesters of the latest framework pauses, by the framework's id (a pause
# may never resume). Only the initial native pause registers origin evidence.
_LOCK = threading.Lock()
_NATIVE_REQUESTERS: OrderedDict[str, tuple[Requester, Approval]] = OrderedDict()
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
    execution_defaults: bool = True,
    injected_arguments: Callable[[str], Mapping[str, Any]] | None = None,
) -> _F:
    """Wrap ``func`` so a call requests approval instead of running it.

    The wrapper keeps ``func``'s name, docstring and signature, and is ``async``
    when ``func`` is, so a framework that introspects tools sees the original.
    ``execution_defaults=False`` is for MCP schema invokers only; the
    ``injected_arguments`` callback consumes trusted execution-only values.
    their exact identities must match the call and they are excluded from review.
    It binds the call's arguments first, so a call that could never run raises
    :class:`TypeError` at once instead of waiting for an approver. Each of
    ``natives`` is tried next (see :data:`NativePause`); when none applies, the
    call is recorded as pending on the registry of the client scope running it,
    else on ``declared_on`` (ADR 0018), and :class:`ApprovalRequired` is raised.

    A call a native hook approves runs as the requester :func:`record_pending`
    kept for it when this process paused it (#1416). Only the gated tool is
    re-bound: the framework's other model calls in the resumed run keep the
    resuming code's binding. A pause from another process, or past the latest
    1024, has no recorded requester: the call is refused with ``AuthError``
    (ADR 0026). A resume refused for running as another user records
    a ``donkey.approval.resume_refused`` span event, so the audit trail does not
    end on an approval whose tool never ran.

    Raises:
        ConfigError: (from the gated call) No client owns it: it ran outside any
            ``donkey.run()`` / ``donkey.as_user()`` block and ``declared_on`` is
            ``None``; arguments cannot be safely snapshotted or do not match
            the native approval.
        AuthError: A native approval's requester is missing or does not match
            the resuming context.
    """
    signature = inspect.signature(func)

    def ask(
        args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[Approval, Requester, inspect.BoundArguments]:
        injected = injected_arguments(func.__name__) if injected_arguments else {}
        call, reviewed = _bound_snapshots(signature, args, kwargs, injected, execution_defaults)
        arguments = MappingProxyType(_snapshot(reviewed))
        for native in natives:
            approved = native(func.__name__, arguments, risk)
            if approved is not None:
                requester = _native_requester(approved)
                if not _matches(_snapshot(dict(approved.arguments)), reviewed):
                    raise ConfigError(
                        "approved tool arguments differ from the requested call",
                        remediation="Request a fresh approval for the unchanged tool arguments.",
                    )
                return approved, requester, call
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
        registry._add(  # noqa: SLF001 -- this module owns the registry seam
            _Pending(approval, func, call.args, call.kwargs, capture_requester())
        )
        _record_pending(approval)
        raise ApprovalRequired(
            f"tool {approval.tool!r} needs human approval (id {approval.id})",
            approval=approval,
        )

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_gated(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            approval, requester, bound = ask(args, kwargs)
            with _as_requested(approval, requester):
                return await func(*bound.args, **bound.kwargs)

        return async_gated  # type: ignore[return-value]

    @functools.wraps(func)
    def gated(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        approval, requester, bound = ask(args, kwargs)
        with _as_requested(approval, requester):
            return func(*bound.args, **bound.kwargs)

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


def _native_requester(approved: Approval) -> Requester:
    """Check the retained requester and argument snapshot of a native pause."""
    with _LOCK:
        recorded = _NATIVE_REQUESTERS.get(approved.id)
    if recorded is None:
        _record(approved, _EVENT_RESUME_REFUSED, {})
        raise AuthError(
            f"{_what(approved)} has no recorded requester; the tool was not run "
            "(the pause was recorded in another process, before a restart, or was evicted)",
            remediation=(
                "Request a fresh approval in the original requester's context and resume "
                "it in the same process while its requester state is retained. Merely "
                "binding the resumer with donkey.as_user() cannot restore lost evidence."
            ),
            correlation_id=approved.correlation_id,
        )
    requester, origin = recorded
    if not _matches(_snapshot(dict(approved.arguments)), dict(origin.arguments)):
        raise ConfigError(
            "approved tool arguments differ from the recorded pause",
            remediation="Request a fresh approval for the unchanged tool arguments.",
        )
    try:
        check_requester(requester, what=_what(approved))
    except AuthError:
        _record(approved, _EVENT_RESUME_REFUSED, {})
        _log.info("approval %s for tool %s: resume refused", approved.id, approved.tool)
        raise
    return requester


def record_pending(approval: Approval) -> None:
    """Record ``approval`` as a call a framework paused, where it was requested:
    a pending span event and the requester the approved call runs as (#1416).
    The latest 1024 are kept by framework id (ADR 0018). Call only at the initial
    pause, never during inspection; repeated registration preserves the origin."""
    with _LOCK:
        if approval.id in _NATIVE_REQUESTERS:
            return
        _NATIVE_REQUESTERS[approval.id] = (
            capture_requester(),
            replace(approval, native=None),
        )
        _NATIVE_REQUESTERS.move_to_end(approval.id)
        while len(_NATIVE_REQUESTERS) > _NATIVE_KEPT:
            _NATIVE_REQUESTERS.popitem(last=False)
    _record_pending(approval)


def native_approval(
    approval_id: str, *, arguments: Mapping[str, Any] | None = None
) -> Approval | None:
    """Read detached origin evidence without registering or refreshing it (ADR 0026).

    Returns ``None`` after evidence is lost. If review arguments are supplied,
    raises ``ConfigError`` when they differ from the retained exact snapshot.
    """
    with _LOCK:
        recorded = _NATIVE_REQUESTERS.get(approval_id)
    if recorded is None:
        return None
    origin = recorded[1]
    if arguments is not None and not _matches(_snapshot(dict(arguments)), dict(origin.arguments)):
        raise ConfigError(
            "approved tool arguments differ from the recorded pause",
            remediation="Request a fresh approval for the unchanged tool arguments.",
        )
    return replace(origin)


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
