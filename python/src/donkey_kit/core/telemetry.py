"""Telemetry (§2.5) and the run-scoped correlation ID (§2.3).

OpenTelemetry is an optional dependency, off by default in the library and on by
default in the CLI. The correlation ID lives in a ``contextvar`` so a single
agent run's fan-out of model calls and tool calls shares one trace ID end to
end — letting a developer correlate their local trace with what the platform
team sees in Omni Gateway's observability view (a headline feature, §2.5).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

from .cost import CostTags
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

# Span name constants (§2.5).
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
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

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
# Cost-attribution dimensions (§3, BG §1.7, #196). One donkey.cost.* attribute
# per fixed dimension; ``enduser.id`` keeps its dotted external name. These
# carry the full value on the span even while the request-header names are
# UNVERIFIED (docs §3), so per-dimension spend attribution works end to end.
DONKEY_COST_TEAM = "donkey.cost.team"
DONKEY_COST_PROJECT = "donkey.cost.project"
DONKEY_COST_ENV = "donkey.cost.env"
DONKEY_COST_ENDUSER = "donkey.cost.enduser.id"

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
        GEN_AI_USAGE_INPUT_TOKENS,
        GEN_AI_USAGE_OUTPUT_TOKENS,
        DONKEY_CORRELATION_ID,
        DONKEY_POLICY_DECISION,
        DONKEY_POLICY_TYPE,
        DONKEY_BUDGET_REMAINING,
        DONKEY_COST_TEAM,
        DONKEY_COST_PROJECT,
        DONKEY_COST_ENV,
        DONKEY_COST_ENDUSER,
    }
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def new_call_id() -> str:
    """A fresh per-request **call id** (§2.3, #195).

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
    to :data:`_correlation_id` for the block (§2.3, #195).

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
    behind ``donkey.run(id=...)`` (§2.3, #195), optionally carrying per-run
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
    input_tokens: int | None = None,
    output_tokens: int | None = None,
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
    if input_tokens is not None:
        attrs[GEN_AI_USAGE_INPUT_TOKENS] = input_tokens
    if output_tokens is not None:
        attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = output_tokens
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
