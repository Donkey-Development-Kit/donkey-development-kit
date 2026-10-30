"""The approval value object and the approval errors (BG §2.3, #211, #1423).

:class:`Approval` is one call of an approval-gated tool. The errors split by who
decided, and when:

* :class:`ApprovalRequired`: the tool was called and is waiting for a human.
* :class:`ApprovalTimedOut`: nobody decided before the approval expired.
* :class:`ApprovalNotFound`: the id names no pending approval of this client.
* :class:`ApprovalError`: an approval was refused. A human said no
  (:class:`ApprovalRejected`), or the gateway's step-up said no
  (``core.step_up.GatewayApprovalDenied``, which is also a ``PolicyViolation``).
  ``except ApprovalError`` catches both refusals and nothing else.

The module sits just above ``errors`` so that ``step_up`` can subclass
:class:`ApprovalError` (ADR 0018); ``approvals`` re-exports everything here.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import DonkeyError

__all__ = [
    "Approval",
    "ApprovalError",
    "ApprovalNotFound",
    "ApprovalRejected",
    "ApprovalRequired",
    "ApprovalTimedOut",
]


@dataclass(frozen=True)
class Approval:
    """One call of an approval-gated tool, waiting for a human (BG §2.3). A
    framework adapter's approval helpers carry the framework's own object on ``native``.

    ``arguments`` is the call's arguments bound to the tool's parameter names,
    defaults included, so the approver sees the whole call. ``correlation_id`` is
    the enclosing ``donkey.run()`` id, or a fresh one outside a run; the resumed
    call runs under it, so the gateway records join to the approval.
    ``requested_at`` and ``expires_at`` are Unix timestamps; ``expires_at`` is
    ``math.inf`` for a call a framework holds, such as a LangGraph interrupt,
    which never expires.

    ``native`` is the pause on an approval ``pending_approvals()`` returns, and what
    to resume the framework with on one ``approve()`` / ``reject()`` returns (#1422).
    ``None`` for a raise-and-resume approval. It is left out of equality and the repr.
    """

    id: str
    tool: str
    arguments: Mapping[str, Any]
    risk: str | None
    correlation_id: str
    requested_at: float
    expires_at: float
    native: Any = field(default=None, compare=False, repr=False)

    @property
    def expired(self) -> bool:
        """True once ``expires_at`` has passed: the approval can no longer be resolved."""
        return time.time() >= self.expires_at


class ApprovalRequired(DonkeyError):
    """A ``@donkey.tool(approval="required")`` tool was called, and was not run
    (BG §2.3).

    :attr:`approval` is the pending call. Get a decision out of band (Slack,
    ServiceNow, your own UI), then pass its id to
    ``donkey.approvals.resolve(...)`` or ``reject(...)`` on the client that
    recorded it. ``correlation_id`` is the approval's run id.

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


class ApprovalError(DonkeyError):
    """An approval was refused, so the call it gated did not happen (#1423).

    Two subclasses, by who refused: :class:`ApprovalRejected` (a human, through
    DDK's own approval) and ``GatewayApprovalDenied`` (the user, through the
    gateway's CIBA step-up; it is also a ``PolicyViolation``). Waiting, expiry and
    an unknown id are not refusals and are not ``ApprovalError``.
    """

    remediation = (
        "The call was refused at its approval step, so it did not run. Tell the "
        "user or the agent that the action was not authorized; do not retry it "
        "unchanged."
    )


class ApprovalRejected(ApprovalError):
    """A human rejected an approval-gated call that a framework paused natively,
    so the tool was not run (BG §2.3, #212). :attr:`approval` is the call.

    Raised where the framework resumes the call and reports a rejection by
    raising: a LangGraph tool resumed on the ``Command`` its adapter's ``reject()``
    builds. The other frameworks report the rejection to the model as the tool's
    result (#213), and ``donkey.approvals.reject()`` just drops the call.
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


class ApprovalNotFound(DonkeyError, KeyError):
    """No approval with this id is pending in this client's ``donkey.approvals``
    (#1423): it was already resolved or rejected, it was recorded by another
    ``Donkey`` or process, or the process restarted. :attr:`approval_id` is the id.

    Also a :class:`KeyError`, so an ``except KeyError`` written for the earlier
    builtin keeps working.
    """

    remediation = (
        "Decide each approval once, on the Donkey that recorded it: the client whose "
        "donkey.run() or donkey.as_user() block ran the gated call, else the one the "
        "tool was declared on. Pending approvals live in that client's memory, so "
        "after a restart call the tool again to request a fresh approval."
    )

    def __init__(
        self, message: str, *, approval_id: str | None = None, remediation: str | None = None
    ) -> None:
        super().__init__(message, remediation=remediation)
        self.approval_id = approval_id

    def __str__(self) -> str:
        # KeyError's own __str__ quotes the message as a repr.
        return Exception.__str__(self)
