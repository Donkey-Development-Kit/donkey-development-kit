"""The run-scoped correlation state (BG §1.1, #195, #196).

Two contextvars and the ids derived from them: the run correlation id bound by
``donkey.run(...)`` (shared by every request in the block, the client-gateway
join key), the per-run cost-tag overrides, and the fresh per-request call id.
Contextvar-bound, so a run's id and overrides reach calls on framework-spawned
``asyncio`` tasks, which copy the current context, with no threading through
framework state.

Moved out of :mod:`donkey_kit.core.telemetry` (#728): the transport's header
stamping needs this state, not the span machinery. ``core.telemetry`` re-exports
every name, so ``donkey_kit.core.telemetry.run_scope`` and the rest keep working.
"""

from __future__ import annotations

import uuid
import warnings
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from .cost import CostTags

if TYPE_CHECKING:
    from .refusals import TypedRefusals

__all__ = [
    "RunScope",
    "current_correlation_id",
    "current_cost_tags",
    "new_call_id",
    "new_correlation_id",
    "request_correlation_id",
    "run_context",
    "run_scope",
]

_correlation_id: ContextVar[str | None] = ContextVar("donkey_correlation_id", default=None)
# Per-run cost-tag overrides bound by ``donkey.run(team=..., ...)`` (#196). Like
# the correlation id it is contextvar-bound, so a run's overrides reach every
# model call in the block — including calls on framework-spawned asyncio tasks,
# which copy the current context — with no threading through framework state.
_cost_tags: ContextVar[CostTags | None] = ContextVar("donkey_cost_tags", default=None)


def new_correlation_id() -> str:
    """A fresh random run/correlation id (32 hex characters)."""
    return uuid.uuid4().hex


def new_call_id() -> str:
    """A fresh per-request **call id** (BG §1.1, #195).

    Unlike the run/correlation id — which is contextvar-bound and shared across
    every request in a ``donkey.run()`` block — this is
    generated anew for each logical request, so one call can be pinpointed within
    a run. It is client-generated, so it exists even when a request fails before
    any response (a transport error carries no gateway ``x-request-id``)."""
    return uuid.uuid4().hex


def current_correlation_id() -> str | None:
    """The correlation id bound by the enclosing run scope, or ``None`` outside one."""
    return _correlation_id.get()


def current_cost_tags() -> CostTags | None:
    """The cost-tag overrides bound by the enclosing ``donkey.run(...)`` block,
    or ``None`` outside one (#196). The transport and span recorder merge these
    over the configured tags, per field, so a run-scope dimension wins for its
    block and the rest fall back to config."""
    return _cost_tags.get()


class RunScope:
    """A **dual sync/async** context manager that sets the run correlation id
    for the governed calls made in its block (BG §1.1, #195).

    It binds the id to the :data:`_correlation_id` contextvar.

    This is what ``donkey.run(id=...)`` returns, so the same object works under
    both ``with donkey.run(...)`` and ``async with donkey.run(...)`` — binding a
    contextvar needs no ``await``, so both entry paths share one implementation.
    The bound id reaches every model call made inside the block (including calls
    on framework-spawned ``asyncio`` tasks, which copy the current context at
    creation), so a run id set here propagates without threading it through any
    framework state.

    Nested scopes rebind and restore via the contextvar token, so an inner run
    id shadows an outer one for its block and the outer id is restored on exit.
    Enter and exit happen in the same task/context for both protocols, so the
    ``reset(token)`` is always valid.

    Cost-attribution overrides (#196) layer on as additional bound state:
    ``donkey.run(team=..., project=..., env=..., enduser_id=...)`` binds a
    :class:`~donkey_kit.core.cost.CostTags` for the block, merged over the
    configured tags per field. They ride the same enter/exit token discipline as
    the correlation id, so a nested run's overrides shadow and restore cleanly,
    and the correlation binding is unaffected when no cost fields are given.

    With ``refusals`` (the typed-refusal bridge, #724), an exception leaving the
    block is first unbound and then re-raised as its typed
    :class:`~donkey_kit.core.errors.DonkeyError` when it stands for one, exactly
    as :class:`~donkey_kit.core.refusals.TypedRefusals` does on its own.
    """

    __slots__ = ("_run_id", "_cost", "_token", "_cost_token", "_refusals")

    def __init__(
        self,
        run_id: str | None = None,
        cost: CostTags | None = None,
        refusals: TypedRefusals | None = None,
    ) -> None:
        self._run_id = run_id
        self._refusals = refusals
        # Store only a non-empty override, so a plain ``donkey.run(id=...)`` binds
        # nothing on the cost contextvar and leaves any outer run's tags in place.
        self._cost = cost if (cost is not None and not cost.is_empty) else None
        self._token: Any = None
        self._cost_token: Any = None

    def _bind(self) -> str:
        rid = self._run_id or new_correlation_id()
        self._token = _correlation_id.set(rid)
        if self._cost is not None:
            self._cost_token = _cost_tags.set(self._cost)
        return rid

    def _unbind(self) -> None:
        if self._cost_token is not None:
            _cost_tags.reset(self._cost_token)
            self._cost_token = None
        if self._token is not None:
            _correlation_id.reset(self._token)
            self._token = None

    def __enter__(self) -> str:
        return self._bind()

    def __exit__(self, *exc: Any) -> None:
        self._unbind()
        typed = self._refusals.resolve(exc[1]) if self._refusals is not None else None
        # Drop the frame's reference to the framework error before raising, as
        # TypedRefusals.__exit__ does.
        del exc
        if typed is not None:
            raise typed from typed.__cause__

    async def __aenter__(self) -> str:
        return self._bind()

    async def __aexit__(self, *exc: Any) -> None:
        self._unbind()
        typed = self._refusals.resolve(exc[1]) if self._refusals is not None else None
        del exc
        if typed is not None:
            raise typed from typed.__cause__


def run_scope(
    run_id: str | None = None,
    cost: CostTags | None = None,
    refusals: TypedRefusals | None = None,
) -> RunScope:
    """Build a :class:`RunScope` — the dual sync/async run correlation binding
    behind ``donkey.run(id=...)`` (BG §1.1, #195), optionally carrying per-run
    cost-tag overrides (#196) and the typed-refusal bridge applied on exit
    (#724)."""
    return RunScope(run_id, cost, refusals)


def run_context(run_id: str | None = None) -> RunScope:
    """Deprecated: use :func:`run_scope`, or ``donkey.run(id=...)``.

    Binds a correlation ID for the block, exactly as :func:`run_scope` does,
    and emits a :class:`DeprecationWarning` (#720)."""
    warnings.warn(
        "donkey_kit.core.run_context() is deprecated; use donkey.run(id=...) "
        "or donkey_kit.core.telemetry.run_scope() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return run_scope(run_id)


def request_correlation_id() -> str:
    """The bound run's correlation ID, or a fresh one that is deliberately *not*
    bound (#803).

    Only a ``donkey.run()`` block binds a correlation ID; a
    call outside one is its own run. Binding on first use would pin the very
    first request's ID to the ambient context for its whole lifetime — the rest
    of a blocking process, or the rest of a long-lived ``asyncio.run(main())``
    (a queue consumer, a bot), including every task spawned after it — so
    unrelated calls would all report the same run. Grouping is opt-in.
    """
    return _correlation_id.get() or new_correlation_id()
