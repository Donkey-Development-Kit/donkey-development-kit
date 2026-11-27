"""The model wallet's exhaustion refusal, typed (#315, docs/verified-apis.md §4).

A model wallet is a spend or token budget the gateway keeps per caller, keyed on JWT claims.
When it is spent the gateway refuses locally with a ``429``: the body is the ``text/plain``
``{"error":"token rate limit exceeded"}``, ``retry-after`` is the smallest exhausted window in
seconds, the only wallet header is ``x-model-wallet-selected`` and there are no ``x-token-*``
headers. The token-window refusal is the inverse: the ``x-token-*`` trio and no ``retry-after``.

:class:`WalletExhausted` types the first. It subclasses
:class:`~donkey_kit.core.errors.TokenBudgetExceeded`, so an existing
``except TokenBudgetExceeded`` handler or ``donkey.on(TokenBudgetExceeded)`` reaction still
catches it.

``errors`` cannot import this module, because the class subclasses its own. Importing this
module installs the rule in ``errors``' late-bound slot, ahead of the auth and generic
token-budget rules. The ``core`` package imports it, so every
:func:`~donkey_kit.core.errors.classify` call sees it.
"""

from __future__ import annotations

from typing import Any

from . import _wire, errors
from ._refusal_body import retry_after as read_retry_after
from .errors import DonkeyError, TokenBudgetExceeded

__all__ = ["WalletExhausted"]


class WalletExhausted(TokenBudgetExceeded):
    """A model wallet is spent: the gateway refused the call because the wallet that matched
    the caller has no budget left in its window (docs/verified-apis.md §4, #315).

    A :class:`TokenBudgetExceeded`, so existing handlers for it still catch this. Terminal and
    never retried, like every :class:`PolicyViolation`. Raised on a ``429`` that carries
    ``x-model-wallet-selected`` and no ``x-token-*`` or ``x-ratelimit-*`` headers.

    The wire cannot say *what* ran out. A wallet limit can be denominated in tokens or in
    dollars and the two refusals are byte-identical, so this class makes no claim about the
    unit, and the SDK never computes cost locally to guess it. The spend, the remaining budget
    and any warning before the refusal are not in-band either (upstream gap #1863).

    Args:
        message: The human-readable refusal text.
        retry_after: Seconds until the smallest exhausted window resets, read from the
            ``retry-after`` response header; ``None`` when the gateway sent none.
        wallet: The wallet that refused, read from ``x-model-wallet-selected``.

    Docs: https://docs.donkey-kit.dev/errors
    """

    policy = "model-wallet"
    remediation: str = (
        "The model wallet that matched this caller is spent for its current window. Wait for it "
        "to reset (see retry_after) or ask the wallet's owner to raise the limit."
    )

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        wallet: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, retry_after=retry_after, **kw)
        self.wallet = wallet


def _match_wallet_exhausted(s: errors._Shape) -> DonkeyError | None:
    """Model wallet exhausted: a ``429`` that names the matched wallet and carries neither the
    token window's ``x-token-*`` trio nor the stock rate limit's ``x-ratelimit-*`` pair."""
    headers = s.response.headers
    wallet = headers.get(_wire.WALLET_SELECTED_HEADER)
    if (
        s.status != 429
        or not wallet
        or _wire.TOKEN_LIMIT_HEADER in headers
        or _wire.REQUEST_LIMIT_HEADER in headers
    ):
        return None
    return WalletExhausted(
        "Model wallet exhausted (429).",
        retry_after=read_retry_after(s.response),
        wallet=wallet,
        **s.kw,
    )


errors._LATE_BOUND_MATCHERS.append(_match_wallet_exhausted)  # noqa: SLF001
