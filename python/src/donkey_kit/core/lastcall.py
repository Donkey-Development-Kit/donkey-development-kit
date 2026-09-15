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
from dataclasses import dataclass
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

# VERIFIED (LIVE, docs/verified-apis.md §3 "Gateway identity on response",
# 2026-08-28, ``responses.success.headers.txt``). The gateway states what it did
# with the request: which provider/model actually served it, whether that was a
# routing FALLBACK (the "Enhanced Resilience for Intelligent Routing" failover),
# and the routing strategy. All four are consumed here on the success path
# (#309) — a substitution the developer did not choose is otherwise invisible.
# ``LLM_PROVIDER_HEADER`` is also the SOLE source of ``gen_ai.system`` on the
# span, so ``core/transport.py`` imports it from here (one definition, §0.3).
ROUTING_TYPE_HEADER = "x-llm-proxy-routing-type"
ROUTING_FALLBACK_HEADER = "x-llm-proxy-routing-fallback"
LLM_PROVIDER_HEADER = "x-llm-proxy-llm-provider"
LLM_MODEL_HEADER = "x-llm-proxy-llm-model"

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


def _parse_fallback(raw: str | None) -> bool | None:
    """The routing-fallback flag from ``x-llm-proxy-routing-fallback``, or ``None``
    when the header is absent (a non-proxy / simulated response) or carries an
    unrecognised value. VERIFIED values are the literal strings ``"true"`` /
    ``"false"``; anything else is treated as unknown (``None``) rather than
    guessed, so a bare ``None`` never masquerades as a definitive ``False``
    (§0.3)."""
    if raw is None:
        return None
    token = raw.strip().lower()
    if token == "true":
        return True
    if token == "false":
        return False
    return None


def routing_fallback(response: httpx.Response) -> bool | None:
    """The tri-state routing-fallback flag for a response: ``True`` / ``False``
    when the gateway stated it, ``None`` when the header is absent (non-proxy /
    simulated) or unrecognised. This is the value that lands on
    :attr:`LastCall.fallback` and the span, where ``False`` is a meaningful
    observation distinct from ``None``."""
    return _parse_fallback(response.headers.get(ROUTING_FALLBACK_HEADER))


def is_fallback(response: httpx.Response) -> bool:
    """True iff the gateway definitively marked this response as a routing
    FALLBACK. Used by the transport's retry predicate: a response the gateway
    already failed over must not be additionally retried by the SDK, or an outage
    the gateway handled gets a second recovery layer stacked on top (#309, #183).
    An absent or unrecognised header is *not* a fallback, so this definitive-
    ``True``-only reading is safe to gate a retry decision on."""
    return routing_fallback(response) is True


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
    # --- gateway routing & fallback (§3, #309) -----------------------------
    #: The model the caller ASKED for (from the request body). The reference
    #: point for :attr:`substituted`; ``None`` when the request carried none.
    requested_model: str | None = None
    #: The provider the gateway actually routed to (``x-llm-proxy-llm-provider``).
    served_provider: str | None = None
    #: The model the gateway actually served (``x-llm-proxy-llm-model``). After a
    #: routing fallback this may differ from :attr:`requested_model`.
    served_model: str | None = None
    #: The routing strategy the gateway applied (``x-llm-proxy-routing-type``,
    #: e.g. ``"ModelBased"``).
    routing_type: str | None = None
    #: Whether the gateway performed a routing FALLBACK. ``None`` when the header
    #: is absent (non-proxy / simulated response) — distinct from a definitive
    #: ``False`` ("no fallback occurred").
    fallback: bool | None = None

    @property
    def observed(self) -> bool:
        """True iff a governed response actually populated this record."""
        return self.status is LastCallStatus.OBSERVED

    @property
    def substituted(self) -> bool:
        """True iff the gateway served a *different* model than the one requested
        — a silent model substitution the developer's cost model, token
        assumptions and evaluation are otherwise blind to (#309). Requires both
        the requested and served model to be known; a missing either side is not
        a substitution claim."""
        return (
            self.requested_model is not None
            and self.served_model is not None
            and self.requested_model != self.served_model
        )

    @property
    def available(self) -> bool:
        """False only when the current surface structurally cannot be observed
        (LiteLLM-backed / ``default_headers``-only adapters); a plain cold read is
        still ``available`` — it just has not observed anything yet."""
        return self.status is not LastCallStatus.UNAVAILABLE

    @classmethod
    def from_response(
        cls,
        response: httpx.Response,
        *,
        requested_model: str | None = None,
        now: datetime | None = None,
    ) -> LastCall:
        """Build an ``OBSERVED`` record from a governed response's §3 headers.

        Always ``OBSERVED`` — the SDK saw a response — even if the gateway
        carried no identity headers (each field then stays ``None``): "we saw the
        response, it said nothing" is a true and different statement from "we
        never saw a response". ``requested_model`` is the model the caller sent
        (the transport reads it from the request body); it is the reference point
        for :attr:`substituted`. ``now`` is injectable for tests."""
        api_instance_id, environment_id = _parse_decorator_operation(
            response.headers.get(DECORATOR_OPERATION_HEADER)
        )
        return cls(
            status=LastCallStatus.OBSERVED,
            request_id=response.headers.get(REQUEST_ID_HEADER),
            api_instance_id=api_instance_id,
            environment_id=environment_id,
            observed_at=now if now is not None else _utcnow(),
            requested_model=requested_model,
            served_provider=response.headers.get(LLM_PROVIDER_HEADER),
            served_model=response.headers.get(LLM_MODEL_HEADER),
            routing_type=response.headers.get(ROUTING_TYPE_HEADER),
            fallback=_parse_fallback(response.headers.get(ROUTING_FALLBACK_HEADER)),
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


def observe_last_call(
    response: httpx.Response,
    *,
    requested_model: str | None = None,
    now: datetime | None = None,
) -> LastCall:
    """Record the gateway identity, routing and fallback from a governed model
    response into this context (§2.3, #362/#309). Called from the transport's
    ``_on_response`` beside :meth:`Budget.observe`, which passes the requested
    model it already parsed from the request body. Returns the record it set, for
    tests. ``now`` is injectable; production uses the wall clock (UTC)."""
    record = LastCall.from_response(response, requested_model=requested_model, now=now)
    _last_call.set(record)
    return record
