"""Telemetry (BG §1.6) and the run-scoped correlation ID (BG §1.1).

OpenTelemetry is an optional dependency (the ``[otel]`` extra). Telemetry is
**on by default** (``DonkeyConfig.telemetry``), but the export pipeline is
**inert unless an OTLP endpoint is configured**: :func:`configure_otlp_export`
builds a real exporter only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` (or the
traces-specific variant) is set — so a process with no endpoint produces no
spans, connects to nothing, and prints nothing (BG §1.6 "inert and silent",
#194). That exporter rides a **DDK-scoped** ``TracerProvider``; the
process-global provider is never set unless the caller opts in with
``telemetry_install_global`` (#732), so a host that configures its own provider
later still gets DDK's spans. This is what makes the zero-config promise hold: set the standard OTel
endpoint env var and spans flow to your sink with **no SDK-specific env var**.
Opt out of telemetry entirely with the single flag ``DONKEY_TELEMETRY=false``.

The correlation ID lives in a ``contextvar`` so a single agent run's fan-out of
model calls and tool calls shares one trace ID end to end — letting a developer
correlate their local trace with what the platform team sees in Omni Gateway's
observability view (a headline feature, BG §1.6). That state is defined in
:mod:`donkey_kit.core.correlation`, which the transport's header policy reads
directly, and is re-exported here so these import paths are unchanged (#728).
"""

from __future__ import annotations

import contextlib
import logging
import os
import warnings
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

# The run-scoped correlation state lives in ``core.correlation`` (#728); these
# names are re-exported so ``donkey_kit.core.telemetry.<name>`` keeps working.
from .correlation import (
    RunScope,
    current_correlation_id,
    current_cost_tags,
    new_call_id,
    new_correlation_id,
    request_correlation_id,
    run_context,
    run_scope,
)

if TYPE_CHECKING:
    from .config import DonkeyConfig
from .errors import (
    AgentKilled,
    ContentSafetyBlocked,
    DonkeyError,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
)

__all__ = [
    "DONKEY_BUDGET_REMAINING",
    "DONKEY_CACHE_SCORE",
    "DONKEY_CACHE_STATUS",
    "DONKEY_CORRELATION_ID",
    "DONKEY_COST_ENDUSER",
    "DONKEY_COST_ENV",
    "DONKEY_COST_PROJECT",
    "DONKEY_COST_TEAM",
    "DONKEY_POLICY_DECISION",
    "DONKEY_POLICY_TYPE",
    "DONKEY_ROUTING_FALLBACK",
    "DONKEY_ROUTING_MATCHED_TOPIC",
    "DONKEY_ROUTING_SCORE",
    "DONKEY_ROUTING_TYPE",
    "DONKEY_USAGE_CACHED_TOKENS",
    "DONKEY_USAGE_CACHE_WRITE_TOKENS",
    "DONKEY_USAGE_REASONING_TOKENS",
    "GEN_AI_COMPLETION",
    "GEN_AI_PROMPT",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_RESPONSE_MODEL",
    "GEN_AI_SEMCONV_VERSION",
    "GEN_AI_SYSTEM",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "POLICY_DECISION_ALLOW",
    "POLICY_DECISION_REFUSE",
    "SPAN_LLM_CHAT",
    "GenAiSpan",
    "RunScope",
    "TelemetryExportWarning",
    "build_genai_attributes",
    "configure_otlp_export",
    "current_correlation_id",
    "current_cost_tags",
    "genai_span",
    "new_call_id",
    "new_correlation_id",
    "policy_type_slug",
    "request_correlation_id",
    "run_context",
    "run_scope",
    "start_genai_span",
]

_log = logging.getLogger(__name__)


# Span name constants (BG §1.6).
SPAN_LLM_CHAT = "donkey.llm.chat"

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
# dropped by :meth:`GenAiSpan.record` unless the caller opted in.
GEN_AI_PROMPT = "gen_ai.prompt"
GEN_AI_COMPLETION = "gen_ai.completion"

# donkey.* — stable public API (renaming a value here is a breaking change).
DONKEY_CORRELATION_ID = "donkey.correlation_id"
DONKEY_POLICY_DECISION = "donkey.policy.decision"
DONKEY_POLICY_TYPE = "donkey.policy.type"
DONKEY_BUDGET_REMAINING = "donkey.budget.remaining"
# Cost-attribution dimensions (docs/verified-apis.md §3, BG §1.7, #196). One
# donkey.cost.* attribute per fixed dimension; ``enduser.id`` keeps its dotted
# external name. These carry the full value even while the request-header
# names are UNVERIFIED (docs/verified-apis.md §3), for end-to-end attribution.
DONKEY_COST_TEAM = "donkey.cost.team"
DONKEY_COST_PROJECT = "donkey.cost.project"
DONKEY_COST_ENV = "donkey.cost.env"
DONKEY_COST_ENDUSER = "donkey.cost.enduser.id"
# Gateway routing & resilience (docs/verified-apis.md §3, #309). The served
# provider already lands on ``gen_ai.system`` and the served model on
# ``gen_ai.response.model``; these two carry the gateway-specific routing facts
# the semconv has no key for. Emitted even when ``fallback`` is ``False`` — "we
# routed normally" is a signal an operator wants on every span, not just the
# failover ones.
DONKEY_ROUTING_TYPE = "donkey.routing.type"
DONKEY_ROUTING_FALLBACK = "donkey.routing.fallback"
# Semantic-routing match detail (docs/verified-apis.md §3, #590). Populated only
# on a semantic-routing proxy (``routing_type == "Semantic"``), absent on
# model-based routing — the topic/score explain what ``donkey.routing.type`` leaves opaque.
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
# Add a new content-bearing attribute (e.g. tool-call args/results) HERE, so
# the single switch keeps covering it.
_CONTENT_ATTRIBUTES = frozenset({GEN_AI_PROMPT, GEN_AI_COMPLETION})


# --- Optional OpenTelemetry span helper -------------------------------------
def _tracer() -> Any | None:
    """DDK's tracer, resolved per span (#732).

    A global provider the host has set always wins, even one set after
    ``Donkey()``: OTel's default proxy provider forwards to it. Only while no
    global provider is set do spans go to the DDK-scoped provider that
    :func:`configure_otlp_export` built from an OTLP endpoint. With neither, the
    default proxy tracer records nothing."""
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    if _scoped_tracer is not None and not _host_provider_set():
        return _scoped_tracer
    return trace.get_tracer("donkey_kit")


def _host_provider_set() -> bool:
    """True once anything has set the process-global ``TracerProvider``.

    Until then OTel hands out its placeholder ``ProxyTracerProvider``; any other
    provider (an SDK one, ``opentelemetry-instrument``'s, or a deliberate
    ``NoOpTracerProvider``) is the host's choice and DDK defers to it."""
    from opentelemetry import trace

    return not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider)


# --- Zero-config OTLP export bootstrap (BG §1.6, #194) -----------------------
# The span helpers above get their tracer from ``_tracer()``, which rides
# whatever global TracerProvider the host process installed, or the DDK-scoped
# provider built below. Without either they export nothing: OpenTelemetry's
# default is a no-op provider. This is what turns "we build spans" into "spans
# reach the customer's sink", with the zero-config contract of BG §1.6:
#   set OTEL_EXPORTER_OTLP_ENDPOINT (the *standard* OTel env var) → spans export.
#   no SDK-specific env var, and no endpoint set → inert and silent.
# The provider it builds is DDK-scoped: it carries DDK's own spans and is never
# made the process-global provider unless ``telemetry_install_global`` is set.
# OTel lets the global provider be set only once, so taking it implicitly would
# silently lock out a host that configures its own afterwards (#732,
# docs/adr/0010-no-hidden-global-side-effects.md). Export I/O runs on the
# BatchSpanProcessor's background thread, off the request hot path — which is
# exactly why per-call overhead stays under the 1ms bar (benchmarked in CI,
# #194): the call site only creates the span, sets attributes and enqueues;
# the network flush is somebody else's thread.


class TelemetryExportWarning(UserWarning):
    """Emitted once when an OTLP endpoint is configured but the exporter it needs
    is not installed — so the caller asked for export and would otherwise get
    silence. Never raised when no endpoint is set (that path is inert by design).
    """


# Guards ``configure_otlp_export`` to build at most one provider per process
# (one exporter, one batch thread). Reset by `_reset_for_tests` below.
_otlp_export_configured = False
# The DDK-scoped provider's tracer, set when an endpoint is configured and the
# global provider was neither set by the host nor installed by opt-in.
_scoped_tracer: Any | None = None
# One-time de-dupe for the missing-exporter warning, keyed by protocol.
_warned_missing_exporter: set[str] = set()


def _reset_for_tests() -> None:
    """Drop the three flags above so no test's export config outlives it (#750)."""
    global _otlp_export_configured, _scoped_tracer
    _otlp_export_configured = False
    _scoped_tracer = None
    _warned_missing_exporter.clear()


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
    """Wire a zero-config OTLP exporter for this process, once (BG §1.6, #194).

    Called from ``Runtime.__init__``. Inert and silent (AC #4) when telemetry is
    off, when no OTLP endpoint env var is set, or when ``[otel]`` is not
    installed. When an endpoint *is* set, builds a ``TracerProvider`` + OTLP
    ``BatchSpanProcessor`` and:

    - if a host already set the global provider (``opentelemetry-instrument``, a
      manual setup), drops it and lets DDK's spans ride the host's;
    - else, with ``telemetry_install_global`` on, installs it as the global
      provider (the pre-#732 behaviour, now opt-in);
    - else keeps it DDK-scoped: DDK's spans export through it and
      ``trace.get_tracer_provider()`` is left unchanged, so a host provider set
      later still wins and receives DDK's spans (#732).

    Idempotent: guarded so repeated ``Donkey()`` construction builds at most
    one provider per process."""
    global _otlp_export_configured, _scoped_tracer
    if _otlp_export_configured:
        return
    provider = _build_tracer_provider(config)
    if provider is None:
        # inert: opt-out, no endpoint, or [otel]/exporter absent. DEBUG only, and
        # silent behind the package NullHandler, so "inert and silent" holds.
        _log.debug(
            "OTLP export not installed (telemetry=%s, endpoint configured=%s)",
            config.telemetry,
            _otlp_endpoint_configured(),
        )
        return
    from opentelemetry import trace

    _otlp_export_configured = True
    if _host_provider_set():
        # A host already owns the global provider — never clobber it. Drop the
        # one we just built so its batch thread does not linger unused.
        provider.shutdown()
        _log.debug("OTLP export: keeping the host's TracerProvider; spans ride it")
        return
    if config.telemetry_install_global:
        trace.set_tracer_provider(provider)
        _log.debug("OTLP export installed as the global provider (protocol %s)", _otlp_protocol())
        return
    _scoped_tracer = provider.get_tracer("donkey_kit")
    _log.debug("OTLP export installed on a DDK-scoped provider (protocol %s)", _otlp_protocol())


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
    if isinstance(error, AgentKilled):
        return "agent_killed"
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
