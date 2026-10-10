"""The typed-refusal bridge: recover a :class:`DonkeyError` from whatever a
framework raised (BG §1.2, #724, ADR ``docs/adr/0002-typed-refusal-bridge.md``).

A refusal must reach the caller as its typed class, never as the framework's
generic connection or status error, so a host framework cannot mistake it for a
transient fault. The transport already raises typed errors inside ``send()``
(:class:`GatewayUnavailable`, :class:`ModelSubstituted`), and every HTTP SDK
re-wraps them: ``openai`` before 3 and ``anthropic`` raise ``APIConnectionError``
with the typed error on ``__cause__`` (``openai`` 3, through the httpx2 bridge,
lets it through as it is), and a gateway rejection arrives as the SDK's own
status error carrying the response. :func:`translate` undoes both, without
importing any framework:

1. It walks the exception's chain — ``__cause__``, else an unsuppressed
   ``__context__`` — and returns the first :class:`DonkeyError` it finds.
2. A link with an ``exc.response`` that has a ``status_code`` and ``headers``
   (openai, anthropic and google-genai status errors, by duck type) is
   classified with :func:`~donkey_kit.core.errors.classify`, but only when the
   SDK's own transport sent it: its request carries the header names the
   transport stamps on every send. A status error from some other HTTP call in
   the block is not a governed refusal and passes through untouched.
3. The walk only steps through links that carry an HTTP ``request`` or
   ``response`` (the shape of an HTTP SDK's error). An exception your own code
   raises while handling a refusal, chained or not, stops the walk, so the
   bridge never turns your ``HTTPException(...) from exc`` back into the
   refusal. Framework wrapper types that hold neither (Strands'
   ``ModelThrottledException``) are seen through by a per-adapter translator
   passed in ``translators`` (``AdapterSpec.refusal_translator``).
4. An exception group (an ``asyncio.TaskGroup`` or anyio failure, #950) is
   looked inside: each leaf is translated on its own. When every leaf stands for
   the same :class:`DonkeyError` class, the group stands for that typed error
   (the first leaf's, depth first). Otherwise :meth:`TypedRefusals.resolve`
   rebuilds the group with the same shape and each translatable leaf replaced
   by its typed error, so ``except* PIIDetected`` works; a group with no
   translatable leaf propagates as it is.

:class:`TypedRefusals` applies :func:`translate` on exit, as a sync or async
context manager or a decorator. ``donkey.run()`` and ``@donkey.governed`` apply
it to their block by default; ``donkey_kit.typed_refusals()`` is the standalone
form.
"""

from __future__ import annotations

import builtins
import functools
import inspect
import logging
import sys
from collections.abc import Awaitable, Callable, Iterable
from types import TracebackType
from typing import Any, ParamSpec, TypeVar, cast

from .errors import DonkeyError, ResponseLike, classify

__all__ = ["Translator", "TypedRefusals", "translate"]

_log = logging.getLogger(__name__)

#: A per-adapter translator: given one link of an exception chain, return the
#: typed error it stands for, or ``None`` to let the walk carry on.
Translator = Callable[[BaseException], "DonkeyError | None"]

_P = ParamSpec("_P")
_R = TypeVar("_R")

# The ``request.extensions`` keys the transport stamps on every request it sends
# (core/transport.py). Their presence on a response's request is the proof the
# response came back through the SDK's own transport.
_SENT_BY_TRANSPORT = ("donkey_call_id_header", "donkey_correlation_header")

# Bounds the walk; real chains are a handful of links deep.
_MAX_LINKS = 32

# ``BaseExceptionGroup`` is builtin from Python 3.11. Below it, anyio raises the
# ``exceptiongroup`` backport's class, which is recognised once something else
# has imported it; core never imports it (and the SDK does not depend on it).
_BUILTIN_GROUP: type[BaseException] | None = getattr(builtins, "BaseExceptionGroup", None)


def _group_types() -> tuple[type[BaseException], ...]:
    """The exception-group base classes in use in this process."""
    found: list[type[BaseException]] = [] if _BUILTIN_GROUP is None else [_BUILTIN_GROUP]
    backport = getattr(sys.modules.get("exceptiongroup"), "BaseExceptionGroup", None)
    if isinstance(backport, type) and backport not in found:
        found.append(backport)
    return tuple(found)


def _is_group(exc: BaseException) -> bool:
    return isinstance(exc, _group_types())


def translate(exc: BaseException, translators: Iterable[Translator] = ()) -> DonkeyError | None:
    """The typed error ``exc`` stands for, or ``None`` when it is not a refusal.

    Returns ``exc`` itself when it already is a :class:`DonkeyError`. Otherwise
    walks the chain as the module docstring describes, trying each of
    ``translators`` on every link before the built-in rules. A translator that
    raises is skipped. Never raises, and never imports a framework.

    For an exception group, returns the typed error only when every leaf stands
    for the same :class:`DonkeyError` class (the first leaf's, depth first), and
    ``None`` otherwise; :meth:`TypedRefusals.resolve` handles a mixed group.

    Args:
        exc: The exception a block raised.
        translators: Per-adapter translators for framework wrapper types.
    """
    hooks = tuple(translators)
    if _is_group(exc):
        collapsed = _collapse(_translate_leaves(exc, hooks))
        return collapsed[1] if collapsed is not None else None
    return _translate_one(exc, hooks)


def _translate_one(exc: BaseException, hooks: tuple[Translator, ...]) -> DonkeyError | None:
    """:func:`translate` for one exception that is not a group: the chain walk."""
    link: BaseException | None = exc
    seen: set[int] = set()
    while link is not None and id(link) not in seen and len(seen) < _MAX_LINKS:
        seen.add(id(link))
        if isinstance(link, DonkeyError):
            return link
        for hook in hooks:
            try:
                typed = hook(link)
            except Exception:  # noqa: BLE001 — a broken translator must not mask the error
                # A broken translator (a framework half-imported, a renamed
                # exception class) must not replace the error leaving the
                # user's block with its own; skip it and keep walking.
                _log.debug("refusal translator %r failed; skipped", hook, exc_info=True)
                continue
            if typed is not None:
                return typed
        response = _status_response(link)
        if response is not None:
            return classify(cast(ResponseLike, response)) if _sent_by_transport(response) else None
        if not _carries_http(link):
            return None
        link = link.__cause__ or (None if link.__suppress_context__ else link.__context__)
    return None


# A group's leaves in depth-first order, each with the typed error it stands for
# (``None`` for a leaf that is not a refusal, or is not an ``Exception``).
_Leaves = list[tuple[BaseException, "DonkeyError | None"]]


def _leaves(group: BaseException) -> list[BaseException]:
    """The non-group leaves of ``group``, depth first."""
    out: list[BaseException] = []
    for exc in cast(Any, group).exceptions:
        out.extend(_leaves(exc) if _is_group(exc) else (exc,))
    return out


def _translate_leaves(group: BaseException, hooks: tuple[Translator, ...]) -> _Leaves:
    """Every leaf of ``group`` with its translation. A ``KeyboardInterrupt``,
    ``SystemExit`` or cancellation leaf is never translated."""
    return [
        (leaf, _translate_one(leaf, hooks) if isinstance(leaf, Exception) else None)
        for leaf in _leaves(group)
    ]


def _collapse(leaves: _Leaves) -> tuple[BaseException, DonkeyError] | None:
    """The first ``(leaf, typed)`` pair when every leaf translates to the same
    :class:`DonkeyError` class, else ``None``."""
    first = leaves[0][1] if leaves else None
    if first is None or any(typed is None or type(typed) is not type(first) for _, typed in leaves):
        return None
    return leaves[0][0], first


def _rebuild(group: BaseException, typed: dict[int, DonkeyError]) -> BaseException:
    """``group`` with every leaf whose ``id`` is in ``typed`` replaced by its typed
    error, keeping the nesting, messages and the metadata ``BaseExceptionGroup.split``
    keeps (traceback, cause, context, notes). A subgroup with nothing to replace
    is reused as it is."""
    members = cast(Any, group).exceptions
    replaced = [
        (_rebuild(exc, typed) if _is_group(exc) else typed.get(id(exc), exc)) for exc in members
    ]
    if all(new is old for new, old in zip(replaced, members, strict=True)):
        return group
    derived = cast(BaseException, cast(Any, group).derive(replaced))
    derived.__traceback__ = group.__traceback__
    derived.__cause__ = group.__cause__
    derived.__context__ = group.__context__
    notes = getattr(group, "__notes__", None)
    if notes is not None:
        cast(Any, derived).__notes__ = list(notes)
    return derived


def _with_framework_error(typed: DonkeyError, exc: BaseException) -> DonkeyError:
    """``typed``, with ``framework_error`` set to ``exc`` when it has none yet
    and is not ``exc`` itself (a :class:`DonkeyError` raised directly)."""
    if typed is not exc and typed.framework_error is None:
        typed.framework_error = exc
    return typed


def _attr(obj: object, name: str) -> object:
    """``getattr(obj, name, None)``, also tolerating the ``RuntimeError`` httpx
    raises for a request or response that was never set."""
    try:
        return getattr(obj, name, None)
    except RuntimeError:
        return None


def _status_response(exc: BaseException) -> object | None:
    """``exc.response`` when it looks like an HTTP error response (an ``int``
    ``status_code`` of 400 or more, plus ``headers``), else ``None``."""
    response = _attr(exc, "response")
    status = _attr(response, "status_code")
    if isinstance(status, int) and status >= 400 and _attr(response, "headers") is not None:
        return response
    return None


def _sent_by_transport(response: object) -> bool:
    """Whether the SDK's transport sent the request behind ``response``."""
    extensions = _attr(_attr(response, "request"), "extensions")
    return isinstance(extensions, dict) and any(key in extensions for key in _SENT_BY_TRANSPORT)


def _carries_http(exc: BaseException) -> bool:
    """Whether ``exc`` has the shape of an HTTP SDK's error: a ``request`` or a
    ``response`` attribute. Only such links are walked through."""
    return _attr(exc, "request") is not None or _attr(exc, "response") is not None


class TypedRefusals:
    """Re-raise a refusal from the block as its typed :class:`DonkeyError`.

    A dual sync/async context manager and a decorator (for sync and async
    callables), returned by ``donkey_kit.typed_refusals()``::

        async with typed_refusals():
            await client.chat.completions.create(...)   # PIIDetected, not PermissionDeniedError

        @typed_refusals()
        def ask(question: str) -> str: ...

    An exception :func:`translate` cannot type propagates unchanged, and so does
    a :class:`DonkeyError` raised directly. A translated error is raised without
    a chained cause, because the framework error's message can repeat the
    gateway's rejection text (a PII block holds the blocked values) and a
    traceback renders every chained exception. The framework error stays on
    ``exc.framework_error``, and no frame in the typed error's traceback holds
    it as a local variable. A typed error the transport raised keeps its own
    ``__cause__`` (for :class:`GatewayUnavailable`, the httpx error).

    A refusal inside an ``asyncio.TaskGroup`` arrives in an exception group
    (#950). When every leaf is the same refusal class, the typed error is raised
    in the group's place; otherwise the group is re-raised with its refusal
    leaves typed, so ``except* PIIDetected`` catches them (see :meth:`resolve`).

    Args:
        translators: Called on exit to get the per-adapter translators. Late, so a
            framework imported after the object was built is still covered.

    Docs: https://docs.donkey-kit.dev/errors#typed-refusals-at-the-framework-boundary
    """

    __slots__ = ("_translators",)

    def __init__(self, translators: Callable[[], Iterable[Translator]] | None = None) -> None:
        self._translators = translators

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        typed = self.resolve(exc)
        # Drop this frame's references so the typed error's traceback holds no
        # frame local pointing at the framework error (see the class docstring).
        del exc, exc_type, tb
        if typed is not None:
            raise typed from typed.__cause__

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        typed = self.resolve(exc)
        del exc, exc_type, tb
        if typed is not None:
            raise typed from typed.__cause__

    def resolve(self, exc: BaseException | None) -> BaseException | None:
        """The exception to raise in place of ``exc``, or ``None`` to let it
        propagate unchanged.

        The exit step without the raise, for a caller that owns its own
        ``__exit__`` (``donkey.run()``'s scope). Only an :class:`Exception` is
        translated, never a ``KeyboardInterrupt`` or a task cancellation, and a
        :class:`DonkeyError` raised directly is left as it is. Sets
        ``framework_error`` to the framework error on a typed error that has
        none yet.

        An exception group resolves to its typed error when every leaf stands
        for the same :class:`DonkeyError` class. Otherwise, when any leaf
        translates, it resolves to a rebuilt group of the same shape whose
        translatable leaves are typed, so ``except*`` matches them; a leaf the
        bridge cannot type (a ``KeyboardInterrupt`` included) is kept as the
        same object. A group with nothing to translate resolves to ``None``.
        """
        if exc is None or isinstance(exc, DonkeyError):
            return None
        is_group = _is_group(exc)
        if not is_group and not isinstance(exc, Exception):
            return None
        hooks = tuple(self._translators()) if self._translators is not None else ()
        if is_group:
            return self._resolve_group(exc, hooks)
        typed = _translate_one(exc, hooks)
        return _with_framework_error(typed, exc) if typed is not None else None

    @staticmethod
    def _resolve_group(group: BaseException, hooks: tuple[Translator, ...]) -> BaseException | None:
        leaves = _translate_leaves(group, hooks)
        collapsed = _collapse(leaves)
        if collapsed is not None:
            return _with_framework_error(collapsed[1], collapsed[0])
        changed: dict[int, DonkeyError] = {}
        for leaf, typed in leaves:
            if typed is not None and typed is not leaf:
                changed[id(leaf)] = _with_framework_error(typed, leaf)
        return _rebuild(group, changed) if changed else None

    def __call__(self, func: Callable[_P, _R]) -> Callable[_P, _R]:
        """Decorate ``func`` so its body runs inside this bridge."""
        if inspect.iscoroutinefunction(func):
            coro_fn = cast(Callable[_P, Awaitable[Any]], func)

            @functools.wraps(func)
            async def async_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> object:
                with self:
                    return await coro_fn(*args, **kwargs)

            return cast(Callable[_P, _R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with self:
                return func(*args, **kwargs)

        return sync_wrapper
