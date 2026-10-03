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

:class:`TypedRefusals` applies :func:`translate` on exit, as a sync or async
context manager or a decorator. ``donkey.run()`` and ``@donkey.governed`` apply
it to their block by default; ``donkey_kit.typed_refusals()`` is the standalone
form.
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from types import TracebackType
from typing import Any, ParamSpec, TypeVar, cast

from .errors import DonkeyError, classify

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


def translate(
    exc: BaseException, translators: Iterable[Translator] = ()
) -> DonkeyError | None:
    """The typed error ``exc`` stands for, or ``None`` when it is not a refusal.

    Returns ``exc`` itself when it already is a :class:`DonkeyError`. Otherwise
    walks the chain as the module docstring describes, trying each of
    ``translators`` on every link before the built-in rules. A translator that
    raises is skipped. Never raises, and never imports a framework.

    Args:
        exc: The exception a block raised.
        translators: Per-adapter translators for framework wrapper types.
    """
    hooks = tuple(translators)
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
            return classify(cast(Any, response)) if _sent_by_transport(response) else None
        if not _carries_http(link):
            return None
        link = link.__cause__ or (None if link.__suppress_context__ else link.__context__)
    return None


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

    def resolve(self, exc: BaseException | None) -> DonkeyError | None:
        """The typed error to raise in place of ``exc``, or ``None`` to let it
        propagate unchanged.

        The exit step without the raise, for a caller that owns its own
        ``__exit__`` (``donkey.run()``'s scope). Only an :class:`Exception` is
        translated, never a ``KeyboardInterrupt`` or a task cancellation, and a
        :class:`DonkeyError` raised directly is left as it is. Sets
        ``framework_error`` to ``exc`` on a typed error that has none yet.
        """
        if not isinstance(exc, Exception) or isinstance(exc, DonkeyError):
            return None
        typed = translate(exc, self._translators() if self._translators is not None else ())
        if typed is not None and typed.framework_error is None:
            typed.framework_error = exc
        return typed

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
