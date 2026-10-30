"""Declarative refusal reaction handlers, applied at the transport (BG §2.1, #208).

``donkey.on(TokenBudgetExceeded).wait_for_reset(max_wait="45m")`` registers a
reaction once. Every governed data-plane client of that ``Donkey`` then runs it
at its ``_on_refusal`` hook (BG §1.1) whenever a final response is that refusal,
so twelve graph nodes share one PII handler instead of twelve try/excepts. The
most specific registered type wins (the refusal's MRO is walked), so
``on(PolicyViolation)`` is the catch-all.

The rule (BG §2.1, #209): a handler *reacts*, it never *decides policy*. No
verb turns a refusal into a success. Each reaction ends in one of two ways:

- the refusal propagates to the caller exactly as it would have without the
  handler, or
- a **new** governed request goes out (a new call id, the same run) and its
  result, which the gateway judges afresh, is returned instead.

There is no ``ignore()``, ``suppress()`` or ``allow()``: the gateway enforces,
and a client-side "allow" would be client-side enforcement by another name.
``max_times`` and ``max_wait`` are outer bounds on every reaction, and a breach
propagates the latest refusal. A handler that raises is logged and the refusal
propagates, so it never masks the refusal. :class:`AgentKilled` is terminal and
never handled (BG §2.6): registering it is a :class:`TypeError`, and a
catch-all skips it.

This module is sans-IO (ADR 0009). :class:`ReactionRun` decides; each client
sleeps, calls the handler and sends.
"""

from __future__ import annotations

import copy
import inspect
import json
import logging
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol, TypeAlias

import httpx

from .errors import AgentKilled, PolicyViolation

__all__ = [
    "Duration",
    "EscalationSink",
    "Invoke",
    "OnRefusal",
    "ReactionRun",
    "RefusalContext",
    "RefusalHandlers",
    "Resend",
]

_log = logging.getLogger(__name__)

#: A wait bound: seconds, a ``timedelta``, or a string such as ``"45m"``.
Duration: TypeAlias = float | timedelta | str

_DEFAULT_MAX_WAIT = "15m"
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
# Recomputed by httpx for the new body, so never copied onto a re-send.
_BODY_FRAMING = frozenset({"content-length", "transfer-encoding"})


class _QueueLike(Protocol):
    def put_nowait(self, item: PolicyViolation, /) -> None: ...


#: Where ``escalate(to=...)`` sends a refusal: a callable, or a queue.
EscalationSink: TypeAlias = Callable[[PolicyViolation], object] | _QueueLike


class RefusalContext:
    """What a :meth:`OnRefusal.call` handler is given: the refusal, a copy of
    the refused request's JSON body, and :meth:`resend`.

    The handler reacts and then returns. If it called :meth:`resend`, the
    transport sends that new governed request and returns its result. If it did
    not, the refusal propagates, so returning early never swallows it
    (BG §2.1, #209). The transport builds it; ``issued`` collects the
    request :meth:`resend` asks for."""

    def __init__(
        self, violation: PolicyViolation, request: httpx.Request, issued: list[httpx.Request]
    ) -> None:
        self._violation = violation
        self._request = request
        self._body = _json_body(request)
        self._issued = issued

    @property
    def violation(self) -> PolicyViolation:
        """The typed refusal the gateway returned."""
        return self._violation

    @property
    def body(self) -> dict[str, Any] | None:
        """A deep copy of the refused request's JSON body, or ``None`` when it
        had none. Changing it changes nothing until you pass it to :meth:`resend`."""
        return copy.deepcopy(self._body)

    def resend(self, *, json: Mapping[str, Any]) -> None:
        """Ask for a new governed request with ``json`` as its body, sent once
        the handler returns. It goes to the same endpoint through the same
        governed pipeline, so the gateway evaluates it from scratch: a masked
        prompt that still carries PII is refused again.

        Raises:
            RuntimeError: ``resend`` was already called for this refusal.
        """
        if self._issued:
            raise RuntimeError("resend() was already called for this refusal")
        self._issued.append(_rebuild(self._request, _encode(json)))


@dataclass(frozen=True)
class Resend:
    """A new governed request to send in place of the refusal, after ``delay``
    seconds. Only a reaction makes one; the client sends it through its own
    governed pipeline, never returns the refused response as a success."""

    request: httpx.Request
    delay: float = 0.0


@dataclass(frozen=True)
class Invoke:
    """User code the client must run: the handler or escalation ``fn`` with
    ``arg``. :meth:`ReactionRun.after` turns its completion into the next step."""

    fn: Callable[[Any], object]
    arg: object
    label: str
    issued: list[httpx.Request] | None = None


@dataclass(frozen=True)
class _WaitForReset:
    max_wait: float
    max_times: int


@dataclass(frozen=True)
class _Call:
    handler: Callable[[RefusalContext], object]
    max_times: int


@dataclass(frozen=True)
class _Fallback:
    model: str
    max_times: int


@dataclass(frozen=True)
class _Escalate:
    to: Callable[[PolicyViolation], object]


_Reaction: TypeAlias = _WaitForReset | _Call | _Fallback | _Escalate


class RefusalHandlers:
    """The reactions one ``Donkey`` registered, shared by its data-plane clients."""

    def __init__(self) -> None:
        self._by_type: dict[type[PolicyViolation], _Reaction] = {}

    def __bool__(self) -> bool:
        return bool(self._by_type)

    def on(self, refusal: type[PolicyViolation]) -> OnRefusal:
        """The builder that registers a reaction to ``refusal``.

        Raises:
            TypeError: ``refusal`` is not a :class:`PolicyViolation` subclass,
                or is :class:`AgentKilled`, which is terminal (BG §2.6).
        """
        if not (isinstance(refusal, type) and issubclass(refusal, PolicyViolation)):
            raise TypeError(f"on() takes a PolicyViolation subclass, not {refusal!r}")
        if issubclass(refusal, AgentKilled):
            raise TypeError(
                "AgentKilled is terminal and cannot be handled (BG §2.6): an "
                "administrator blocked this agent, and no retry can succeed."
            )
        return OnRefusal(self, refusal)

    def register(self, refusal: type[PolicyViolation], reaction: _Reaction) -> None:
        """Make ``reaction`` the handler for ``refusal``, replacing any earlier one."""
        if refusal in self._by_type:
            _log.debug("replacing the on(%s) handler", refusal.__name__)
        self._by_type[refusal] = reaction

    def match(self, violation: PolicyViolation) -> tuple[type[PolicyViolation], _Reaction] | None:
        """The registered type and reaction for ``violation``: the nearest one
        in its MRO, or ``None``. :class:`AgentKilled` never matches (BG §2.6)."""
        if isinstance(violation, AgentKilled):
            return None  # terminal, even under an on(PolicyViolation) catch-all
        for cls in type(violation).__mro__:
            reaction = self._by_type.get(cls)
            if reaction is not None:
                return cls, reaction
        return None

    def start(self) -> ReactionRun:
        """The bookkeeping for one logical ``send()``: the bounds count from here."""
        return ReactionRun(self)


class OnRefusal:
    """Returned by ``donkey.on(refusal)``: pick exactly one reaction.

    A second registration for the same type replaces the first. Every verb
    either propagates the refusal or issues a new governed request; none
    returns a success for the refused one (BG §2.1, #209)."""

    def __init__(self, handlers: RefusalHandlers, refusal: type[PolicyViolation]) -> None:
        self._handlers = handlers
        self._refusal = refusal

    def wait_for_reset(self, *, max_wait: Duration = _DEFAULT_MAX_WAIT, max_times: int = 1) -> None:
        """Wait for the window the refusal reports, then re-send the same request.

        The wait is the refusal's own reset time (``retry_after``). A refusal
        that carries none propagates rather than retrying at zero delay, and
        so does one whose wait would take the total past ``max_wait``.

        Args:
            max_wait: The most time to spend waiting in one call, in seconds, a
                ``timedelta``, or a string such as ``"45m"``, ``"30s"``, ``"1h"``.
            max_times: The most re-sends in one call.

        Raises:
            ValueError: A bound is not positive, or ``max_wait`` does not parse.
        """
        bound = _seconds(max_wait)
        self._handlers.register(self._refusal, _WaitForReset(bound, _times(max_times)))

    def call(self, handler: Callable[[RefusalContext], object], *, max_times: int = 1) -> None:
        """Run ``handler(ctx)`` with a :class:`RefusalContext`. The handler may
        call ``ctx.resend(json=...)`` to issue a new governed request; if it
        does not, the refusal propagates. An ``async def`` handler is awaited on
        the async client; the blocking client cannot await it, so there the
        refusal propagates with a warning.

        Raises:
            TypeError: ``handler`` is not callable.
            ValueError: ``max_times`` is not positive.
        """
        if not callable(handler):
            raise TypeError(f"call() takes a callable, not {handler!r}")
        self._handlers.register(self._refusal, _Call(handler, _times(max_times)))

    def fallback(self, *, model: str, max_times: int = 1) -> None:
        """Re-send the request to ``model`` instead, as a new governed request.
        The refusal propagates when the request has no JSON body naming a
        ``model`` (a native Gemini path carries it in the URL), or already asked
        for ``model``.

        Raises:
            ValueError: ``model`` is empty, or ``max_times`` is not positive.
        """
        if not isinstance(model, str) or not model:
            raise ValueError("fallback() needs a non-empty model name")
        self._handlers.register(self._refusal, _Fallback(model, _times(max_times)))

    def escalate(self, *, to: EscalationSink) -> None:
        """Hand the refusal to ``to`` (a callable, or a queue's ``put_nowait``),
        then let it propagate. An escalation records; it never retries.

        Raises:
            TypeError: ``to`` is neither callable nor has ``put_nowait``.
        """
        put = getattr(to, "put_nowait", None)
        sink = put if callable(put) else to
        if not callable(sink):
            raise TypeError(f"escalate() takes a callable or a queue, not {to!r}")
        self._handlers.register(self._refusal, _Escalate(sink))


class ReactionRun:
    """The reactions of one logical ``send()``: matches each refusal to its
    handler, enforces ``max_times``/``max_wait`` and decides the next step."""

    def __init__(self, handlers: RefusalHandlers) -> None:
        self._handlers = handlers
        self._uses: dict[type[PolicyViolation], int] = {}
        self._waited: dict[type[PolicyViolation], float] = {}

    def react(self, violation: PolicyViolation, request: httpx.Request) -> Resend | Invoke | None:
        """The next step for ``violation``: a re-send, user code to run, or
        ``None`` to let the refusal propagate."""
        matched = self._handlers.match(violation)
        if matched is None:
            return None
        cls, reaction = matched
        name = type(violation).__name__
        if isinstance(reaction, _Escalate):
            return Invoke(reaction.to, violation, f"on({cls.__name__}).escalate")
        used = self._uses.get(cls, 0)
        if used >= reaction.max_times:
            _log.debug(
                "%s: the on(%s) handler already ran max_times=%d; the refusal propagates",
                name,
                cls.__name__,
                reaction.max_times,
            )
            return None
        self._uses[cls] = used + 1
        if isinstance(reaction, _WaitForReset):
            return self._wait(cls, reaction, violation, request)
        if isinstance(reaction, _Fallback):
            return _fallback(reaction, name, request)
        issued: list[httpx.Request] = []
        context = RefusalContext(violation, request, issued)
        return Invoke(reaction.handler, context, f"on({cls.__name__}).call", issued)

    def _wait(
        self,
        cls: type[PolicyViolation],
        reaction: _WaitForReset,
        violation: PolicyViolation,
        request: httpx.Request,
    ) -> Resend | None:
        name = type(violation).__name__
        delay = getattr(violation, "retry_after", None)
        if not isinstance(delay, (int, float)) or not math.isfinite(delay):
            _log.debug("%s carries no reset time; wait_for_reset() lets it propagate", name)
            return None
        delay = max(float(delay), 0.0)
        waited = self._waited.get(cls, 0.0) + delay
        if waited > reaction.max_wait:
            _log.debug(
                "%s resets in %.1fs, past wait_for_reset(max_wait=%.0fs); the refusal propagates",
                name,
                delay,
                reaction.max_wait,
            )
            return None
        try:
            rebuilt = _rebuild(request, None)
        except httpx.RequestNotRead:
            _log.debug("%s: the request body was streamed, so it cannot be re-sent", name)
            return None
        self._waited[cls] = waited
        _log.debug("%s: waiting %.1fs for the reset, then re-sending", name, delay)
        return Resend(rebuilt, delay)

    @staticmethod
    def after(step: Invoke) -> Resend | None:
        """The step after ``step``'s code returned: the re-send a ``call()``
        handler asked for, else ``None`` (the refusal propagates)."""
        return Resend(step.issued[0]) if step.issued else None

    @staticmethod
    def failed(step: Invoke, exc: Exception) -> None:
        """Log a handler that raised. The refusal propagates unmasked (#208)."""
        _log.warning(
            "the %s handler raised %s; the original refusal propagates",
            step.label,
            type(exc).__name__,
            exc_info=exc,
        )

    @staticmethod
    def cannot_await(step: Invoke, result: object) -> None:
        """Discard an awaitable a handler returned on the blocking client."""
        if inspect.iscoroutine(result):
            result.close()
        _log.warning(
            "the %s handler is async, which the blocking client cannot await; "
            "the refusal propagates. Use the async client, or a plain function.",
            step.label,
        )


def _fallback(reaction: _Fallback, name: str, request: httpx.Request) -> Resend | None:
    body = _json_body(request)
    if body is None or not isinstance(body.get("model"), str):
        _log.warning(
            "%s: fallback(model=%r) needs a JSON body naming a model; the refusal propagates",
            name,
            reaction.model,
        )
        return None
    if body["model"] == reaction.model:
        _log.debug("%s came from the fallback model itself; the refusal propagates", name)
        return None
    body["model"] = reaction.model
    _log.debug("%s: re-sending to the fallback model %r", name, reaction.model)
    return Resend(_rebuild(request, _encode(body)))


def _json_body(request: httpx.Request) -> dict[str, Any] | None:
    """The request's JSON object body, or ``None`` (unread, empty, not JSON)."""
    try:
        raw = request.content
    except httpx.RequestNotRead:
        return None
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _encode(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body).encode()


def _rebuild(request: httpx.Request, content: bytes | None) -> httpx.Request:
    """A new request to the same URL with the same headers and extensions
    (so the same run correlation id), carrying ``content`` or the original body.
    The client pins a fresh call id on it, as on any new logical request."""
    body = request.content if content is None else content
    headers = [(k, v) for k, v in request.headers.multi_items() if k.lower() not in _BODY_FRAMING]
    return httpx.Request(
        request.method,
        request.url,
        headers=headers,
        content=body,
        extensions=dict(request.extensions),
    )


def _times(max_times: int) -> int:
    if isinstance(max_times, bool) or not isinstance(max_times, int) or max_times < 1:
        raise ValueError(f"max_times must be a positive int, not {max_times!r}")
    return max_times


def _seconds(value: Duration) -> float:
    """``value`` in seconds: a number, a ``timedelta`` or a string of number +
    unit pairs (``"45m"``, ``"1h30m"``, ``"500ms"``).

    Raises:
        ValueError: It does not parse, or is not positive.
    """
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif isinstance(value, str):
        text = value.strip().lower()
        parts = _DURATION.findall(text)
        if not parts or "".join(n + u for n, u in parts) != text:
            raise ValueError(f"max_wait {value!r} is not a duration such as '45m' or '30s'")
        seconds = sum(float(n) * _UNIT_SECONDS[u] for n, u in parts)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
    else:
        raise ValueError(f"max_wait must be seconds, a timedelta or a string, not {value!r}")
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"max_wait must be positive, not {value!r}")
    return seconds
