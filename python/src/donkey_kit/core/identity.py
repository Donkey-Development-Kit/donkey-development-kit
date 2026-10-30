"""On-behalf-of identity: run governed calls as a user (BG §2.4, #215).

``donkey.as_user(token=...)`` binds the user's access token to a
:class:`~contextvars.ContextVar` for the block. The transport reads the binding
per send and stamps the token, unchanged, on data-plane requests, so the
gateway sees the user, not the service identity::

    async with donkey.as_user(token=request.access_token):
        await agent.run(task)        # every governed model call is the user's

Sync code uses a plain ``with`` on the same object (#1436).

The SDK runs no token exchange: the gateway's OBO policy trades the user token
at the identity provider (docs/verified-apis.md §13, #1350). So the SDK makes
no control-plane request for the user and needs no connected-app credentials.

The binding is a contextvar like ``donkey.run(...)``: it reaches the
``asyncio`` tasks the block spawns, which copy the current context, and never
leaks into a concurrent task that entered no scope. Nested scopes restore the
outer user on exit.

**Wire contract.** The token rides as ``Authorization: Bearer <token>``, the
header the gateway's JWT Validation and OBO policies read (docs/verified-apis.md
§13); ``user_token_header`` overrides it.

**Checked endpoints only** (ADR 0005, #1439). The user token never comes from
the working directory's config files, so it is never sent to an endpoint read
from them unless ``DONKEY_TRUST_PROJECT_CONFIG`` opts in:
:func:`check_user_token_endpoint` runs ``DonkeyConfig.check_endpoints`` for it
when the scope is built, for the LLM proxy. A discovered ``ToolSet`` checks the
control plane its MCP gateway URLs were read from, for the token and the
gateway credential together (``tools.auth.check_gateway_endpoint``, ADR 0019).

**No authorisation here.** The SDK forwards a token; what the user may do is
decided by the gateway and the platform. Nothing in this module inspects the
token's claims or expiry, or allows or refuses a call on its strength: an
expired token comes back from the gateway as a typed error.

**A deferred call keeps its requester** (#1416). A call requested in one
context and run in another, such as an approved tool resumed from the
approver's handler, would otherwise run as whoever resumes it.
:func:`capture_requester` records the binding and attribution at request time,
and :func:`requester_scope` runs the call under them later. A resuming context
that binds a different user is refused with an
:class:`~donkey_kit.core.errors.AuthError`: the check compares bindings, not
claims, so "the same user" means the same token.

The same documented degradation as ``donkey.run(...)`` applies (BG §1.8): a
``connection_kwargs()``-only adapter that does not route through the shared
transport never sees the binding.
"""

from __future__ import annotations

import hmac
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import _verify
from .config import TOKEN_AUTH_MODES
from .correlation import _exact_attribution, current_business_group, current_cost_tags
from .errors import AuthError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .config import Capability, DonkeyConfig
    from .cost import CostTags

__all__ = [
    "USER_TOKEN_LABEL",
    "Requester",
    "UserScope",
    "capture_requester",
    "check_requester",
    "check_user_token_endpoint",
    "current_user",
    "requester_scope",
    "user_token_header",
]

#: How an endpoint-check refusal names the user token (``DonkeyConfig.check_endpoints``).
USER_TOKEN_LABEL = "the donkey.as_user() token"


def user_token_header(cfg: DonkeyConfig) -> str:
    """The request header the user token rides in: the ``user_token_header``
    override, else the placeholder default."""
    return cfg.user_token_header or _verify.USER_TOKEN_HEADER


def check_user_token_endpoint(cfg: DonkeyConfig, *, need: Capability) -> None:
    """Refuse to send the ``donkey.as_user()`` token to ``need``'s endpoint when
    ``DonkeyConfig.check_endpoints`` would refuse a credential from outside the
    project config files (ADR 0005, #1439). ``"control_plane"`` covers the MCP
    gateway URLs, which are read from that endpoint.

    Raises:
        ConfigError: The endpoint was read from ``./.donkey-kit.toml`` or its
            ``.local`` overlay and ``DONKEY_TRUST_PROJECT_CONFIG`` is not set
            (for ``base_url``, unless it is a standard Anypoint host), or it is
            not ``https://``.
    """
    cfg.check_endpoints(need=need, code_credential=USER_TOKEN_LABEL)


class _UserBinding:
    """The user a block runs as: the token forwarded on their behalf."""

    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token


_current_user: ContextVar[_UserBinding | None] = ContextVar("donkey_current_user", default=None)


def current_user() -> _UserBinding | None:
    """The user bound by the enclosing ``donkey.as_user(...)`` block, or ``None``."""
    return _current_user.get()


class UserScope:
    """The **dual sync/async** context manager ``donkey.as_user(...)`` returns (BG §2.4).

    Binding a contextvar needs no ``await``, so ``with`` and ``async with``
    share one implementation, as in ``donkey.run(...)``: blocking code binds
    the user for the sync client (#1436).

    Construction refuses a missing token with
    :class:`~donkey_kit.core.errors.AuthError`, and an LLM proxy URL the token
    may not be sent to with :class:`~donkey_kit.core.errors.ConfigError`
    (#1439), before the block runs. There is
    no fallback to the service identity and no setting that enables one
    (#216): inside the block a data-plane request carries the user token or is
    not sent. Exit restores whatever was bound before, so nested scopes shadow
    and restore like ``donkey.run(...)``.
    """

    __slots__ = ("_binding", "_reset")

    def __init__(self, cfg: DonkeyConfig, token: str) -> None:
        """
        Raises:
            AuthError: ``token`` is missing or empty.
            ConfigError: The user token would ride in the default
                ``Authorization`` header in the ``jwt`` / ``bearer`` modes,
                where the data-plane token already rides; or ``llm_proxy_url``
                was read from the working directory's config files and
                ``DONKEY_TRUST_PROJECT_CONFIG`` is not set
                (:func:`check_user_token_endpoint`).
        """
        if not isinstance(token, str) or not token.strip():
            # An AuthError, not a ConfigError: a request handler that lost the
            # user's token must stop here, not run the block as the service.
            raise AuthError(
                "donkey.as_user() was given no token: the user's token is "
                "missing, so the block can't run as the user and no request was sent "
                "as the service identity instead.",
                remediation=(
                    "Pass the signed-in user's current token. There is no "
                    "setting that runs the block as the service instead (BG §2.4)."
                ),
            )
        header = user_token_header(cfg)
        if (
            cfg.llm_proxy_auth in TOKEN_AUTH_MODES
            and header.lower() == _verify.LLM_PROXY_WALLET_JWT_HEADER.lower()
        ):
            raise ConfigError(
                f"donkey.as_user() can't put the user token in {header!r} in "
                f"{cfg.llm_proxy_auth!r} mode: the data-plane token already rides there. "
                "Set user_token_header (env DONKEY_USER_TOKEN_HEADER) to the header "
                "the gateway reads the user token from."
            )
        check_user_token_endpoint(cfg, need="llm")
        self._binding = _UserBinding(token)
        self._reset: Token[_UserBinding | None] | None = None

    def _bind(self) -> None:
        self._reset = _current_user.set(self._binding)

    def _unbind(self) -> None:
        if self._reset is not None:
            _current_user.reset(self._reset)
            self._reset = None

    def __enter__(self) -> None:
        self._bind()

    def __exit__(self, *exc: object) -> None:
        self._unbind()

    async def __aenter__(self) -> None:
        self._bind()

    async def __aexit__(self, *exc: object) -> None:
        self._unbind()


@dataclass(frozen=True)
class Requester:
    """Who asked for a deferred call: the ``donkey.as_user(...)`` binding and
    the attribution (cost tags, business group) bound when it was requested
    (#1416). Build it with :func:`capture_requester`; ``None`` fields mean
    nothing was bound, so the service identity and the configured attribution
    apply."""

    user: _UserBinding | None
    cost: CostTags | None
    business_group: str | None

    def matches_current_user(self) -> bool:
        """True when this context binds the requester's user: the same token,
        or no user when the requester had none. Stricter than
        :func:`check_requester`, which lets an unbound context pass because
        :func:`requester_scope` re-binds the requester's user; a caller that
        cannot re-bind, such as a per-tool MCP binding whose headers carry the
        requester's token (#1418), needs this one."""
        return _same_user(current_user(), self.user)


def capture_requester() -> Requester:
    """The :class:`Requester` of a call requested here, now: the current user
    binding and attribution, to run the call under later (#1416)."""
    return Requester(current_user(), current_cost_tags(), current_business_group())


def _same_user(a: _UserBinding | None, b: _UserBinding | None) -> bool:
    if a is None or b is None:
        return a is b
    return hmac.compare_digest(a.token.encode(), b.token.encode())


def check_requester(requester: Requester, *, what: str) -> None:
    """Refuse to run ``what`` (say, "approval 1f2e for tool 'refund'") for
    ``requester`` when this context binds a different user (#1416).

    No binding here passes: :func:`requester_scope` re-binds the requester's.
    The comparison is by token, as the SDK reads no claims, so the requester's
    own refreshed token counts as a different user.

    Raises:
        AuthError: This context is inside a ``donkey.as_user(...)`` block for a
            different token, or for any user when the requester had none.
    """
    bound = current_user()
    if bound is None or _same_user(bound, requester.user):
        return
    asked = "with another user's token" if requester.user is not None else "as the service"
    raise AuthError(
        f"{what} was requested {asked}, but this code runs inside donkey.as_user() "
        "with a different token, so it was not run: it would have run as someone "
        "other than its requester.",
        remediation=(
            "Resume the call outside any donkey.as_user() block: DDK runs it as its "
            "requester, with the requester's user token and attribution. Inside "
            "as_user(), only the requester's own token may resume it (BG §2.4)."
        ),
    )


@contextmanager
def requester_scope(requester: Requester | None, *, what: str) -> Iterator[None]:
    """Run the block as ``requester``: check it with :func:`check_requester`,
    then bind exactly its user and attribution, replacing whatever this context
    binds, and restore them on exit (#1416). ``None`` (no requester was
    captured) binds nothing, so this context's binding stands. A sync context
    manager that works in ``async`` code too, as binding a contextvar needs no
    ``await``.

    Raises:
        AuthError: This context binds a different user (see :func:`check_requester`).
    """
    if requester is None:
        yield
        return
    check_requester(requester, what=what)
    token = _current_user.set(requester.user)
    try:
        with _exact_attribution(requester.cost, requester.business_group):
            yield
    finally:
        _current_user.reset(token)
