"""Gateway step-up outcomes: the OAuth2 OBO policy's CIBA answers, typed (#1320, BG §2.2).

With CIBA enabled (``cibaEnabled``), the OAuth2 OBO Credential Injection policy asks the user
to approve the call out of band before it exchanges their token. Until they answer, it refuses
the request. Both answers were captured live from OBO policy 1.1.3 on an A2A
``message/send`` (docs/verified-apis.md §4 records the capture):

* **Pending:** ``401`` with ``Retry-After`` (the poll interval plus 2s) and the body
  ``{"error":"authorization_pending",...}``, with no ``www-authenticate`` header. That
  becomes :class:`GatewayApprovalPending`. Without the body rule it would be an
  :class:`~donkey_kit.core.errors.AuthError` telling the caller to check a credential
  that is fine.
* **Denied:** ``403`` with ``{"error":"access_denied",...}``, which becomes
  :class:`GatewayApprovalDenied`.

Each rule needs its status and its flat-string body ``error``, so a plain 401 is still an auth
error. An approved request just succeeds. An unanswered request that passes its expiry is never
reported to the client: the policy starts a new flow and keeps answering "pending". The
initiation failure (``502``, ``ciba_initiation_failed``, also in docs/verified-apis.md §4) has
no rule here: it is an :class:`~donkey_kit.core.errors.UpstreamModelError`, like any ``5xx``.

``errors`` cannot import this module, because these classes subclass its own. Importing this
module installs the rules in ``errors``' step-up slot, ahead of the auth rule. The ``core``
package imports it, so every :func:`~donkey_kit.core.errors.classify` call sees them.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from . import errors
from ._refusal_body import retry_after, str_or_none
from .approval_types import ApprovalError
from .errors import DonkeyError, PolicyViolation

__all__ = ["GatewayApprovalDenied", "GatewayApprovalPending", "wait_for_approval"]

_T = TypeVar("_T")

#: The shortest and longest pause between two sends while the gateway waits for the user.
#: The gateway's own ``Retry-After`` was 5s to 8s live; a missing one waits the short end.
_MIN_WAIT = 1.0
_MAX_WAIT = 30.0


class GatewayApprovalPending(DonkeyError):
    """The gateway is waiting for the user to approve this call out of band (CIBA, #1320).

    Not a :class:`~donkey_kit.core.errors.PolicyViolation`: nothing has been refused yet. The
    same request succeeds once the user approves it. ``retry_after`` is the wait, in seconds,
    that the gateway asked for (``Retry-After``), or ``None`` when it sent none. Retrying
    sooner only returns this error again. The SDK retries it only where the caller asks:
    :func:`wait_for_approval` (#214).
    """

    remediation: str = (
        "The gateway asked the user to approve this call on another device (CIBA) and is "
        "waiting for their answer. Re-send the same request after retry_after seconds; it "
        "succeeds once the user approves it. Do not change or refresh the credentials: they "
        "were accepted."
    )

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class GatewayApprovalDenied(PolicyViolation, ApprovalError):
    """The user denied the out-of-band (CIBA) approval the gateway requested (#1320).

    Terminal and never retried, like every
    :class:`~donkey_kit.core.errors.PolicyViolation`. Also an
    :class:`~donkey_kit.core.approval_types.ApprovalError`, beside
    ``ApprovalRejected``: here the gateway said no, there a human did (#1423).

    The gateway caches the decision. On OBO policy 1.1.3 the cache key is the
    user (``login_hint``) rather than the conversation, so a retry, a fresh
    token or a new A2A ``contextId`` gets the same denial until that entry
    expires (up to an hour was observed).
    """

    policy = "oauth2-obo-ciba"
    remediation: str = (
        "The user denied the approval the gateway asked them for (CIBA). This is terminal and "
        "was NOT retried. Tell the user the action was not authorized; the gateway keeps "
        "answering with this denial until its cached decision expires."
    )


async def wait_for_approval(
    send: Callable[[], Awaitable[_T]],
    *,
    timeout: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> _T:
    """Await ``send()``, re-sending it while the gateway waits for the user (#214, BG §2.3).

    Each :class:`GatewayApprovalPending` makes the loop sleep what the gateway asked
    (``retry_after``, kept between 1s and 30s) and send again, until the call goes through,
    ``timeout`` seconds have passed, or the user denies it. A denial raises
    :class:`GatewayApprovalDenied` as it is. When the next pause would pass ``timeout``, the
    last pending error is raised, so the caller sees that the user had not answered yet. A
    ``timeout`` of 0 sends once, like a bare call.

    The gateway, not this loop, owns the approval and its audit trail: the user approves on
    their own device and the policy lets the same request through. The loop decides nothing.
    ``sleep`` and ``clock`` are injectable for tests.
    """
    started = clock()
    while True:
        try:
            return await send()
        except GatewayApprovalPending as pending:
            wait = min(max(pending.retry_after or _MIN_WAIT, _MIN_WAIT), _MAX_WAIT)
            if clock() - started + wait > timeout:
                raise
            await sleep(wait)


def _flat_error(s: errors._Shape) -> str | None:
    return str_or_none(s.body.get("error")) if s.body is not None else None


def _match_pending(s: errors._Shape) -> DonkeyError | None:
    """CIBA pending: 401 with a flat-string ``error == "authorization_pending"``."""
    if s.status != 401 or _flat_error(s) != "authorization_pending":
        return None
    return GatewayApprovalPending(
        "The gateway is waiting for the user to approve this call (CIBA, 401).",
        retry_after=retry_after(s.response),
        **s.kw,
    )


def _match_denied(s: errors._Shape) -> DonkeyError | None:
    """CIBA denied: 403 with a flat-string ``error == "access_denied"``."""
    if s.status != 403 or _flat_error(s) != "access_denied":
        return None
    return GatewayApprovalDenied(
        "The user denied the gateway's approval request (CIBA, 403).", **s.kw
    )


errors._STEP_UP_MATCHERS[:] = [_match_pending, _match_denied]  # noqa: SLF001
