"""GenAI span contract (#192, BG §1.6): a pinned OTel gen_ai.* namespace and a
stable donkey.* namespace, dual-emitted on ONE span.

The acceptance bar this module encodes:

- The semantic-convention version is pinned in a single constant, and the keys
  are transcribed literals — not re-exported from the installed
  ``opentelemetry.semconv`` package — so upgrading that package never silently
  changes what we emit (AC #1/#2). Every gen_ai.* and donkey.* key has a test
  asserting the exact string (AC #3); the donkey.* keys are public API, so a
  rename breaks these tests loudly (AC #4).
- ``build_genai_attributes`` is the pure assembler both namespaces flow through;
  it omits any field left ``None`` so an unobserved value is absent, never a
  placeholder.
- ``genai_span`` puts both namespaces on the *same* span named
  :data:`SPAN_LLM_CHAT` (AC #5), and is an inert no-op when telemetry is off or
  OpenTelemetry is not installed — telemetry is never a hard dependency.

The dual-namespace span assertion needs the OTel SDK; it ``importorskip``s so
the base-only install skips it while the ``otel`` extra runs it in CI.
"""

from __future__ import annotations

import pytest

from donkey_kit.core import telemetry
from donkey_kit.core.errors import (
    AuthError,
    ContentSafetyBlocked,
    DonkeyError,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
    UpstreamModelError,
    UpstreamRequestError,
)

# --- the pinned semconv version + the literal keys (AC #1-#4) ---------------


def test_semconv_version_is_pinned_in_one_constant() -> None:
    # Bumping this is the deliberate single-file change AC #2 requires. If this
    # value changes, a CHANGELOG entry must accompany it.
    assert telemetry.GEN_AI_SEMCONV_VERSION == "1.30.0"


def test_gen_ai_keys_are_the_pinned_literal_strings() -> None:
    # Transcribed literals at the pinned version — NOT imported from
    # opentelemetry.semconv, so the installed package's default can drift without
    # changing what we emit (AC #1).
    assert telemetry.GEN_AI_SYSTEM == "gen_ai.system"
    assert telemetry.GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
    assert telemetry.GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
    assert telemetry.GEN_AI_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"


def test_donkey_keys_are_the_stable_public_literal_strings() -> None:
    # These are PUBLIC API (AC #4): renaming one is a breaking change, and this
    # test is the tripwire that makes that break loud.
    assert telemetry.DONKEY_CORRELATION_ID == "donkey.correlation_id"
    assert telemetry.DONKEY_POLICY_DECISION == "donkey.policy.decision"
    assert telemetry.DONKEY_POLICY_TYPE == "donkey.policy.type"
    assert telemetry.DONKEY_BUDGET_REMAINING == "donkey.budget.remaining"
    assert telemetry.DONKEY_COST_TEAM == "donkey.cost.team"
    assert telemetry.DONKEY_COST_PROJECT == "donkey.cost.project"
    assert telemetry.DONKEY_COST_ENV == "donkey.cost.env"
    assert telemetry.DONKEY_COST_ENDUSER == "donkey.cost.enduser.id"


def test_policy_decision_values_are_the_documented_literals() -> None:
    assert telemetry.POLICY_DECISION_ALLOW == "allow"
    assert telemetry.POLICY_DECISION_REFUSE == "refuse"


def test_routing_keys_are_the_pinned_and_stable_literal_strings() -> None:
    # The served model uses the pinned semconv key; the two routing facts the
    # semconv has no key for live under the stable donkey.* namespace (§3, #309).
    # gen_ai.response.model is pinned; the donkey.routing.* pair is public API.
    assert telemetry.GEN_AI_RESPONSE_MODEL == "gen_ai.response.model"
    assert telemetry.DONKEY_ROUTING_TYPE == "donkey.routing.type"
    assert telemetry.DONKEY_ROUTING_FALLBACK == "donkey.routing.fallback"


def test_routing_keys_are_allowlisted_for_the_generic_emitter() -> None:
    # They must be emittable — an allowlist-driven span drops anything not here.
    assert telemetry.GEN_AI_RESPONSE_MODEL in telemetry._ALLOWED_SPAN_ATTRIBUTES
    assert telemetry.DONKEY_ROUTING_TYPE in telemetry._ALLOWED_SPAN_ATTRIBUTES
    assert telemetry.DONKEY_ROUTING_FALLBACK in telemetry._ALLOWED_SPAN_ATTRIBUTES


# --- build_genai_attributes: the pure dual-namespace assembler --------------


def test_build_genai_attributes_emits_every_key_when_all_present() -> None:
    attrs = telemetry.build_genai_attributes(
        system="openai",
        request_model="gpt-4o",
        input_tokens=1420,
        output_tokens=310,
        decision=telemetry.POLICY_DECISION_ALLOW,
        policy_type="pii_detected",
        budget_remaining=18450,
        cost_team="support",
        cost_project="triage-v2",
        cost_env="prod",
        cost_enduser_id="user-42",
        correlation_id="run-7f3a",
    )
    assert attrs == {
        "gen_ai.system": "openai",
        "gen_ai.request.model": "gpt-4o",
        "gen_ai.usage.input_tokens": 1420,
        "gen_ai.usage.output_tokens": 310,
        "donkey.policy.decision": "allow",
        "donkey.policy.type": "pii_detected",
        "donkey.budget.remaining": 18450,
        "donkey.cost.team": "support",
        "donkey.cost.project": "triage-v2",
        "donkey.cost.env": "prod",
        "donkey.cost.enduser.id": "user-42",
        "donkey.correlation_id": "run-7f3a",
    }


def test_build_genai_attributes_omits_none_fields() -> None:
    # An unobserved value is absent, never emitted as a null/placeholder — a
    # request-only span (before the response) carries just the request model.
    attrs = telemetry.build_genai_attributes(request_model="gpt-4o")
    assert attrs == {"gen_ai.request.model": "gpt-4o"}


def test_build_genai_attributes_empty_when_nothing_observed() -> None:
    assert telemetry.build_genai_attributes() == {}


def test_build_genai_attributes_keeps_zero_token_counts() -> None:
    # 0 tokens is a real observation (an empty completion), distinct from None.
    attrs = telemetry.build_genai_attributes(input_tokens=0, output_tokens=0)
    assert attrs["gen_ai.usage.input_tokens"] == 0
    assert attrs["gen_ai.usage.output_tokens"] == 0


def test_build_genai_attributes_emits_the_routing_facts() -> None:
    # §3, #309: the served model, routing strategy and fallback flag land on the
    # span beside the request model.
    attrs = telemetry.build_genai_attributes(
        request_model="gpt-5.1",
        response_model="gpt-4o",
        routing_type="ModelBased",
        fallback=True,
    )
    assert attrs["gen_ai.request.model"] == "gpt-5.1"
    assert attrs["gen_ai.response.model"] == "gpt-4o"
    assert attrs["donkey.routing.type"] == "ModelBased"
    assert attrs["donkey.routing.fallback"] is True


def test_build_genai_attributes_emits_fallback_false_but_drops_none() -> None:
    # "We routed normally" (fallback=False) is a signal worth emitting on every
    # span; only an absent header (None) is dropped — a False must not vanish.
    assert telemetry.build_genai_attributes(fallback=False) == {"donkey.routing.fallback": False}
    assert telemetry.build_genai_attributes(fallback=None) == {}


# --- policy_type_slug: classified refusal -> donkey.policy.type -------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TokenBudgetExceeded("x", remediation="r"), "token_budget"),
        (PIIDetected("x", remediation="r"), "pii_detected"),
        (PromptInjectionBlocked("x", remediation="r"), "injection"),
        (ContentSafetyBlocked("x", remediation="r"), "content_safety"),
        (PolicyViolation("x", remediation="r"), "policy_violation"),
    ],
)
def test_policy_type_slug_maps_each_policy_violation(error: DonkeyError, expected: str) -> None:
    assert telemetry.policy_type_slug(error) == expected


@pytest.mark.parametrize(
    "error",
    [
        AuthError("x"),
        UpstreamRequestError("x"),
        UpstreamModelError("x"),
        DonkeyError("x"),
    ],
)
def test_policy_type_slug_is_none_for_non_policy_errors(error: DonkeyError) -> None:
    # Auth / upstream / transport failures carry no governance allow-or-refuse
    # decision, so they map to None — the transport omits the decision rather
    # than misreporting one.
    assert telemetry.policy_type_slug(error) is None


def test_policy_type_slug_prefers_the_most_specific_subclass() -> None:
    # PIIDetected is a PolicyViolation; the specific slug must win over the base.
    assert telemetry.policy_type_slug(PIIDetected("x", remediation="r")) == "pii_detected"


# --- genai_span: inert unless telemetry is on AND OTel is installed ----------


def test_genai_span_disabled_yields_an_inert_handle() -> None:
    with telemetry.genai_span(enabled=False) as gspan:
        # record() must be a safe no-op — no span, no crash.
        gspan.record(system="openai", request_model="gpt-4o", input_tokens=5)


def test_genai_span_without_otel_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    # Enabled, but OTel absent (tracer is None): still a no-op, never an error.
    monkeypatch.setattr(telemetry, "_tracer", lambda: None)
    with telemetry.genai_span(enabled=True) as gspan:
        gspan.record(system="openai", input_tokens=5)


# --- genai_span: both namespaces on ONE span (AC #5) ------------------------


def _in_memory_tracer():
    """A real OTel tracer wired to an in-memory exporter, or skip if the SDK
    (the ``otel`` extra) is not installed."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("donkey_kit.test"), exporter


def test_genai_span_puts_both_namespaces_on_one_span(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer, exporter = _in_memory_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    with telemetry.genai_span(enabled=True) as gspan:
        gspan.record(request_model="gpt-4o")  # recorded at span start
        gspan.record(  # recorded after the response settles
            system="openai",
            input_tokens=1420,
            output_tokens=310,
            decision=telemetry.POLICY_DECISION_ALLOW,
            budget_remaining=18450,
            correlation_id="run-7f3a",
        )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1  # AC #5: ONE span, never two
    span = spans[0]
    assert span.name == telemetry.SPAN_LLM_CHAT
    attrs = dict(span.attributes)
    # gen_ai.* (pinned) and donkey.* (stable) coexist on the same span.
    assert attrs["gen_ai.system"] == "openai"
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.usage.input_tokens"] == 1420
    assert attrs["gen_ai.usage.output_tokens"] == 310
    assert attrs["donkey.policy.decision"] == "allow"
    assert attrs["donkey.budget.remaining"] == 18450
    assert attrs["donkey.correlation_id"] == "run-7f3a"


def test_genai_span_records_a_refusal_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer, exporter = _in_memory_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    with telemetry.genai_span(enabled=True) as gspan:
        gspan.record(
            decision=telemetry.POLICY_DECISION_REFUSE,
            policy_type="token_budget",
        )

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["donkey.policy.decision"] == "refuse"
    assert attrs["donkey.policy.type"] == "token_budget"


# --- set_error: refusals/exceptions mark the span ERROR (#193, AC #1/#4) -----


def test_genai_span_set_error_is_inert_without_a_span() -> None:
    # Off / OTel absent → GenAiSpan(None): set_error() and end() are safe no-ops,
    # never a crash and never a hard OTel import.
    gspan = telemetry.GenAiSpan(None)
    gspan.set_error()
    gspan.end()


def test_genai_span_set_error_sets_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer, exporter = _in_memory_tracer()  # importorskips: base-only skips this test
    from opentelemetry.trace import StatusCode

    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    with telemetry.genai_span(enabled=True) as gspan:
        gspan.record(decision=telemetry.POLICY_DECISION_REFUSE, policy_type="pii_detected")
        gspan.set_error()

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR


# --- start_genai_span: a detached span the caller ends itself (#193) ---------
# Streaming needs the span to outlive send(): usage lands in the terminal SSE
# event, which the caller reads after send() has returned. So the streaming
# path opens a DETACHED span (not the auto-closing genai_span context manager)
# and hands it to the stream wrapper, which ends it when the stream closes.


def test_start_genai_span_disabled_is_inert() -> None:
    gspan = telemetry.start_genai_span(enabled=False)
    gspan.record(request_model="gpt-4o")
    gspan.set_error()
    gspan.end()  # no span, no crash


def test_start_genai_span_without_otel_is_inert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(telemetry, "_tracer", lambda: None)
    gspan = telemetry.start_genai_span(enabled=True)
    gspan.record(request_model="gpt-4o")
    gspan.end()


def test_start_genai_span_is_detached_and_ends_only_when_told(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer, exporter = _in_memory_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    gspan = telemetry.start_genai_span(enabled=True)
    gspan.record(request_model="gpt-4o", system="openai")
    # Detached: the span is live but NOT yet finished — nothing has ended it.
    assert exporter.get_finished_spans() == ()

    gspan.record(input_tokens=11, output_tokens=3)
    gspan.end()

    (span,) = exporter.get_finished_spans()  # ends exactly once, when the caller says
    assert span.name == telemetry.SPAN_LLM_CHAT
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.usage.input_tokens"] == 11
    assert attrs["gen_ai.usage.output_tokens"] == 3


# --- Content redaction boundary (#306, BG §1.6) -----------------------------
# Message content (prompt/completion) is emitted ONLY behind an explicit
# telemetry_capture_content opt-in, because spans are exported upstream of the
# gateway's PII masking. Two enforcement points: the generic span() allowlist
# (nothing content-shaped reaches a span by accident) and GenAiSpan.record's
# gate (content dropped unless the span was opened with capture_content=True).


def test_content_attribute_keys_are_the_pinned_literals() -> None:
    # Transcribed at the pinned semconv version, like the other gen_ai.* keys.
    assert telemetry.GEN_AI_PROMPT == "gen_ai.prompt"
    assert telemetry.GEN_AI_COMPLETION == "gen_ai.completion"


def test_content_attributes_are_gated_and_never_allowlisted() -> None:
    # The two content keys are the members of _CONTENT_ATTRIBUTES (the opt-in
    # set) and are deliberately absent from the generic-span allowlist, so no
    # call site can leak them through span().
    assert telemetry._CONTENT_ATTRIBUTES == frozenset(
        {telemetry.GEN_AI_PROMPT, telemetry.GEN_AI_COMPLETION}
    )
    assert not (telemetry._CONTENT_ATTRIBUTES & telemetry._ALLOWED_SPAN_ATTRIBUTES)


def test_build_genai_attributes_maps_prompt_and_completion_to_pinned_keys() -> None:
    # The builder maps content to the pinned keys; the opt-in gate lives in
    # GenAiSpan.record (its only caller), tested below.
    attrs = telemetry.build_genai_attributes(prompt="who is alice?", completion="alice is …")
    assert attrs[telemetry.GEN_AI_PROMPT] == "who is alice?"
    assert attrs[telemetry.GEN_AI_COMPLETION] == "alice is …"


def test_genai_span_drops_content_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer, exporter = _in_memory_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    # Default: capture_content is False. Content handed to record() is dropped;
    # the non-content metadata still lands.
    with telemetry.genai_span(enabled=True) as gspan:
        gspan.record(
            request_model="gpt-4o",
            prompt="my SSN is 000-00-0000",
            completion="I stored 000-00-0000",
        )

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert "gen_ai.prompt" not in attrs
    assert "gen_ai.completion" not in attrs


def test_genai_span_emits_content_only_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer, exporter = _in_memory_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    with telemetry.genai_span(enabled=True, capture_content=True) as gspan:
        gspan.record(
            request_model="gpt-4o",
            prompt="who is alice?",
            completion="alice is a user",
        )

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    # Emitted under the pinned semconv attribute names, and only those.
    assert attrs["gen_ai.prompt"] == "who is alice?"
    assert attrs["gen_ai.completion"] == "alice is a user"
    assert attrs["gen_ai.request.model"] == "gpt-4o"


def test_start_genai_span_honours_the_content_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # The streaming path (detached span) gates content the same way.
    tracer, exporter = _in_memory_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    off = telemetry.start_genai_span(enabled=True)
    off.record(prompt="secret", completion="secret")
    off.end()
    on = telemetry.start_genai_span(enabled=True, capture_content=True)
    on.record(prompt="visible", completion="visible")
    on.end()

    off_span, on_span = exporter.get_finished_spans()
    assert "gen_ai.prompt" not in dict(off_span.attributes)
    assert dict(on_span.attributes)["gen_ai.prompt"] == "visible"


def test_generic_span_allowlist_drops_content_and_unknown_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer, exporter = _in_memory_tracer()
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)

    # A rogue content attribute AND an unrecognised key handed to the generic
    # span() are both dropped by the allowlist; an allowlisted key survives.
    # run_context scopes the correlation binding so it resets on exit (span()
    # calls ensure_correlation_id(), which would otherwise pin an ambient ID).
    with telemetry.run_context("run-allowlist"), telemetry.span(
        telemetry.SPAN_TOOL_CALL,
        enabled=True,
        **{
            telemetry.GEN_AI_PROMPT: "leak me",
            "some.unknown.key": "also dropped",
            telemetry.GEN_AI_REQUEST_MODEL: "gpt-4o",
        },
    ):
        pass

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes)
    assert "gen_ai.prompt" not in attrs
    assert "some.unknown.key" not in attrs
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert "donkey.correlation_id" in attrs  # always attached
