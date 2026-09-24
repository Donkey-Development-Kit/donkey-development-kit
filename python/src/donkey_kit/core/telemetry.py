"""Telemetry (BG §1.6) and the run-scoped correlation ID (BG §1.1).

OpenTelemetry is an optional dependency (the ``[otel]`` extra). Telemetry is
**on by default** (``DonkeyConfig.telemetry``), but the export pipeline is
**inert unless an OTLP endpoint is configured**: :func:`configure_otlp_export`
installs a real exporter only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` (or the
traces-specific variant) is set — so a process with no endpoint produces no
spans, connects to nothing, and prints nothing (BG §1.6 "inert and silent",
#194). This is what makes the zero-config promise hold: set the standard OTel
endpoint env var and spans flow to your sink with **no SDK-specific env var**.
Opt out of telemetry entirely with the single flag ``DONKEY_TELEMETRY=false``.

The correlation ID lives in a ``contextvar`` so a single agent run's fan-out of
model calls and tool calls shares one trace ID end to end — letting a developer
correlate their local trace with what the platform team sees in Omni Gateway's
observability view (a headline feature, BG §1.6).
"""

from __future__ import annotations

import contextlib
import os
import uuid
import warnings
from collections.abc import Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from .cost import CostTags

if TYPE_CHECKING:
    from .config import DonkeyConfig
from .errors import (
    ContentSafetyBlocked,
    DonkeyError,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
)

_correlation_id: ContextVar[str | None] = ContextVar("donkey_correlation_id", default=None)
# Per-run cost-tag overrides bound by ``donkey.run(team=..., ...)`` (#196). Like
# the correlation id it is contextvar-bound, so a run's overrides reach every
# model call in the block — including calls on framework-spawned asyncio tasks,
# which copy the current context — with no threading through framework state.
_cost_tags: ContextVar[CostTags | None] = ContextVar("donkey_cost_tags", default=None)

# Span name constants (BG §1.6).
SPAN_LLM_CHAT = "donkey.llm.chat"
SPAN_REGISTRY_RESOLVE = "donkey.registry.resolve"
SPAN_TOOL_CALL = "donkey.tool.call"
SPAN_PROVISION_APPLY = "donkey.provision.apply"

# --- GenAI span attribute contract (#192, BG §1.6) --------------------------
# Two namespaces on one span (see :func:`genai_span`):
#
#   gen_ai.*  — the OpenTelemetry GenAI semantic conventions, PINNED to the
#     version below. The keys are transcribed literals, deliberately NOT imported
#     from ``opentelemetry.semconv``: the installed package tracks the latest
#     schema (its default drifts release to release), so re-exporting from it
#     would silently change what we emit. Pinning here means what lands on a span
#     is decided in this file, at this version — and bumping the pin is one
#     reviewable, changelog-worthy edit (AC #1/#2).
#
#   donkey.*  — the stable Donkey namespace. These keys are PUBLIC API:
#     renaming one is a breaking change (AC #4), independent of any gen_ai.* bump.
#
# The OTel GenAI conventions are still evolving upstream (they live under
# ``_incubating``); pinning is exactly what insulates us from that churn.
GEN_AI_SEMCONV_VERSION = "1.30.0"

# gen_ai.* — pinned to GEN_AI_SEMCONV_VERSION.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
# The model the gateway ACTUALLY served (``x-llm-proxy-llm-model``). Distinct
# from ``gen_ai.request.model``: after a routing fallback the two differ, and the
# semconv's ``gen_ai.response.model`` is exactly "the model that generated the
# response" (#309). When latency spikes, seeing request≠response model on the
# span is the single fastest read that a failover happened.
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
# Cached-input and reasoning-output token counts (#307). The GenAI semconv at
# GEN_AI_SEMCONV_VERSION pins ONLY ``gen_ai.usage.input_tokens`` /
# ``output_tokens`` — it defines no stable key for cached or reasoning tokens — so
# per AC #4 ("under the pinned semconv names where they exist") these ride the
# stable ``donkey.*`` namespace instead of an invented ``gen_ai.*`` key. Promote a
# row to ``gen_ai.*`` only when a semconv version that pins it is adopted here.

# gen_ai.* CONTENT attributes — the message text itself, pinned like the rest.
# These are the ONLY attributes gated behind ``telemetry_capture_content`` (#306):
# the semconv defines them as opt-in, and emitting them by default would
# re-export prompts/completions upstream of the gateway's PII masking. They are
# deliberately kept OUT of :data:`_ALLOWED_SPAN_ATTRIBUTES` so the generic
# :func:`span` can never carry them, and are dropped by :meth:`GenAiSpan.record`
# unless the caller opted in.
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"

# donkey.* — stable public API (renaming a value here is a breaking change).
DONKEY_CORRELATION_ID = "donkey.correlation_id"
DONKEY_POLICY_DECISION = "donkey.policy.decision"
DONKEY_POLICY_TYPE = "donkey.policy.type"
DONKEY_BUDGET_REMAINING = "donkey.budget.remaining"
# Cost-attribution dimensions (docs/verified-apis.md §3, BG §1.7, #196). One
# donkey.cost.* attribute per fixed dimension; ``enduser.id`` keeps its dotted
# external name. These carry the full value on the span even while the
# request-header names are UNVERIFIED (docs/verified-apis.md §3), so
# per-dimension spend attribution works end to end.
DONKEY_COST_TEAM = "donkey.cost.team"
DONKEY_COST_PROJECT = "donkey.cost.project"
DONKEY_COST_ENV = "donkey.cost.env"
DONKEY_COST_ENDUSER = "donkey.cost.enduser.id"
# Gateway routing & resilience (docs/verified-apis.md §3, #309). The served
# provider already lands on
# ``gen_ai.system`` and the served model on ``gen_ai.response.model``; these two
# carry the gateway-specific routing facts the semconv has no key for. Emitted
# even when ``fallback`` is ``False`` — "we routed normally" is a signal an
# operator wants on every span, not just the failover ones.
DONKEY_ROUTING_TYPE = "donkey.routing.type"
DONKEY_ROUTING_FALLBACK = "donkey.routing.fallback"
# Semantic-routing match detail (docs/verified-apis.md §3, #590). Populated only
# on a semantic-routing proxy (``routing_type == "Semantic"``); absent —and so
# dropped from the span— on model-based routing. The topic and score explain the
# routing decision the bare ``donkey.routing.type`` leaves opaque.
DONKEY_ROUTING_MATCHED_TOPIC = "donkey.routing.matched_topic"
DONKEY_ROUTING_SCORE = "donkey.routing.score"
# Semantic-cache outcome (docs/verified-apis.md §2, #587). Populated only when the
# proxy is fronted by the semantic-caching policy (the ``x-semantic-cache-*``
# headers are present); absent — and so dropped from the span — otherwise. The
# status makes cache effectiveness observable on a trace, and ``hit`` is the
# signal a cost rollup (#317) reads to exclude a verbatim-replay's tokens from
# fresh spend. Stable public API, same as the other donkey.* keys.
DONKEY_CACHE_STATUS = "donkey.cache.status"
DONKEY_CACHE_SCORE = "donkey.cache.score"
# Per-call usage token counts the semconv has no pinned key for (#307). Stable
# public API, same as the other donkey.* keys — renaming one is a breaking change.
DONKEY_USAGE_CACHED_TOKENS = "donkey.usage.cached_tokens"
DONKEY_USAGE_CACHE_WRITE_TOKENS = "donkey.usage.cache_write_tokens"
DONKEY_USAGE_REASONING_TOKENS = "donkey.usage.reasoning_tokens"

# donkey.policy.decision values.
POLICY_DECISION_ALLOW = "allow"
POLICY_DECISION_REFUSE = "refuse"

# --- Content redaction boundary (#306, BG §1.6) -----------------------------
# The attributes that carry message TEXT. Emitting any of these requires an
# explicit ``telemetry_capture_content=True`` opt-in — see :data:`GEN_AI_PROMPT`.
# Adding a new content-bearing attribute (e.g. tool-call arguments/results, when
# that span grows one) means adding it HERE, so the single switch keeps covering
# it and it stays out of the allowlist below.
_CONTENT_ATTRIBUTES = frozenset({GEN_AI_PROMPT, GEN_AI_COMPLETION})

# The allowlist for the generic :func:`span` emitter: every non-content span
# attribute the SDK is permitted to set. The mechanism is an allowlist, not a
# denylist, so a content attribute (or any future key) cannot reach a span by
# accident from any call site — only a key added here is ever emitted, and
# content keys are deliberately excluded. Kept in sync by construction: it is
# the union of the metadata constants, with :data:`_CONTENT_ATTRIBUTES` removed.
_ALLOWED_SPAN_ATTRIBUTES = frozenset(
    {
        GEN_AI_SYSTEM,
        GEN_AI_REQUEST_MODEL,
        GEN_AI_RESPONSE_MODEL,
        GEN_AI_USAGE_INPUT_TOKENS,
        GEN_AI_USAGE_OUTPUT_TOKENS,
        DONKEY_USAGE_CACHED_TOKENS,
        DONKEY_USAGE_CACHE_WRITE_TOKENS,
        DONKEY_USAGE_REASONING_TOKENS,
        DONKEY_CORRELATION_ID,
        DONKEY_POLICY_DECISION,
        DONKEY_POLICY_TYPE,
        DONKEY_BUDGET_REMAINING,
        DONKEY_COST_TEAM,
        DONKEY_COST_PROJECT,
        DONKEY_COST_ENV,
        DONKEY_COST_ENDUSER,
        DONKEY_ROUTING_TYPE,
        DONKEY_ROUTING_FALLBACK,
        DONKEY_ROUTING_MATCHED_TOPIC,
        DONKEY_ROUTING_SCORE,
        DONKEY_CACHE_STATUS,
        DONKEY_CACHE_SCORE,
    }
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def new_call_id() -> str:
    """A fresh per-request **call id** (BG §1.1, #195).

    Unlike the run/correlation id — which is contextvar-bound and shared across
    every request in a :func:`run_context` / ``donkey.run()`` block — this is
    generated anew for each logical request, so one call can be pinpointed within
    a run. It is client-generated, so it exists even when a request fails before
    any response (a transport error carries no gateway ``x-request-id``)."""
    return uuid.uuid4().hex


def current_correlation_id() -> str | None:
    return _correlation_id.get()


def current_cost_tags() -> CostTags | None:
    """The cost-tag overrides bound by the enclosing ``donkey.run(...)`` block,
    or ``None`` outside one (#196). The transport and span recorder merge these
    over the configured tags, per field, so a run-scope dimension wins for its
    block and the rest fall back to config."""
    return _cost_tags.get()


@contextlib.contextmanager
def run_context(run_id: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for the duration of a logical agent run.

    ``with donkey.run_context(run_id=...)`` lets callers supply their own ID;
    otherwise one is generated. Nested calls restore the previous value on exit.
    """

    rid = run_id or new_correlation_id()
    token = _correlation_id.set(rid)
    try:
        yield rid
    finally:
        _correlation_id.reset(token)


class RunScope:
    """A **dual sync/async** context manager that binds the run correlation id
    to :data:`_correlation_id` for the block (BG §1.1, #195).

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
    """

    __slots__ = ("_run_id", "_cost", "_token", "_cost_token")

    def __init__(self, run_id: str | None = None, cost: CostTags | None = None) -> None:
        self._run_id = run_id
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

    async def __aenter__(self) -> str:
        return self._bind()

    async def __aexit__(self, *exc: Any) -> None:
        self._unbind()


def run_scope(run_id: str | None = None, cost: CostTags | None = None) -> RunScope:
    """Build a :class:`RunScope` — the dual sync/async run correlation binding
    behind ``donkey.run(id=...)`` (BG §1.1, #195), optionally carrying per-run
    cost-tag overrides (#196)."""
    return RunScope(run_id, cost)


def ensure_correlation_id() -> str:
    """Return the current correlation ID, creating (and binding) one if absent."""
    rid = _correlation_id.get()
    if rid is None:
        rid = new_correlation_id()
        _correlation_id.set(rid)
    return rid


def request_correlation_id() -> str:
    """The bound run's correlation ID, or a fresh one that is deliberately *not*
    bound.

    For blocking callers. :func:`ensure_correlation_id` binds on first use, which
    is right under ``asyncio.run`` — that runs in its own ``Context``, so the
    binding dies with the run and one run shares one ID. A synchronous call has
    no such boundary: binding there would pin the very first request's ID to the
    ambient context for the rest of the process, so every later unrelated call
    would report the same run. Grouping stays opt-in via :func:`run_context`.
    """
    return _correlation_id.get() or new_correlation_id()


# --- Optional OpenTelemetry span helper -------------------------------------
def _tracer() -> Any | None:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer("donkey_kit")


# --- Zero-config OTLP export bootstrap (BG §1.6, #194) -----------------------
# The span helpers above only ever call ``trace.get_tracer(...)`` — they ride
# whatever global TracerProvider the host process installed. On their own they
# export nothing: OpenTelemetry's default is a no-op provider. This section is
# what turns "we build spans" into "spans reach the customer's sink", with the
# zero-config contract of BG §1.6:
#
#   set OTEL_EXPORTER_OTLP_ENDPOINT (the *standard* OTel env var) → spans export.
#   no SDK-specific env var, and no endpoint set → inert and silent.
#
# Export I/O runs on the BatchSpanProcessor's background thread, off the request
# hot path — which is exactly why per-call overhead stays under the 1ms bar
# (benchmarked in CI, #194): the call site only creates the span, sets attributes
# and enqueues; the network flush is somebody else's thread.


class TelemetryExportWarning(UserWarning):
    """Emitted once when an OTLP endpoint is configured but the exporter it needs
    is not installed — so the caller asked for export and would otherwise get
    silence. Never raised when no endpoint is set (that path is inert by design).
    """


# Guards ``configure_otlp_export`` so multiple ``Donkey()`` constructions install
# at most one provider per process (an OTel provider is a process-global; a second
# install is refused by OTel with a warning anyway). Reset only by tests.
_otlp_export_configured = False
# One-time de-dupe for the missing-exporter warning, keyed by protocol.
_warned_missing_exporter: set[str] = set()


def _otlp_endpoint_configured() -> bool:
    """True iff a standard OTLP endpoint env var is set (BG §1.6). This is the
    inert-and-silent gate: with neither set we never build a provider, so an
    unconfigured process connects to nothing and prints nothing (AC #4)."""
    for name in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        if os.environ.get(name, "").strip():
            return True
    return False


def _otlp_protocol() -> str:
    """The configured OTLP protocol, honouring the standard env vars (traces-
    specific overrides the generic), defaulting to ``http/protobuf`` — the
    OTel-recommended default and the one the ``[otel]`` extra ships an exporter
    for."""
    raw = (
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
        or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "http/protobuf"
    )
    return raw.strip().lower()


def _warn_missing_exporter(protocol: str, package: str) -> None:
    if protocol in _warned_missing_exporter:
        return
    _warned_missing_exporter.add(protocol)
    warnings.warn(
        f"OTEL_EXPORTER_OTLP_ENDPOINT is set and Donkey telemetry is on, but the "
        f"OTLP {protocol!r} exporter is not installed, so no spans will be "
        f"exported. Install it with: pip install {package}",
        TelemetryExportWarning,
        stacklevel=2,
    )


def _build_otlp_exporter() -> Any | None:
    """Construct the OTLP span exporter for the configured protocol, or ``None``
    (after a one-time warning) if that protocol's exporter package is absent.

    The exporter reads ``OTEL_EXPORTER_OTLP_ENDPOINT`` / headers / timeout from
    the environment itself — that is what keeps the wiring zero-config."""
    protocol = _otlp_protocol()
    if protocol in ("http/protobuf", "http/json", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError:
            _warn_missing_exporter("http/protobuf", "opentelemetry-exporter-otlp-proto-http")
            return None
        return OTLPSpanExporter()
    if protocol == "grpc":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GRPCSpanExporter,
            )
        except ImportError:
            # grpc is intentionally NOT in the [otel] extra (it drags in grpcio);
            # honour the protocol only if the caller installed the grpc exporter.
            _warn_missing_exporter("grpc", "opentelemetry-exporter-otlp-proto-grpc")
            return None
        return GRPCSpanExporter()
    _warn_missing_exporter(protocol, "opentelemetry-exporter-otlp-proto-http")
    return None


def _build_tracer_provider(config: DonkeyConfig) -> Any | None:
    """Build a configured ``TracerProvider`` (OTLP exporter behind a
    ``BatchSpanProcessor``), or ``None`` when export must stay inert.

    Returns ``None`` — and touches no global state — when telemetry is off
    (``DONKEY_TELEMETRY=false``), when no OTLP endpoint is configured (AC #4), or
    when the ``[otel]`` SDK/exporter is not installed. Pure with respect to the
    OTel global provider, so it is unit-testable without fighting the process
    singleton; the caller (:func:`configure_otlp_export`) owns installation.

    The default ``TracerProvider`` resource already reads ``OTEL_SERVICE_NAME``
    and ``OTEL_RESOURCE_ATTRIBUTES``, so those standard env vars are honoured for
    free — no Donkey-specific service-name knob."""
    if not config.telemetry or not _otlp_endpoint_configured():
        return None
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return None
    exporter = _build_otlp_exporter()
    if exporter is None:
        return None
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def configure_otlp_export(config: DonkeyConfig) -> None:
    """Install a zero-config OTLP exporter for this process, once (BG §1.6, #194).

    Called from ``Donkey.__init__``. Inert and silent (AC #4) when telemetry is
    off, when no OTLP endpoint env var is set, or when ``[otel]`` is not
    installed. When an endpoint *is* set, installs a ``TracerProvider`` + OTLP
    ``BatchSpanProcessor`` as the global provider — **unless a host already
    installed an SDK provider** (``opentelemetry-instrument``, a manual setup),
    in which case that one is left untouched and our spans simply ride it.

    Idempotent: guarded so repeated ``Donkey()`` construction installs at most
    one provider per process."""
    global _otlp_export_configured
    if _otlp_export_configured:
        return
    provider = _build_tracer_provider(config)
    if provider is None:
        return  # inert: opt-out, no endpoint, or [otel]/exporter absent.
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        # A host already owns the global provider — never clobber it. Drop the
        # one we just built so its batch thread does not linger unused.
        provider.shutdown()
        _otlp_export_configured = True
        return
    trace.set_tracer_provider(provider)
    _otlp_export_configured = True


@contextlib.contextmanager
def span(name: str, *, enabled: bool, **attributes: Any) -> Iterator[None]:
    """Start an OTel span if telemetry is enabled and OTel is installed.

    Always attaches the correlation ID. A no-op (and never an error) when
    telemetry is off or OTel is not installed — telemetry must never be a hard
    dependency of the library.

    Only attributes in :data:`_ALLOWED_SPAN_ATTRIBUTES` are set: the emitter is
    allowlist-driven, so a content attribute (or any unrecognised key) handed in
    from any call site is dropped rather than exported (#306). Message content
    has no path through this function at all — it is carried only by the GenAI
    span, and only under an explicit opt-in (see :meth:`GenAiSpan.record`).
    """

    if not enabled:
        yield
        return
    tracer = _tracer()
    if tracer is None:
        yield
        return
    with tracer.start_as_current_span(name) as sp:  # pragma: no cover - needs otel
        sp.set_attribute(DONKEY_CORRELATION_ID, ensure_correlation_id())
        for key, value in attributes.items():
            if value is not None and key in _ALLOWED_SPAN_ATTRIBUTES:
                sp.set_attribute(key, value)
        yield


# --- GenAI chat span (#192, BG §1.6) ----------------------------------------
def policy_type_slug(error: DonkeyError) -> str | None:
    """The :data:`DONKEY_POLICY_TYPE` value for a classified refusal, or ``None``
    for a non-policy error (auth / upstream / transport) that carries no
    governance allow-or-refuse decision.

    Ordered most-specific-subclass first so a :class:`PIIDetected` (which *is* a
    :class:`PolicyViolation`) reports ``"pii_detected"``, not the generic slug.
    """
    if isinstance(error, TokenBudgetExceeded):
        return "token_budget"
    if isinstance(error, PIIDetected):
        return "pii_detected"
    if isinstance(error, PromptInjectionBlocked):
        return "injection"
    if isinstance(error, ContentSafetyBlocked):
        return "content_safety"
    if isinstance(error, PolicyViolation):
        return "policy_violation"
    return None


def build_genai_attributes(
    *,
    system: str | None = None,
    request_model: str | None = None,
    response_model: str | None = None,
    routing_type: str | None = None,
    fallback: bool | None = None,
    matched_topic: str | None = None,
    routing_score: float | None = None,
    cache_status: str | None = None,
    cache_score: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cached_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    decision: str | None = None,
    policy_type: str | None = None,
    budget_remaining: int | None = None,
    cost_team: str | None = None,
    cost_project: str | None = None,
    cost_env: str | None = None,
    cost_enduser_id: str | None = None,
    correlation_id: str | None = None,
    prompt: str | None = None,
    completion: str | None = None,
) -> dict[str, Any]:
    """Assemble the dual-namespace GenAI span attributes, omitting any field left
    ``None``.

    Both the pinned ``gen_ai.*`` keys and the stable ``donkey.*`` keys are built
    here and land on ONE span (:func:`genai_span`, AC #5). A ``None`` field is
    an unobserved value: it is dropped, never emitted as a null or a placeholder,
    so a caller can record what it knows as it learns it (request model first,
    response fields once the response settles).

    ``prompt`` / ``completion`` are message CONTENT: this function will place
    them under the pinned :data:`GEN_AI_PROMPT` / :data:`GEN_AI_COMPLETION` keys,
    but the opt-in gate lives in :meth:`GenAiSpan.record` — the only caller —
    which drops these keys unless ``telemetry_capture_content`` was set (#306).
    """
    attrs: dict[str, Any] = {}
    if system is not None:
        attrs[GEN_AI_SYSTEM] = system
    if request_model is not None:
        attrs[GEN_AI_REQUEST_MODEL] = request_model
    if response_model is not None:
        attrs[GEN_AI_RESPONSE_MODEL] = response_model
    if routing_type is not None:
        attrs[DONKEY_ROUTING_TYPE] = routing_type
    # ``fallback`` is emitted even when ``False`` — "no fallback" is a real,
    # useful observation; only an absent header (``None``) is dropped (#309).
    if fallback is not None:
        attrs[DONKEY_ROUTING_FALLBACK] = fallback
    # Semantic-routing match detail: present only on a semantic-routing proxy,
    # dropped (``None``) on model-based routing — same omit-when-unobserved rule.
    if matched_topic is not None:
        attrs[DONKEY_ROUTING_MATCHED_TOPIC] = matched_topic
    if routing_score is not None:
        attrs[DONKEY_ROUTING_SCORE] = routing_score
    # Semantic-cache outcome: present only when the caching policy fronts the
    # proxy, dropped (``None``) otherwise — same omit-when-unobserved rule (#587).
    if cache_status is not None:
        attrs[DONKEY_CACHE_STATUS] = cache_status
    if cache_score is not None:
        attrs[DONKEY_CACHE_SCORE] = cache_score
    if input_tokens is not None:
        attrs[GEN_AI_USAGE_INPUT_TOKENS] = input_tokens
    if output_tokens is not None:
        attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = output_tokens
    if cached_tokens is not None:
        attrs[DONKEY_USAGE_CACHED_TOKENS] = cached_tokens
    if cache_write_tokens is not None:
        attrs[DONKEY_USAGE_CACHE_WRITE_TOKENS] = cache_write_tokens
    if reasoning_tokens is not None:
        attrs[DONKEY_USAGE_REASONING_TOKENS] = reasoning_tokens
    if decision is not None:
        attrs[DONKEY_POLICY_DECISION] = decision
    if policy_type is not None:
        attrs[DONKEY_POLICY_TYPE] = policy_type
    if budget_remaining is not None:
        attrs[DONKEY_BUDGET_REMAINING] = budget_remaining
    if cost_team is not None:
        attrs[DONKEY_COST_TEAM] = cost_team
    if cost_project is not None:
        attrs[DONKEY_COST_PROJECT] = cost_project
    if cost_env is not None:
        attrs[DONKEY_COST_ENV] = cost_env
    if cost_enduser_id is not None:
        attrs[DONKEY_COST_ENDUSER] = cost_enduser_id
    if correlation_id is not None:
        attrs[DONKEY_CORRELATION_ID] = correlation_id
    if prompt is not None:
        attrs[GEN_AI_PROMPT] = prompt
    if completion is not None:
        attrs[GEN_AI_COMPLETION] = completion
    return attrs


class GenAiSpan:
    """Handle to the in-flight GenAI span.

    :meth:`record` maps keyword fields through :func:`build_genai_attributes` and
    sets each resulting attribute on the span. When there is no live span
    (telemetry off, or OpenTelemetry not installed) it wraps ``None`` and every
    call is a no-op — telemetry must never be a hard dependency or an error path.

    ``capture_content`` is the per-span copy of ``telemetry_capture_content``
    (#306): when ``False`` (the default), :meth:`record` drops every
    content-bearing attribute (:data:`_CONTENT_ATTRIBUTES`) before it touches the
    span, so prompts/completions never reach the exporter regardless of what a
    call site passes.
    """

    __slots__ = ("_span", "_capture_content")

    def __init__(self, span: Any | None, *, capture_content: bool = False) -> None:
        self._span = span
        self._capture_content = capture_content

    def record(self, **fields: Any) -> None:
        """Set the (non-``None``) attributes named by ``fields`` on the span.

        Idempotent-friendly: call it repeatedly as values become known; each key
        is set to its latest observed value and unobserved fields are skipped.

        Message-content attributes (prompt/completion) are dropped here unless
        this span was opened with ``capture_content=True`` — the redaction
        boundary (#306), enforced regardless of the caller.
        """
        if self._span is None:
            return
        for key, value in build_genai_attributes(**fields).items():
            if key in _CONTENT_ATTRIBUTES and not self._capture_content:
                continue
            self._span.set_attribute(key, value)

    def set_error(self) -> None:
        """Mark the span's OTel status as ERROR — a refusal or an exception is a
        failed operation, not a successful-looking span (#193, AC #1/#4). A no-op
        without a live span, so telemetry-off / OTel-absent never crashes. The
        ``opentelemetry`` import is lazy and only reached when a real span
        exists, keeping the framework-free-core base install clean."""
        if self._span is None:
            return
        from opentelemetry.trace import Status, StatusCode

        self._span.set_status(Status(StatusCode.ERROR))

    def end(self) -> None:
        """End a DETACHED span (one from :func:`start_genai_span`). A no-op
        without a live span. Not to be called for a span owned by the
        :func:`genai_span` context manager — that ends it on block exit."""
        if self._span is None:
            return
        self._span.end()


@contextlib.contextmanager
def genai_span(*, enabled: bool, capture_content: bool = False) -> Iterator[GenAiSpan]:
    """The GenAI chat span (:data:`SPAN_LLM_CHAT`) for one governed model call.

    Yields a :class:`GenAiSpan` the caller records onto. Both the pinned
    ``gen_ai.*`` attributes and the stable ``donkey.*`` attributes go on this one
    span (AC #5). A no-op (yielding an inert handle, never raising) when
    telemetry is off or OpenTelemetry is not installed.

    ``capture_content`` carries ``telemetry_capture_content`` down to the yielded
    handle (#306); when ``False`` (default) the handle drops prompt/completion
    content before it reaches the span.

    The span is opened as a context manager so it closes on the way out even when
    the wrapped call raises before a response exists — a transport error escapes
    the transport's ``_finish`` hook, so the span lifecycle cannot rely on it
    (see ``core.transport.DonkeyAsyncClient._finish``, #179/#192).
    """
    if not enabled:
        yield GenAiSpan(None)
        return
    tracer = _tracer()
    if tracer is None:
        yield GenAiSpan(None)
        return
    with tracer.start_as_current_span(SPAN_LLM_CHAT) as sp:
        yield GenAiSpan(sp, capture_content=capture_content)


def start_genai_span(*, enabled: bool, capture_content: bool = False) -> GenAiSpan:
    """A DETACHED :data:`SPAN_LLM_CHAT` span the caller must :meth:`GenAiSpan.end`.

    Unlike :func:`genai_span` (a context manager that ends the span on block
    exit), this returns a live span whose lifetime is *not* bound to a ``with``
    block. That is what the streaming path needs: the span must outlive
    ``send()`` so the stream wrapper can fill ``gen_ai.usage.*`` from the terminal
    SSE event and end the span when the stream closes — on drain, abandonment, or
    exception (#193). Inert (an end-safe no-op handle, never raising) when
    telemetry is off or OpenTelemetry is not installed.

    The span is deliberately NOT made the current context: a streamed response is
    consumed long after ``send()`` returns, so there is no live scope to nest
    under. It records attributes and closes correctly, which is the whole of the
    span contract #193 requires.

    ``capture_content`` carries ``telemetry_capture_content`` to the returned
    handle (#306); when ``False`` (default) the handle drops prompt/completion
    content before it reaches the span."""
    if not enabled:
        return GenAiSpan(None)
    tracer = _tracer()
    if tracer is None:
        return GenAiSpan(None)
    return GenAiSpan(tracer.start_span(SPAN_LLM_CHAT), capture_content=capture_content)
