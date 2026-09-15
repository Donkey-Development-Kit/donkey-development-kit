"""LastCall — the gateway's own metadata about the most recent governed model
call, read from the response (§2.3, #362, BG §1.1).

On a refusal, :class:`~donkey_kit.core.errors.DonkeyError` already hands the
developer the gateway's ids (``request_id`` and the two client-sent
correlation/call ids). On a ``200`` the same information was thrown on the
floor: consumed internally by :class:`~donkey_kit.core.budget.Budget`, turned
into an OTel span attribute that needs a backend to read, or dropped. So
"which gateway instance served this, and what id do I quote in a ticket?" was
answerable after a failure and unanswerable after a success. This record closes
that asymmetry — one named container, populated on every governed model call
from the LIVE-VERIFIED §3 response headers, reachable at ``donkey.last_call``.

**One container, defined once (#362).** #307 (cached/reasoning token counts) and
#309 (gateway routing + fallback) each sketched a *different* accessor for the
same single HTTP response (``donkey.budget.last_usage`` and
``donkey.last_routing``). Both add their fields to :class:`LastCall` rather than
inventing a parent — this module owns the container; those issues own their
field groups.

**Concurrency scope: contextvar, not instance (hazard #2).** ``Budget`` gets
away with instance-scoped last-write-wins state because a token window genuinely
is one shared resource — any recent observation is a valid observation. "The
last call" is not like that: under the parallel fan-out ``donkey.run()``
encourages, two concurrent calls race and an instance attribute would return
whichever landed last, which is *worse* than nothing because it looks
authoritative. So the record lives in a :class:`~contextvars.ContextVar`. A
framework-spawned ``asyncio`` task copies the current context at creation, so a
model call inside it sets *its own* task's record and never clobbers a sibling's
(and never leaks back to the parent that scattered the tasks). Each task reads
the call it actually made. This matches how ``core.telemetry`` already scopes the
correlation id.

**Three honest states (hazard #3).** A bare ``request_id is None`` is a lie of
omission on the adapters where the SDK does not own the transport (ADK, CrewAI,
LlamaIndex, MS Agent Framework — see
:attr:`donkey_kit.integrations._base.Adapter.observes_last_call`).
A developer there would read ``None`` as "the gateway sent no id" when the truth
is the SDK never saw the response at all. So the record carries a
:class:`LastCallStatus`: ``OBSERVED`` (a governed response populated it),
``UNOBSERVED`` (no governed model call has returned in this context yet), and
``UNAVAILABLE`` (this surface structurally cannot be observed). The conformance
suite asserts the exemption rather than skipping it (§8.1).

**The gateway correlation id is deliberately absent (hazard #1).** The response
``x-correlation-id`` is the gateway's own value; per #300 it is NOT confirmed to
be an echo of the id the client sent (the captured value is a dash-formatted
UUID, while ``new_correlation_id()`` returns un-dashed ``uuid4().hex``). Naming
it ``correlation_id`` here would collide with the run id on ``DonkeyError`` and
bake that ambiguity into the public API, so the field is not added until #300
lands and settles the semantics under an unambiguous name.

Every field traces to the LIVE-VERIFIED §3 "Gateway identity on response" row
(``responses.success.headers.txt``, 2026-08-28), so this is a consumption task,
not a verification one: no ``_verify.Unverified(...)`` guard, no
``UnverifiedValueWarning``. Unparseable or absent headers leave a field ``None``
and never raise on the caller's request path (§0.3).
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

# VERIFIED (LIVE, docs/verified-apis.md §3, 2026-08-28). ``x-request-id`` is the
# gateway's own per-response id — the SAME header ``classify()`` surfaces as
# ``DonkeyError.request_id`` on a refusal, so the success path now reports it on
# identical terms. ``x-envoy-decorator-operation`` encodes the API-instance id
# and environment id as ``api-instance-<instanceId>.<environmentId>.svc``.
REQUEST_ID_HEADER = "x-request-id"
DECORATOR_OPERATION_HEADER = "x-envoy-decorator-operation"

# ``api-instance-21133858.3e6ce455-e3e8-4402-b830-9fcf07d9207b.svc`` → instance
# ``21133858`` + environment ``3e6ce455-…`` (a UUID; it carries dashes but no
# dots, so a plain three-way split on ``.`` is unambiguous). An unrecognised
# shape matches nothing and yields ``(None, None)`` — never a guess (§0.3).
_DECORATOR_RE = re.compile(r"^api-instance-(?P<instance>[^.]+)\.(?P<env>[^.]+)\.svc$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LastCallStatus(str, Enum):
    """Why :class:`LastCall` fields are (or are not) populated — so a ``None``
    is never ambiguous between "the gateway said nothing" and "the SDK never saw
    the response" (hazard #3). ``str``-valued so it prints and logs cleanly."""

    #: A governed model response populated this record.
    OBSERVED = "observed"
    #: No governed model call has returned in this context yet (a cold read).
    UNOBSERVED = "unobserved"
    #: This adapter surface cannot be observed — the SDK does not own its
    #: transport (LiteLLM-backed) or was handed only ``default_headers`` — so no
    #: response ever reaches the record here. Distinct from a mere cold read.
    UNAVAILABLE = "unavailable"


def _parse_decorator_operation(raw: str | None) -> tuple[str | None, str | None]:
    """``(api_instance_id, environment_id)`` from ``x-envoy-decorator-operation``,
    or ``(None, None)`` when the header is absent or not in the verified
    ``api-instance-<id>.<env>.svc`` shape. Never raises (§0.3)."""
    if raw is None:
        return None, None
    m = _DECORATOR_RE.match(raw.strip())
    if m is None:
        return None, None
    return m["instance"], m["env"]


# --- per-call usage token counts (#307, BG §1.3) ----------------------------
# LIVE-VERIFIED (docs/verified-apis.md §3, ``responses.success.body.json``,
# 2026-08-28): the gateway returns token counts in the response BODY's ``usage``
# object, not a header. So unlike the identity fields these are parsed from the
# parsed JSON body, and only for a buffered 2xx — a streaming body carries its
# usage in a terminal SSE event read later (filled by :func:`observe_usage`), and
# a refusal carries none. The six fields, and why cached/reasoning matter:
#   * ``cached_tokens`` — input tokens served from the prompt cache, typically
#     billed at a fraction of the normal rate;
#   * ``cache_write_tokens`` — input tokens written INTO the cache this call;
#   * ``reasoning_tokens`` — output tokens spent on reasoning the developer never
#     sees. A reasoning model can spend most of its output here, so reading only
#     ``total_tokens`` draws the wrong conclusion about both cost and latency.
# Both wire shapes are read so the raw client, the deep LangGraph adapter, and any
# OpenAI-compatible call populate identically: the Responses API
# (``input_tokens`` + ``input_tokens_details``) and Chat Completions
# (``prompt_tokens`` + ``prompt_tokens_details``).
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def _first_int(mapping: dict[str, object], *keys: str) -> int | None:
    """The first key present as an ``int`` (``bool`` excluded — it subclasses
    ``int``), else ``None``. Absent/garbage is ``None``, never ``0`` (§0.3)."""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _usage_details(usage: dict[str, object], *keys: str) -> dict[str, object]:
    """The first present nested ``*_tokens_details`` sub-object as a dict, else an
    empty dict — so a missing details block reads as "no cached/reasoning signal"
    rather than raising."""
    for key in keys:
        details = usage.get(key)
        if isinstance(details, dict):
            return details
    return {}


def parse_usage(usage: object) -> dict[str, int | None]:
    """The six token counts from a ``usage`` mapping, as a ``{field: int | None}``
    dict keyed by :data:`_USAGE_FIELDS`.

    Handles the Responses API (``input_tokens`` / ``input_tokens_details`` /
    ``output_tokens_details``) and Chat Completions (``prompt_tokens`` /
    ``prompt_tokens_details`` / ``completion_tokens_details``) shapes. A non-dict
    ``usage`` (absent, ``None``, wrong type) yields all-``None`` — an absent count
    is ``None``, never ``0`` (the same honesty rule ``Budget`` applies to an
    unobserved window). Never raises (§0.3)."""
    if not isinstance(usage, dict):
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cached_tokens": None,
            "cache_write_tokens": None,
            "reasoning_tokens": None,
        }
    input_details = _usage_details(usage, "input_tokens_details", "prompt_tokens_details")
    output_details = _usage_details(usage, "output_tokens_details", "completion_tokens_details")
    return {
        "input_tokens": _first_int(usage, "input_tokens", "prompt_tokens"),
        "output_tokens": _first_int(usage, "output_tokens", "completion_tokens"),
        "total_tokens": _first_int(usage, "total_tokens"),
        "cached_tokens": _first_int(input_details, "cached_tokens"),
        "cache_write_tokens": _first_int(input_details, "cache_write_tokens"),
        "reasoning_tokens": _first_int(output_details, "reasoning_tokens"),
    }


def usage_mapping(obj: object) -> dict[str, object] | None:
    """The ``usage`` mapping from a parsed SSE ``data:`` object, or ``None``.

    Handles Chat Completions (top-level ``usage``) and the Responses API (``usage``
    nested under ``response``, as the terminal ``response.completed`` event
    carries it). Feeds the streaming scanner, which then :func:`parse_usage` it."""
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        nested = obj.get("response")
        usage = nested.get("usage") if isinstance(nested, dict) else None
    return usage if isinstance(usage, dict) else None


def usage_from_response(response: httpx.Response) -> dict[str, int | None]:
    """The six usage counts from a buffered 2xx JSON body's ``usage`` object, or
    all-``None`` when the response is non-2xx, streaming/unread, non-JSON, or
    carries no ``usage``. Never raises on the caller's request path (§0.3) — a
    streaming body's ``.json()`` raises ``ResponseNotRead``, which is caught and
    treated as "no usage here" (it arrives later via :func:`observe_usage`)."""
    if response.status_code // 100 != 2:
        return parse_usage(None)
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 — streaming/unread/invalid body: no usage
        return parse_usage(None)
    if not isinstance(body, dict):
        return parse_usage(None)
    return parse_usage(body.get("usage"))


@dataclass(frozen=True)
class LastCall:
    """What the gateway said about the most recent governed model call (§3, #362).

    Reached at ``donkey.last_call``. Immutable: each governed response replaces
    the context's record wholesale rather than mutating in place, so a reader
    always sees one internally-consistent call. The gateway-identity fields land
    here now; #307 (usage tokens) and #309 (routing/fallback) add theirs to this
    same type.
    """

    status: LastCallStatus
    #: The gateway's own per-response id (``x-request-id``) — quote it in a
    #: support ticket. Mirrors :attr:`DonkeyError.request_id` on the refusal path.
    request_id: str | None = None
    #: The API Manager instance id that served the call (from the decorator op).
    api_instance_id: str | None = None
    #: The environment id that served the call (from the decorator op).
    environment_id: str | None = None
    #: When the record was observed (freshness), UTC. ``None`` unless OBSERVED.
    observed_at: datetime | None = None
    #: For UNAVAILABLE, the adapter surface(s) that cannot observe; else ``None``.
    surface: str | None = None
    # --- per-call usage token counts (#307), from the response BODY's ``usage``.
    # ``None`` (never ``0``) when unobserved or absent; on a stream they land once
    # the terminal SSE event is scanned (:func:`observe_usage`), not at record time.
    #: Prompt/input tokens billed for this call.
    input_tokens: int | None = None
    #: Completion/output tokens produced by this call (includes ``reasoning_tokens``).
    output_tokens: int | None = None
    #: Total tokens the gateway attributed to this call.
    total_tokens: int | None = None
    #: Input tokens served from the prompt cache (billed at the cached rate).
    cached_tokens: int | None = None
    #: Input tokens written into the prompt cache this call.
    cache_write_tokens: int | None = None
    #: Output tokens spent on model reasoning the developer never sees.
    reasoning_tokens: int | None = None

    @property
    def observed(self) -> bool:
        """True iff a governed response actually populated this record."""
        return self.status is LastCallStatus.OBSERVED

    @property
    def available(self) -> bool:
        """False only when the current surface structurally cannot be observed
        (LiteLLM-backed / ``default_headers``-only adapters); a plain cold read is
        still ``available`` — it just has not observed anything yet."""
        return self.status is not LastCallStatus.UNAVAILABLE

    @classmethod
    def from_response(cls, response: httpx.Response, *, now: datetime | None = None) -> LastCall:
        """Build an ``OBSERVED`` record from a governed response's §3 headers.

        Always ``OBSERVED`` — the SDK saw a response — even if the gateway
        carried no identity headers (each field then stays ``None``): "we saw the
        response, it said nothing" is a true and different statement from "we
        never saw a response". ``now`` is injectable for tests."""
        api_instance_id, environment_id = _parse_decorator_operation(
            response.headers.get(DECORATOR_OPERATION_HEADER)
        )
        usage = usage_from_response(response)
        return cls(
            status=LastCallStatus.OBSERVED,
            request_id=response.headers.get(REQUEST_ID_HEADER),
            api_instance_id=api_instance_id,
            environment_id=environment_id,
            observed_at=now if now is not None else _utcnow(),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            cached_tokens=usage["cached_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
        )


# The cold-read value: no governed model call has returned in this context yet.
# A single frozen instance is safe to share — it is immutable and carries no
# per-call state.
UNOBSERVED = LastCall(status=LastCallStatus.UNOBSERVED)


def unavailable(surface: str) -> LastCall:
    """The record for a surface that structurally cannot be observed — names the
    adapter(s) so ``donkey.last_call`` reads "not available on this surface"
    instead of an indistinguishable bare ``None`` (hazard #3)."""
    return LastCall(status=LastCallStatus.UNAVAILABLE, surface=surface)


# Contextvar-scoped, never instance-scoped (hazard #2): the record for the call
# made in *this* context. Set by the transport's ``_on_response`` on every
# governed model response; read by ``donkey.last_call``. ``None`` means no call
# has been observed in this context — a cold read, surfaced as :data:`UNOBSERVED`.
_last_call: ContextVar[LastCall | None] = ContextVar("donkey_last_call", default=None)


def current_last_call() -> LastCall | None:
    """The record observed in this context, or ``None`` for a cold read. The
    ``donkey.last_call`` accessor turns a ``None`` into :data:`UNOBSERVED` or an
    :func:`unavailable` record depending on which adapters have been used."""
    return _last_call.get()


def observe_last_call(response: httpx.Response, *, now: datetime | None = None) -> LastCall:
    """Record the gateway identity from a governed model response into this
    context (§2.3, #362). Called from the transport's ``_on_response`` beside
    :meth:`Budget.observe`. Returns the record it set, for tests. ``now`` is
    injectable; production uses the wall clock (UTC)."""
    record = LastCall.from_response(response, now=now)
    _last_call.set(record)
    return record


def observe_usage(counts: dict[str, int | None]) -> LastCall | None:
    """Merge streamed usage token counts into this context's record once the
    terminal SSE event has been scanned (#307).

    A streamed response has no usage at :func:`observe_last_call` time — it lands
    in a terminal event the caller reads later — so the transport's stream wrapper
    calls this on finalize. It only fills the usage fields (identity stays as the
    ``OBSERVED`` record already set in this context) and only for a value actually
    seen, so a partial terminal event never overwrites a known count with ``None``.
    A no-op — returning ``None`` — when there is no ``OBSERVED`` record to merge
    into (the transport always sets one before the stream is consumed)."""
    current = _last_call.get()
    if current is None or current.status is not LastCallStatus.OBSERVED:
        return None

    # Only fill a field the terminal event actually carried; fall back to the
    # value already on the record so a partial event never nulls a known count.
    # Explicit per-field (not ``**counts``) keeps mypy --strict happy: replace()
    # can't type-check a splatted str-keyed dict against the frozen fields.
    def _pick(field: str, existing: int | None) -> int | None:
        value = counts.get(field)
        return value if value is not None else existing

    merged = replace(
        current,
        input_tokens=_pick("input_tokens", current.input_tokens),
        output_tokens=_pick("output_tokens", current.output_tokens),
        total_tokens=_pick("total_tokens", current.total_tokens),
        cached_tokens=_pick("cached_tokens", current.cached_tokens),
        cache_write_tokens=_pick("cache_write_tokens", current.cache_write_tokens),
        reasoning_tokens=_pick("reasoning_tokens", current.reasoning_tokens),
    )
    _last_call.set(merged)
    return merged
