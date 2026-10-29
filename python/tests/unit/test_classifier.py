"""``classify.register()``: custom gateway policies as typed exceptions (BG §2.2, #210).

Covers every form in the issue's "Registration reference": each match key, the
Gemini list envelope, numeric codes, patterns and dotted paths; specificity and
ties; every row of the rejection table; the never-echoed message; handlers and
the span slug; and the simulator / conformance fallback that serves a
registered type with no hand-written body.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

import donkey_kit
from donkey_kit import Donkey, DonkeyConfig, PolicyViolation, RefusalContext
from donkey_kit._testing import isolated_classify
from donkey_kit.core import _policy_spec, classifier, errors, telemetry
from donkey_kit.core._refusal_body import code_str
from donkey_kit.core.classifier import PolicyRegistration, classify
from donkey_kit.core.errors import (
    AgentKilled,
    ModelNotRoutable,
    PIIDetected,
    UpstreamRequestError,
)
from donkey_kit.simulator.inject import resolve_fixture

_PYTHON = Path(__file__).resolve().parents[2]


class AcmePolicyViolation(PolicyViolation):
    remediation = "An Acme data-handling policy refused this request. See go/acme-policies."


class SalaryLeakBlocked(AcmePolicyViolation):
    remediation = "Salary figures may not leave HR. Ask the user to open the HR portal."
    policy = "acme_salary_block"


class CustomerPIIBlocked(AcmePolicyViolation):
    remediation = "Customer identifiers must be tokenised before this call."
    policy = "acme_customer_pii"


class AcmeBlocked(AcmePolicyViolation):
    remediation = "An Acme policy refused this request."
    policy = "acme_generic_block"


class Unslugged(PolicyViolation):
    """Sets no policy: the slug comes from the error_type / error_code value."""

    remediation = "Ask an Acme admin which policy refused the call."


def _resp(
    status: int,
    body: object = None,
    *,
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> httpx.Response:
    request = httpx.Request("POST", "https://proxy/chat/completions")
    if content is not None:
        return httpx.Response(status, headers=headers, content=content, request=request)
    if body is None:
        return httpx.Response(status, headers=headers, request=request)
    return httpx.Response(status, headers=headers, json=body, request=request)


# --- every registration form (reference 1-8) ----------------------------------


def test_nested_error_type_with_a_class_policy() -> None:
    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )
    err = classify(_resp(403, {"error": {"type": "acme_salary_block", "message": "182,000"}}))
    assert type(err) is SalaryLeakBlocked
    assert err.policy == "acme_salary_block"


def test_nested_error_type_is_the_slug_when_the_class_sets_none() -> None:
    reg = classify.register(match={"status": 403, "error_type": "acme_salary"}, raises=Unslugged)
    assert reg.policy == "acme_salary"
    err = classify(_resp(403, {"error": {"type": "acme_salary"}}))
    assert type(err) is Unslugged
    assert err.policy == "acme_salary"


def test_error_type_reads_gemini_status_on_the_list_envelope() -> None:
    classify.register(
        match={"status": 403, "error_type": "ACME_SALARY_BLOCK"}, raises=SalaryLeakBlocked
    )
    body = [{"error": {"code": 403, "status": "ACME_SALARY_BLOCK", "message": "no"}}]
    assert type(classify(_resp(403, body))) is SalaryLeakBlocked


def test_an_upper_case_error_type_needs_a_class_policy() -> None:
    with pytest.raises(ValueError, match="not a valid policy name"):
        classify.register(match={"status": 403, "error_type": "ACME_BLOCK"}, raises=Unslugged)


def test_nested_error_code() -> None:
    reg = classify.register(match={"status": 403, "error_code": "acme_code"}, raises=Unslugged)
    assert reg.policy == "acme_code"
    assert type(classify(_resp(403, {"error": {"code": "acme_code"}}))) is Unslugged


def test_a_numeric_code_is_matched_as_its_string() -> None:
    classify.register(match={"status": 403, "error_code": "4031"}, raises=SalaryLeakBlocked)
    assert type(classify(_resp(403, {"error": {"code": 4031}}))) is SalaryLeakBlocked


def test_a_numeric_error_code_in_the_spec_is_a_type_error() -> None:
    with pytest.raises(TypeError, match="error_code"):
        classify.register(match={"status": 403, "error_code": 4031}, raises=SalaryLeakBlocked)


def test_header_marker_only_names_are_case_insensitive_values_exact() -> None:
    classify.register(
        match={"status": 403, "headers": {"X-Acme-Policy": "salary-block"}},
        raises=SalaryLeakBlocked,
    )
    hit = _resp(403, {"message": "nope"}, headers={"x-acme-policy": "salary-block"})
    assert type(classify(hit)) is SalaryLeakBlocked
    miss = _resp(403, {"message": "nope"}, headers={"x-acme-policy": "SalaryLeakBlocked-Block"})
    assert type(classify(miss)) is not SalaryLeakBlocked


def test_several_headers_are_anded() -> None:
    headers = {"x-acme-policy": "salary-block", "x-acme-action": "reject"}
    classify.register(match={"status": 403, "headers": headers}, raises=SalaryLeakBlocked)
    assert type(classify(_resp(403, headers=headers))) is SalaryLeakBlocked
    assert (
        type(classify(_resp(403, headers={"x-acme-policy": "salary-block"})))
        is not SalaryLeakBlocked
    )


def test_a_header_only_match_needs_a_class_policy() -> None:
    with pytest.raises(ValueError, match="no natural slug"):
        classify.register(match={"status": 403, "headers": {"x-acme": "1"}}, raises=Unslugged)


def test_flat_string_body_is_an_exact_match_never_a_substring() -> None:
    body = {"error": "Salary figures may not leave HR"}
    classify.register(match={"status": 451, "body": body}, raises=SalaryLeakBlocked)
    assert type(classify(_resp(451, body))) is SalaryLeakBlocked
    longer = {"error": "Salary figures may not leave HR, ever"}
    assert type(classify(_resp(451, longer))) is not SalaryLeakBlocked


def test_a_compiled_pattern_must_match_the_whole_string_value() -> None:
    pattern = re.compile(r"Salary figures for employee \d+ blocked")
    classify.register(match={"status": 403, "body": {"error": pattern}}, raises=SalaryLeakBlocked)
    hit = {"error": "Salary figures for employee 4411 blocked"}
    assert type(classify(_resp(403, hit))) is SalaryLeakBlocked
    tail = {"error": "Salary figures for employee 4411 blocked today"}
    assert type(classify(_resp(403, tail))) is not SalaryLeakBlocked
    assert type(classify(_resp(403, {"error": 4411}))) is not SalaryLeakBlocked  # strings only


def test_dotted_body_paths_resolve_object_keys_only() -> None:
    body = {"policy.id": "salary-guard", "policy.action": "reject"}
    classify.register(match={"status": 403, "body": body}, raises=SalaryLeakBlocked)
    response = {"policy": {"id": "salary-guard", "action": "reject"}, "detail": "x"}
    assert type(classify(_resp(403, response))) is SalaryLeakBlocked
    listed = {"policy": [{"id": "salary-guard", "action": "reject"}]}
    assert type(classify(_resp(403, listed))) is not SalaryLeakBlocked


@pytest.mark.parametrize(
    ("spec", "value"),
    [(False, "false"), (7, "7"), ("7", 7), (True, 1), (1, True)],
)
def test_body_comparison_is_type_strict(spec: object, value: object) -> None:
    classify.register(match={"status": 403, "body": {"flag": spec}}, raises=SalaryLeakBlocked)
    assert type(classify(_resp(403, {"flag": spec}))) is SalaryLeakBlocked
    assert type(classify(_resp(403, {"flag": value}))) is not SalaryLeakBlocked


def test_more_conditions_win() -> None:
    marker = {"x-acme-policy": "block"}
    classify.register(match={"status": 403, "headers": marker}, raises=AcmeBlocked)
    classify.register(
        match={"status": 403, "headers": marker, "error_code": "salary"}, raises=SalaryLeakBlocked
    )
    salary = _resp(403, {"error": {"code": "salary"}}, headers=marker)
    assert type(classify(salary)) is SalaryLeakBlocked
    other = _resp(403, {"error": {"code": "other"}}, headers=marker)
    assert type(classify(other)) is AcmeBlocked


def test_a_tie_is_the_generic_refusal_naming_both_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    classify.register(match={"status": 403, "headers": {"x-a": "1"}}, raises=AcmeBlocked)
    classify.register(match={"status": 403, "headers": {"x-b": "1"}}, raises=SalaryLeakBlocked)
    tied = _resp(403, headers={"x-a": "1", "x-b": "1"})
    with caplog.at_level(logging.WARNING, logger="donkey_kit.core.classifier"):
        first = classify(tied)
        second = classify(tied)
    for err in (first, second):
        assert type(err) is PolicyViolation
        assert err.policy == "unknown"
        assert "AcmeBlocked (acme_generic_block)" in err.remediation
        assert "SalaryLeakBlocked (acme_salary_block)" in err.remediation
    assert len([r for r in caplog.records if "registrations match" in r.message]) == 1


# --- precedence: after the built-in policy rules, before the envelope ---------


def test_a_custom_403_is_no_longer_an_upstream_request_error() -> None:
    response = _resp(403, {"error": {"type": "acme_salary_block", "message": "x"}})
    assert type(classify(response)) is UpstreamRequestError
    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )
    assert type(classify(response)) is SalaryLeakBlocked


def test_a_built_in_shape_still_wins() -> None:
    classify.register(
        match={"status": 403, "body": {"error.message": "pii"}}, raises=SalaryLeakBlocked
    )
    pii = _resp(403, {"error": {"type": "pii_detected", "message": "pii"}})
    assert type(classify(pii)) is PIIDetected


def test_an_uncaptured_vendor_header_can_be_registered() -> None:
    # #1429: CrowdStrike AIDR is not a built-in rule until its shape is captured
    # (#1184), so a caller who has seen it on their own proxy can type it.
    class AidrBlocked(PolicyViolation):
        remediation = "CrowdStrike AIDR blocked this request."
        policy = "acme_aidr"

    headers = {"x-llm-proxy-crowdstrike-aidr-action": "reject"}
    response = _resp(403, {"error": "Content blocked"}, headers=headers)
    assert type(classify(response)) is PolicyViolation
    classify.register(match={"status": 403, "headers": headers}, raises=AidrBlocked)
    assert type(classify(response)) is AidrBlocked


def test_model_not_routable_and_upstream_request_stay_disjoint() -> None:
    classify.register(match={"status": 404, "error_type": "acme_route"}, raises=SalaryLeakBlocked)
    routable = _resp(404, {"error": {"type": "not_found", "message": "model x not found"}})
    assert type(classify(routable)) is not SalaryLeakBlocked
    assert type(classify(_resp(404, {"error": {"type": "acme_route"}}))) is SalaryLeakBlocked


def test_errors_classify_and_donkey_classify_honour_registrations() -> None:
    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )
    response = _resp(403, {"error": {"type": "acme_salary_block"}})
    assert type(errors.classify(response)) is SalaryLeakBlocked
    assert Donkey.classify.fget is not None
    with Donkey(DonkeyConfig(llm_proxy_url="https://proxy")) as donkey:
        assert donkey.classify is donkey_kit.classify is classify
        assert type(donkey.classify(response)) is SalaryLeakBlocked


# --- the raised error ---------------------------------------------------------


def test_the_message_never_echoes_gateway_text_but_response_keeps_it() -> None:
    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )
    response = _resp(
        403,
        {"error": {"type": "acme_salary_block", "message": "Outbound salary figure: 182,000"}},
        headers={"x-correlation-id": "c-1"},
    )
    err = classify(response, correlation_id="c-1", call_id="k-1")
    assert str(err) == "Request refused by custom policy acme_salary_block (403)."
    assert "182,000" not in str(err)
    assert err.remediation == SalaryLeakBlocked.remediation
    assert err.response is response
    assert "182,000" in err.response.text
    assert (err.correlation_id, err.call_id) == ("c-1", "k-1")


# --- rejected at registration time (reference 13) -----------------------------

_REJECTED: list[tuple[dict[str, Any], type[Exception], str]] = [
    ({"status": 403}, ValueError, "no discriminator"),
    ({"status": 403, "error_typ": "x"}, TypeError, "error_typ"),
    ({"error_type": "acme_x"}, TypeError, "status"),
    ({"status": "403", "error_type": "acme_x"}, TypeError, "status"),
    ({"status": 401, "error_type": "acme_x"}, ValueError, "401"),
    ({"status": 429, "error_type": "acme_x"}, ValueError, "429"),
    ({"status": 500, "error_type": "acme_x"}, ValueError, "4xx"),
    ({"status": 403, "error_type": "pii_detected"}, ValueError, "PIIDetected"),
    ({"status": 403, "error_code": "agent_killed"}, ValueError, "AgentKilled"),
    ({"status": 403, "body": {"error": "access_denied"}}, ValueError, "GatewayApprovalDenied"),
    (
        {"status": 403, "headers": {"x-injection-protection": "blocked"}},
        ValueError,
        "PromptInjectionBlocked",
    ),
    ({"status": 403, "headers": {"www-authenticate": "Bearer"}}, ValueError, "AuthError"),
    ({"status": 400, "error_type": "invalid_request_error"}, ValueError, "provider"),
    ({"status": 404, "error_code": "model_not_found"}, ValueError, "built-in|provider"),
    (
        {"status": 403, "body": {"error": "x", "error.type": "acme_x"}},
        ValueError,
        "Contradictory",
    ),
    ({"status": 403, "error_type": ""}, TypeError, "error_type"),
    ({"status": 403, "body": {"x": 1.5}}, TypeError, "body"),
    ({"status": 403, "body": {"x": re.compile(rb"x")}}, TypeError, "body"),
]


@pytest.mark.parametrize(("match", "error", "message"), _REJECTED)
def test_a_bad_spec_is_rejected_at_registration(
    match: dict[str, Any], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        classify.register(match=match, raises=SalaryLeakBlocked)
    assert classify.registered() == ()


def test_a_duplicate_spec_is_rejected() -> None:
    classify.register(match={"status": 403, "error_type": "acme_x"}, raises=SalaryLeakBlocked)
    with pytest.raises(ValueError, match="Duplicate"):
        classify.register(match={"status": 403, "error_type": "acme_x"}, raises=SalaryLeakBlocked)


def test_one_slug_for_two_classes_is_rejected() -> None:
    classify.register(match={"status": 403, "error_type": "acme_x"}, raises=Unslugged)

    class Other(PolicyViolation):
        remediation = "Ask an Acme admin."
        policy = "acme_x"

    with pytest.raises(ValueError, match="already used by Unslugged"):
        classify.register(match={"status": 403, "error_code": "acme_y"}, raises=Other)


def test_one_class_may_have_several_shapes() -> None:
    classify.register(match={"status": 403, "error_type": "acme_a"}, raises=SalaryLeakBlocked)
    classify.register(match={"status": 451, "error_type": "acme_a"}, raises=SalaryLeakBlocked)
    assert len(classify.registered()) == 2


def test_a_built_in_slug_is_rejected() -> None:
    class Named(PolicyViolation):
        remediation = "r"
        policy = "pii_detected"

    with pytest.raises(ValueError, match="built-in policy name"):
        classify.register(match={"status": 403, "error_type": "acme_x"}, raises=Named)


class _NoRemediation(PolicyViolation):
    policy = "acme_none"


class _KillSubclass(AgentKilled):
    remediation = "r"


class _PIISubclass(PIIDetected):
    remediation = "r"


@pytest.mark.parametrize(
    ("raises", "message"),
    [
        (PolicyViolation, "your own PolicyViolation subclass"),
        (_NoRemediation, "remediation"),
        (_KillSubclass, "AgentKilled"),
        (_PIISubclass, "PIIDetected"),
        (ValueError, "PolicyViolation subclass"),
        (ModelNotRoutable, "PolicyViolation subclass"),
    ],
)
def test_an_unregistrable_exception_is_a_type_error(raises: Any, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        classify.register(match={"status": 403, "error_type": "acme_x"}, raises=raises)


def test_an_exception_whose_constructor_refuses_the_classifier_is_a_type_error() -> None:
    class Strict(PolicyViolation):
        remediation = "r"
        policy = "acme_strict"

        def __init__(self, message: str) -> None:
            super().__init__(message)

    with pytest.raises(TypeError, match="Strict"):
        classify.register(match={"status": 403, "error_type": "acme_x"}, raises=Strict)


def test_an_unsatisfiable_pattern_is_rejected() -> None:
    with pytest.raises(ValueError, match="synthesise|Contradictory"):
        classify.register(
            match={"status": 403, "body": {"error": re.compile(r"(?=a)b")}},
            raises=SalaryLeakBlocked,
        )


# --- introspection and removal (reference 12) ---------------------------------


def test_registered_and_unregister() -> None:
    reg = classify.register(match={"status": 403, "error_type": "acme_x"}, raises=SalaryLeakBlocked)
    assert isinstance(reg, PolicyRegistration)
    assert classify.registered() == (reg,)
    assert repr(classify) == "<donkey_kit.classify: 1 registered>"
    with pytest.raises(AttributeError):
        reg.status = 404  # type: ignore[misc]
    assert classify.unregister(SalaryLeakBlocked) == (reg,)
    assert classify.registered() == ()
    assert classify.unregister(SalaryLeakBlocked) == ()
    response = _resp(403, {"error": {"type": "acme_x"}})
    assert type(classify(response)) is UpstreamRequestError


def test_a_client_built_before_a_registration_sees_it() -> None:
    pytest.importorskip("openai")
    with Donkey(_cfg()) as donkey:
        llm = donkey.openai(sync=True)
        classify.register(
            match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
        )
        with donkey.simulate(SalaryLeakBlocked), pytest.raises(SalaryLeakBlocked):
            with donkey.run():
                llm.chat.completions.create(
                    model="gpt-4o", messages=[{"role": "user", "content": "x"}]
                )


def test_isolated_classify_keeps_earlier_and_drops_later_registrations() -> None:
    kept = classify.register(
        match={"status": 403, "error_type": "acme_x"}, raises=SalaryLeakBlocked
    )
    with isolated_classify() as registry:
        assert registry is classify
        registry.register(match={"status": 403, "error_type": "acme_y"}, raises=Unslugged)
        assert len(classify.registered()) == 2
    assert classify.registered() == (kept,)


def test_the_reset_seam_empties_the_registry() -> None:
    classify.register(match={"status": 403, "error_type": "acme_x"}, raises=SalaryLeakBlocked)
    classifier._reset_for_tests()
    assert classify.registered() == ()


# --- example synthesis --------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    [
        r"Salary figures for employee \d+ blocked",
        r"[A-Z]{3}-\d{2,4}",
        r"(foo|bar)baz?",
        r"\w+@\w+\.com",
        r"x[^a-z]y",
        r"a.b",
        r"(?:ab)+c*",
        r"\s?ok\S",
    ],
)
def test_a_pattern_samples_to_a_string_it_fullmatches(pattern: str) -> None:
    compiled = re.compile(pattern)
    reg = classify.register(
        match={"status": 403, "body": {"m": compiled}}, raises=SalaryLeakBlocked
    )
    sample = json.loads(reg.example().content)["m"]
    assert compiled.fullmatch(sample), sample


def test_the_example_is_the_minimal_matching_response() -> None:
    reg = classify.register(
        match={
            "status": 403,
            "error_type": "acme_x",
            "error_code": "c1",
            "headers": {"x-acme": "1"},
            "body": {"error.rule": 7},
        },
        raises=SalaryLeakBlocked,
    )
    example = reg.example()
    assert example.status_code == 403
    assert example.headers["x-acme"] == "1"
    assert json.loads(example.content) == {"error": {"type": "acme_x", "code": "c1", "rule": 7}}
    assert reg.conditions == 4


# --- handlers and spans (reference 11) ----------------------------------------


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


def _chat(donkey: Donkey) -> None:
    llm = donkey.openai(sync=True)
    llm.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": "x"}])


@pytest.mark.parametrize("target", [SalaryLeakBlocked, AcmePolicyViolation, PolicyViolation])
def test_a_handler_on_the_type_its_family_or_the_catch_all_runs(target: type) -> None:
    pytest.importorskip("openai")
    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )
    seen: list[PolicyViolation] = []

    def record(ctx: RefusalContext) -> None:
        seen.append(ctx.violation)

    with Donkey(_cfg()) as donkey:
        donkey.on(target).call(record)
        with donkey.simulate(SalaryLeakBlocked), pytest.raises(SalaryLeakBlocked):
            with donkey.run():
                _chat(donkey)
    assert [type(v) for v in seen] == [SalaryLeakBlocked]
    assert seen[0].policy == "acme_salary_block"


def test_the_span_records_the_slug_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("openai")
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("donkey_kit.test")
    monkeypatch.setattr(telemetry, "_tracer", lambda: tracer)
    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )
    with (
        Donkey(_cfg()) as donkey,
        donkey.simulate(SalaryLeakBlocked),
        pytest.raises(SalaryLeakBlocked),
    ):
        with donkey.run():
            _chat(donkey)
    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert attrs["donkey.policy.decision"] == "refuse"
    assert attrs["donkey.policy.type"] == "acme_salary_block"


def test_policy_type_slug() -> None:
    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )
    err = classify(_resp(403, {"error": {"type": "acme_salary_block"}}))
    assert telemetry.policy_type_slug(err) == "acme_salary_block"
    # An unregistered subclass falls back to the built-in family slug.
    assert telemetry.policy_type_slug(CustomerPIIBlocked("x")) == "policy_violation"


# --- simulator and conformance fallback (reference 12, §8) --------------------


def test_resolve_fixture_serves_a_registered_type() -> None:
    classify.register(
        match={"status": 403, "headers": {"x-acme-policy": "salary"}, "error_code": "s"},
        raises=SalaryLeakBlocked,
    )
    fixture = resolve_fixture(SalaryLeakBlocked)
    assert fixture.shape == "registered:SalaryLeakBlocked"
    assert fixture.status == 403
    assert fixture.headers["x-acme-policy"] == "salary"


def test_resolve_fixture_names_classify_register_for_an_unknown_type() -> None:
    with pytest.raises(ValueError, match=r"classify\.register\(\)"):
        resolve_fixture(SalaryLeakBlocked)


def test_resolve_fixture_refuses_a_type_whose_example_ties() -> None:
    classify.register(
        match={"status": 403, "headers": {"x-a": "1"}, "body": {"k": "v"}},
        raises=SalaryLeakBlocked,
    )
    classify.register(
        match={"status": 403, "headers": {"x-a": "1"}, "body": {"k": re.compile("v+")}},
        raises=AcmeBlocked,
    )
    # SalaryLeakBlocked's own example matches AcmeBlocked equally: it would
    # classify as the generic tie, so the simulator cannot serve it.
    with pytest.raises(ValueError, match="cannot inject SalaryLeakBlocked"):
        resolve_fixture(SalaryLeakBlocked)


async def test_serve_refusal_arms_a_registered_type() -> None:
    openai = pytest.importorskip("openai")
    from donkey_kit.conformance.harness import ConformanceHarness
    from donkey_kit.conformance.suite import Outcome, Scenario

    classify.register(
        match={"status": 403, "error_type": "acme_salary_block"}, raises=SalaryLeakBlocked
    )

    class Agent:
        def __init__(self, donkey: Donkey) -> None:
            self._client = donkey.openai()

        async def run(self, prompt: str) -> str:
            try:
                await self._client.responses.create(model="gpt-4o", input=prompt)
            except openai.APIStatusError as exc:
                raise classify(exc.response) from exc
            return "ok"

    async def check(ctx: Any) -> Outcome:
        ctx.serve_refusal(SalaryLeakBlocked)
        obs = await ctx.run()
        return Outcome(type(obs.raised) is SalaryLeakBlocked, repr(obs.raised))

    scenario = Scenario("custom", "custom", "typed?", check)
    outcome = await ConformanceHarness(Agent).run_scenario(scenario)
    assert outcome.passed, outcome.detail


# --- the provider-value set is the captured one -------------------------------


def _captured_error_values() -> set[str]:
    values: set[str] = set()
    roots = (_PYTHON / "tests/fixtures", _PYTHON / "src/donkey_kit/simulator/_fixtures")
    for path in (p for root in roots for p in root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue  # an empty-body capture
        for envelope in data if isinstance(data, list) else [data]:
            if isinstance(envelope, dict) and "jsonrpc" in envelope:
                continue  # a JSON-RPC error (MCP tool-access denial, #303), not an HTTP envelope
            error = envelope.get("error") if isinstance(envelope, dict) else None
            if isinstance(error, dict):
                values.update(
                    v for k in ("type", "code", "status") if (v := code_str(error.get(k)))
                )
    return values


def test_every_captured_provider_error_value_is_in_the_provider_set() -> None:
    built_in = {"pii_detected", "agent_killed"}
    captured = _captured_error_values()
    assert built_in <= captured  # the walk reaches the rejection captures
    assert captured - built_in <= _policy_spec._PROVIDER_VALUES
