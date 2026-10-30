"""The run ledger: what happened between one ``donkey.run()`` block and the
gateway (#613, epic #611).

``donkey.last_call`` keeps only the most recent call. The ledger keeps every
governed model call made in the run, the refusals the gateway returned, and the
run's token totals, until the block exits, so an agent can adapt within a run
(redact after a ``PIIDetected``, degrade near the budget reserve) and a caller
can ask what the run cost after it ends.

It rides a :class:`~contextvars.ContextVar` bound by
:class:`~donkey_kit.core.correlation.RunScope`, the same mechanism as the run's
correlation id: a framework-spawned task copies the context, so it appends to
its run's ledger, and a concurrent run never sees another's. A nested run binds
its own ledger and the outer one is restored on exit. Appends take a lock, so a
parallel fan-out on threads (under :func:`contextvars.copy_context`) is safe too.

Metadata only. A call is its :class:`~donkey_kit.core.lastcall.LastCall`
record and a refusal is a :class:`RunRefusal`: the error's type, policy and
ids, never the exception itself, whose ``.response`` body (and, for
``PIIDetected``, ``gateway_message``) can echo the content the gateway masked.

Memory is bounded: ``calls`` and ``refusals`` keep the newest
``donkey.run(ledger_max_entries=...)`` each (default 1000) and count the rest
in ``dropped``. The totals (``call_count``, ``refusal_count``, ``usage``) keep
counting past the cap, so a long run's cost stays exact.
"""

from __future__ import annotations

import threading
from collections import deque
from contextvars import ContextVar, Token
from dataclasses import dataclass, fields, replace

from .errors import ConfigError, DonkeyError, PolicyViolation
from .lastcall import LastCall

__all__ = [
    "DEFAULT_MAX_ENTRIES",
    "RunLedger",
    "RunRefusal",
    "RunUsage",
    "bind_run_ledger",
    "checked_max_entries",
    "current_run_ledger",
    "record_call",
    "record_refusal",
    "record_usage",
    "unbind_run_ledger",
]

#: How many calls, and separately how many refusals, a ledger keeps by default.
DEFAULT_MAX_ENTRIES = 1000


@dataclass(frozen=True)
class RunUsage:
    """Token totals across every call in a run, summed from each call's
    ``usage`` (#307). A call that reported no count adds nothing, so a total is
    ``0`` both when nothing was used and when nothing was reported;
    ``calls_with_usage`` tells the two apart."""

    #: Prompt/input tokens, as each provider reports them (see :class:`LastCall`).
    input_tokens: int = 0
    #: Completion/output tokens, including reasoning tokens.
    output_tokens: int = 0
    #: Total tokens the gateway attributed to the run's calls.
    total_tokens: int = 0
    #: Input tokens served from the prompt cache.
    cached_tokens: int = 0
    #: Input tokens written into the prompt cache.
    cache_write_tokens: int = 0
    #: Output tokens spent on reasoning the developer never sees.
    reasoning_tokens: int = 0
    #: How many calls reported any usage count.
    calls_with_usage: int = 0


@dataclass(frozen=True)
class RunRefusal:
    """One refusal in a run's ledger (#613): what refused the call and how to
    find it, with none of the response content.

    Built from the typed :class:`~donkey_kit.core.errors.DonkeyError`, but not
    the exception itself: its ``.response`` keeps the gateway's body, which can
    echo the very values a policy blocked.
    """

    #: The typed error the response maps to, e.g. ``PIIDetected``. Test it with
    #: :meth:`is_a`.
    kind: type[DonkeyError]
    #: The policy slug for a :class:`~donkey_kit.core.errors.PolicyViolation`
    #: (``"pii-detection"``), else ``None``.
    policy: str | None
    #: The refused response's HTTP status (``200`` for a substitution), or ``None``.
    status_code: int | None
    #: The run id the call sent.
    correlation_id: str | None
    #: The per-call id the call sent.
    call_id: str | None
    #: The upstream provider's request id, when the gateway forwarded one.
    request_id: str | None
    #: For ``PIIDetected``, the flagged entity types (``["Email"]``), never the values.
    entities: tuple[str, ...] = ()

    def is_a(self, kind: type[DonkeyError]) -> bool:
        """Whether this refusal is a ``kind``, e.g. ``refusal.is_a(PIIDetected)``."""
        return issubclass(self.kind, kind)

    @classmethod
    def of(cls, error: DonkeyError) -> RunRefusal:
        """The ledger entry for ``error``."""
        response = error.response
        status = getattr(response, "status_code", None)
        entities = getattr(error, "entities", None) or ()
        return cls(
            kind=type(error),
            policy=error.policy if isinstance(error, PolicyViolation) else None,
            status_code=status if isinstance(status, int) else None,
            correlation_id=error.correlation_id,
            call_id=error.call_id,
            request_id=error.request_id,
            entities=tuple(str(e) for e in entities),
        )


_USAGE_FIELDS = tuple(f.name for f in fields(RunUsage) if f.name != "calls_with_usage")


def _counts(record: LastCall | None) -> dict[str, int]:
    if record is None:
        return dict.fromkeys(_USAGE_FIELDS, 0)
    return {name: getattr(record, name) or 0 for name in _USAGE_FIELDS}


def _reported(record: LastCall | None) -> bool:
    return record is not None and any(getattr(record, n) is not None for n in _USAGE_FIELDS)


@dataclass(frozen=True)
class RunLedger:
    """A snapshot of one run's ledger (#613): every governed model call, every
    refusal, and the token totals, as of when it was read.

    Read it inside the block at ``donkey.current_run``, or after the block from
    the scope: ``scope = donkey.run(...)``, ``async with scope: ...``, then
    ``scope.ledger``. A snapshot never changes; read again for newer state.

    Each entry in :attr:`calls` is the call's :class:`LastCall` record, so the
    served model, provider, fallback flag, request id, semantic-cache outcome
    and semantic-routing match of every call are on it.

    Populated only from responses that pass through the SDK's shared HTTP
    client, the same limit as ``donkey.last_call``. When every adapter used on
    the Donkey routes outside it, :attr:`available` is false and :attr:`surface`
    names them: the ledger is unavailable, not empty.
    """

    #: The run's correlation id.
    run_id: str
    #: The newest calls, oldest first, at most the run's ``ledger_max_entries``.
    calls: tuple[LastCall, ...] = ()
    #: The newest refusals, oldest first: every policy refusal the gateway
    #: returned on a model call in the run, whether or not a framework caught it,
    #: and any :class:`~donkey_kit.core.errors.ModelSubstituted` the SDK raised.
    refusals: tuple[RunRefusal, ...] = ()
    #: Every call in the run, including those dropped from :attr:`calls`.
    call_count: int = 0
    #: Every refusal in the run, including those dropped from :attr:`refusals`.
    refusal_count: int = 0
    #: Token totals across every call in the run, dropped calls included.
    usage: RunUsage = RunUsage()
    #: How many calls and refusals the cap dropped from :attr:`calls` and
    #: :attr:`refusals`.
    dropped: int = 0
    #: The adapter surface(s) that cannot feed the ledger, or ``None``.
    surface: str | None = None

    @property
    def available(self) -> bool:
        """``False`` when no call made on this Donkey's adapters can reach the
        ledger, so its emptiness means nothing."""
        return self.surface is None


class _Ledger:
    """The mutable ledger a run binds. Every method takes the lock."""

    __slots__ = (
        "_run_id",
        "_calls",
        "_refusals",
        "_call_count",
        "_refusal_count",
        "_usage",
        "_dropped",
        "_lock",
    )

    def __init__(self, run_id: str, max_entries: int) -> None:
        self._run_id = run_id
        self._calls: deque[LastCall] = deque(maxlen=max_entries)
        self._refusals: deque[RunRefusal] = deque(maxlen=max_entries)
        self._call_count = 0
        self._refusal_count = 0
        self._usage = RunUsage()
        self._dropped = 0
        self._lock = threading.Lock()

    @property
    def run_id(self) -> str:
        return self._run_id

    def _add_usage(self, before: LastCall | None, after: LastCall) -> None:
        old, new = _counts(before), _counts(after)
        newly_reported = _reported(after) and not _reported(before)
        self._usage = replace(
            self._usage,
            calls_with_usage=self._usage.calls_with_usage + int(newly_reported),
            **{n: getattr(self._usage, n) + new[n] - old[n] for n in _USAGE_FIELDS},
        )

    def add_call(self, record: LastCall) -> None:
        with self._lock:
            if len(self._calls) == self._calls.maxlen:
                self._dropped += 1
            self._calls.append(record)
            self._call_count += 1
            self._add_usage(None, record)

    def update_call(self, before: LastCall, after: LastCall) -> None:
        with self._lock:
            # A streamed call's usage lands after its record (#307). Replace the
            # entry if the cap has not dropped it yet; the totals move either way.
            for i in range(len(self._calls) - 1, -1, -1):
                if self._calls[i] is before:
                    self._calls[i] = after
                    break
            self._add_usage(before, after)

    def add_refusal(self, refusal: RunRefusal) -> None:
        with self._lock:
            if len(self._refusals) == self._refusals.maxlen:
                self._dropped += 1
            self._refusals.append(refusal)
            self._refusal_count += 1

    def snapshot(self, surface: str | None = None) -> RunLedger:
        with self._lock:
            return RunLedger(
                run_id=self._run_id,
                calls=tuple(self._calls),
                refusals=tuple(self._refusals),
                call_count=self._call_count,
                refusal_count=self._refusal_count,
                usage=self._usage,
                dropped=self._dropped,
                surface=surface,
            )


# Bound by RunScope for its block; ``None`` outside any run, where nothing is kept.
_run_ledger: ContextVar[_Ledger | None] = ContextVar("donkey_run_ledger", default=None)


def current_run_ledger(surface: str | None = None) -> RunLedger | None:
    """A snapshot of the bound run's ledger, or ``None`` outside a run.
    ``surface`` names the adapters that cannot feed it, if every one used can't."""
    ledger = _run_ledger.get()
    return ledger.snapshot(surface) if ledger is not None else None


def checked_max_entries(value: int) -> int:
    """``value`` if it is a usable ledger cap, else :class:`ConfigError`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConfigError(
            f"ledger_max_entries must be a whole number, 1 or more, not {value!r}.",
            remediation="Pass donkey.run(ledger_max_entries=...) a whole number, 1 or more.",
        )
    return value


def bind_run_ledger(run_id: str, max_entries: int) -> tuple[_Ledger, Token[_Ledger | None]]:
    """Bind a fresh ledger for a run's block; the caller resets the token on exit."""
    # A scope that rebinds the run's own id (an approval gate resuming a tool,
    # #211) keeps the run's ledger, so the tool's calls land in the run.
    outer = _run_ledger.get()
    ledger = outer if outer is not None and outer.run_id == run_id else _Ledger(run_id, max_entries)
    return ledger, _run_ledger.set(ledger)


def unbind_run_ledger(token: Token[_Ledger | None]) -> None:
    """Restore the ledger bound before :func:`bind_run_ledger`."""
    _run_ledger.reset(token)


def record_call(record: LastCall) -> None:
    """Append a governed model call's record to the bound run's ledger. A no-op
    outside a run. Called by the transport beside ``observe_last_call``."""
    ledger = _run_ledger.get()
    if ledger is not None:
        ledger.add_call(record)


def record_usage(before: LastCall | None, after: LastCall | None) -> None:
    """Swap a streamed call's record for the one carrying its usage (#307). A
    no-op outside a run or when either record is missing."""
    ledger = _run_ledger.get()
    if ledger is not None and before is not None and after is not None:
        ledger.update_call(before, after)


def record_refusal(error: DonkeyError) -> None:
    """Append a typed refusal's :class:`RunRefusal` to the bound run's ledger.
    A no-op outside a run."""
    ledger = _run_ledger.get()
    if ledger is not None:
        ledger.add_refusal(RunRefusal.of(error))
