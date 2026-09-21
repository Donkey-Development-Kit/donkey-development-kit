"""errors.classify behaviour (BG §1.2). Policy rejections must be terminal."""

from __future__ import annotations

import httpx
import pytest

from donkey_kit.core.errors import (
    AuthError,
    ContentSafetyBlocked,
    DonkeyError,
    GatewayUnavailable,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
    UpstreamModelError,
    classify,
    gateway_unavailable,
)
from donkey_kit.core.transport import CALL_ID_HEADER, CORRELATION_HEADER


def _resp(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {}, request=httpx.Request("POST", "https://x"))


def test_401_is_auth_error() -> None:
    assert isinstance(classify(_resp(401)), AuthError)


def test_auth_error_uses_data_plane_default_unless_overridden() -> None:
    default = AuthError("boom")
    control_plane = AuthError("boom", remediation="check the connected app")

    assert default.remediation is AuthError.remediation
    assert "DONKEY_LLM_PROXY_CLIENT_ID" in default.remediation
    assert control_plane.remediation == "check the connected app"
    assert AuthError.connected_app_remediation.strip()
    assert AuthError.provider_chain_remediation.strip()


def test_generic_4xx_is_terminal_policy_violation() -> None:
    err = classify(_resp(400))
    assert isinstance(err, PolicyViolation)
    assert err.remediation  # required, non-empty (BG §1.2)


def test_429_is_token_budget_with_retry_after() -> None:
    err = classify(_resp(429, {"retry-after": "42"}))
    assert isinstance(err, TokenBudgetExceeded)
    assert err.retry_after == 42.0


def test_injection_protection_header_is_prompt_injection_blocked() -> None:
    """#181 row 3: a 400 carrying ``x-injection-protection: blocked`` is the
    injection-protection policy refusal, wired to the (previously dead)
    PromptInjectionBlocked exception with a required, non-empty remediation."""
    err = classify(_resp(400, {"x-injection-protection": "blocked"}))
    assert isinstance(err, PromptInjectionBlocked)
    assert err.policy == "prompt-injection-protection"
    assert err.remediation  # required, non-empty (BG §1.2)


def test_400_without_injection_header_is_not_prompt_injection() -> None:
    """#181 AC (a): the header — not the status — is the discriminator. An
    ordinary malformed 400 with no ``x-injection-protection`` header must stay
    an ordinary refusal, never PromptInjectionBlocked."""
    err = classify(_resp(400))
    assert not isinstance(err, PromptInjectionBlocked)
    assert isinstance(err, PolicyViolation)


def _json_resp(status: int, body: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, json=body, request=httpx.Request("POST", "https://x")
    )


def test_regex_prompt_guard_403_is_prompt_injection_not_auth() -> None:
    """#289: a 403 with a top-level ``matched_patterns`` list is a Regex Prompt
    Guard block, typed BEFORE the 401/403 → auth rule so it is not mis-typed as
    an auth failure."""
    err = classify(_json_resp(403, {"error": "blocked", "matched_patterns": ["pii-ssn"]}))
    assert isinstance(err, PromptInjectionBlocked)
    assert not isinstance(err, AuthError)
    assert err.policy == "regex-prompt-guard"
    assert err.remediation  # required, non-empty


def test_content_safety_403_header_is_content_safety_not_auth() -> None:
    """#289: a 403 carrying a vendor ``...-action: reject`` header is a
    content-safety block, keyed on the header (so a body-less Bedrock reject is
    caught) and typed BEFORE the 401/403 → auth rule."""
    err = classify(
        _resp(
            403,
            {
                "x-llm-proxy-bedrock-guardrail-action": "reject",
                "x-llm-proxy-bedrock-guardrail-reason": "denied_topic, pii",
            },
        )
    )
    assert isinstance(err, ContentSafetyBlocked)
    assert not isinstance(err, AuthError)
    assert err.policy == "content-safety"
    assert err.categories == ["denied_topic", "pii"]  # parsed from the reason header
    assert err.remediation  # required, non-empty


def test_content_safety_action_allow_is_not_content_safety_blocked() -> None:
    """The vendor header only discriminates on ``reject``; an ``allow`` verdict on
    a 403 is not a moderation block (#289). Nor is it auth: the header-less 403
    carries no ``www-authenticate`` challenge, so it is an unrecognised gateway
    policy refusal surfaced honestly as a generic PolicyViolation, not mis-typed
    as AuthError (#184)."""
    err = classify(_resp(403, {"x-llm-proxy-azure-content-safety-action": "allow"}))
    assert not isinstance(err, ContentSafetyBlocked)
    assert not isinstance(err, AuthError)
    assert isinstance(err, PolicyViolation)
    assert "shape unconfirmed" in str(err)


# --- honest fall-through for unrecognised refusals (#184) -------------------
# A non-429 4xx that matches none of the documented discriminators above and
# carries no nested provider envelope must NOT be coerced into a subclass whose
# contract we haven't verified. It is surfaced as a generic PolicyViolation that
# names what was observed and says the shape is unconfirmed. A 403 is auth ONLY
# when it carries the verified www-authenticate challenge (docs/verified-apis.md §4).


def test_unrecognised_403_falls_through_to_honest_policy_violation() -> None:
    """A header-less 403 matching no documented discriminator is an unrecognised
    gateway refusal, surfaced honestly — not mis-typed as AuthError (#184)."""
    err = classify(_resp(403))
    assert isinstance(err, PolicyViolation)
    assert not isinstance(err, AuthError)
    assert err.policy == "unknown"
    assert "shape unconfirmed" in str(err)
    # The remediation must tell the operator this is unconfirmed and to file it.
    assert err.remediation
    assert "unconfirmed" in err.remediation
    assert "issue" in err.remediation.lower()


def test_unrecognised_403_names_the_observed_policy_headers() -> None:
    """The message names the observable discriminators — status plus any
    ``x-llm-proxy-*`` policy headers present — so the unconfirmed shape can be
    typed from the report alone (#184, #253)."""
    err = classify(
        _resp(403, {"x-llm-proxy-mystery-verdict": "deny", "content-type": "application/json"})
    )
    assert isinstance(err, PolicyViolation)
    message = str(err)
    assert "status 403" in message
    assert "x-llm-proxy-mystery-verdict" in message
    # Non-policy headers are not part of the discriminator surface.
    assert "content-type" not in message


def test_403_with_www_authenticate_is_still_auth_error() -> None:
    """Regression guard for the #184 split: a 403 carrying a ``www-authenticate``
    challenge is the verified client-id-enforcement shape (docs/verified-apis.md §4) and must
    remain an AuthError, so ``donkey doctor``'s credentials diagnosis (#202)
    stays intact."""
    err = classify(_resp(403, {"www-authenticate": 'Bearer realm="anypoint"'}))
    assert isinstance(err, AuthError)


def test_401_is_auth_error_regardless_of_headers() -> None:
    """A 401 is always auth, with or without a www-authenticate header — the
    #184 www-authenticate gate applies only to disambiguating 403s."""
    assert isinstance(classify(_resp(401)), AuthError)
    assert isinstance(classify(_resp(401, {"x-llm-proxy-mystery-verdict": "deny"})), AuthError)


def test_5xx_is_retryable_upstream() -> None:
    assert isinstance(classify(_resp(503)), UpstreamModelError)


def test_policy_violation_is_not_a_retryable_type() -> None:
    # A PolicyViolation must never be an UpstreamModelError (which the transport
    # would retry). Distinct branches of the taxonomy (BG §1.2).
    assert not issubclass(PolicyViolation, UpstreamModelError)


# --- correlation/call id read-back (BG §1.1, #195) -----------------------------
# classify() derives the run id and the per-call id from the response's own
# request headers, so a caller bridging an openai error gets them for free.


def _resp_with_ids(status: int, correlation: str, call: str) -> httpx.Response:
    request = httpx.Request(
        "POST",
        "https://x",
        headers={CORRELATION_HEADER: correlation, CALL_ID_HEADER: call},
    )
    return httpx.Response(status, request=request)


def test_classify_reads_correlation_and_call_id_from_the_request() -> None:
    """AC: DonkeyError.correlation_id equals the header that was sent; call_id
    equals the per-call header. Both come from the response's request, so
    ``classify(err.response)`` needs no extra wiring."""
    err = classify(_resp_with_ids(400, "run-abc", "call-xyz"))
    assert err.correlation_id == "run-abc"
    assert err.call_id == "call-xyz"


def test_classify_explicit_ids_override_the_request_headers() -> None:
    """When a header name was overridden via config, auto-derivation can't see it,
    so an explicitly passed id wins over whatever is on the request."""
    err = classify(
        _resp_with_ids(400, "run-abc", "call-xyz"),
        correlation_id="explicit-run",
        call_id="explicit-call",
    )
    assert err.correlation_id == "explicit-run"
    assert err.call_id == "explicit-call"


def test_classify_without_a_request_yields_no_ids() -> None:
    """A response with no request set (httpx raises on access) must not blow up:
    the ids are simply None, and request_id still comes from the response."""
    resp = httpx.Response(500, headers={"x-request-id": "gw-1"})
    err = classify(resp)
    assert err.correlation_id is None
    assert err.call_id is None
    assert err.request_id == "gw-1"  # gateway's own id, from the response header


def test_gateway_unavailable_is_a_donkey_error_not_a_policy_violation() -> None:
    # An ungoverned failure — nothing was refused — so it must NOT be a
    # PolicyViolation (that base marks a terminal gateway refusal), but it is a
    # catchable DonkeyError like the rest of the taxonomy (#379, BG §1.2).
    err = gateway_unavailable(base_url="https://gw.example")
    assert isinstance(err, DonkeyError)
    assert not isinstance(err, PolicyViolation)


def test_gateway_unavailable_carries_base_url_cause_and_ids() -> None:
    cause = httpx.ConnectError("connection refused")
    err = gateway_unavailable(
        base_url="https://gw.example",
        cause=cause,
        correlation_id="run-9",
        call_id="call-9",
    )
    assert err.base_url == "https://gw.example"
    assert err.cause is cause
    assert err.correlation_id == "run-9"
    assert err.call_id == "call-9"
    assert err.request_id is None  # no response behind a transport failure
    assert "gw.example" in str(err)  # the origin is in the message, not only .base_url


def test_gateway_unavailable_remediation_names_the_three_causes_and_doctor() -> None:
    err = gateway_unavailable(base_url="https://gw.example")
    # Single canonical wording, reusable by `donkey doctor` (#202).
    assert err.remediation is GatewayUnavailable.remediation
    for needle in ("unreachable", "base URL", "egress", "donkey doctor"):
        assert needle in err.remediation


# --- mandatory remediation on every PolicyViolation (#182) ------------------
# A typed refusal without a next step is just a renamed exception: remediation
# is structurally guaranteed non-empty, and every concrete subclass ships its
# own canonical default — the single source `donkey doctor` (#202) reuses.


def _all_policy_violation_types() -> set[type[PolicyViolation]]:
    """Every PolicyViolation type, the base included, discovered transitively so
    a subclass added later is covered without editing this test."""
    seen: set[type[PolicyViolation]] = {PolicyViolation}
    stack: list[type[PolicyViolation]] = [PolicyViolation]
    while stack:
        for sub in stack.pop().__subclasses__():
            if sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return seen


def test_every_policy_violation_type_ships_its_own_nonempty_default() -> None:
    """AC: every concrete subclass supplies a default (its OWN, not merely the
    inherited base one, so each diagnosis carries tailored wording)."""
    types = _all_policy_violation_types()
    # Guard the known taxonomy is covered; a new member must make a deliberate
    # appearance here (and, by the assertion below, ship its own remediation).
    assert {
        PolicyViolation,
        PIIDetected,
        TokenBudgetExceeded,
        PromptInjectionBlocked,
        ContentSafetyBlocked,
    } <= types
    for cls in types:
        assert "remediation" in vars(cls), f"{cls.__name__} ships no own remediation default"
        assert vars(cls)["remediation"].strip(), f"{cls.__name__} default is empty/whitespace"


def test_every_policy_violation_instance_has_a_nonempty_remediation() -> None:
    """Constructed with only a message (every subclass's extra kwargs are
    optional), each still carries a non-empty next step."""
    for cls in _all_policy_violation_types():
        err = cls("boom")
        assert err.remediation.strip(), f"{cls.__name__}('boom').remediation is empty"


def test_policy_violation_raises_on_empty_or_whitespace_remediation() -> None:
    """AC: the constructor fails on an empty remediation — an explicit blank is a
    bug, not a silent acceptance."""
    for bad in ("", "   ", "\n\t "):
        with pytest.raises(ValueError):
            PolicyViolation("boom", remediation=bad)


def test_omitting_remediation_uses_the_class_default_not_a_copy() -> None:
    # The default is the same object as the class attribute — one source of
    # wording, so `donkey doctor` (#202) and the exception can't drift apart.
    assert PolicyViolation("boom").remediation is PolicyViolation.remediation
    assert PIIDetected("boom").remediation is PIIDetected.remediation


def test_explicit_remediation_overrides_the_default() -> None:
    err = PIIDetected("boom", remediation="mask the CREDIT_CARD value before resubmitting")
    assert err.remediation == "mask the CREDIT_CARD value before resubmitting"


def test_classify_pii_falls_back_to_the_class_default_remediation() -> None:
    # classify() no longer passes PII remediation inline; it must resolve to the
    # canonical class default (#182) — the same string, one source.
    err = classify(_json_resp(403, {"error": {"type": "pii_detected", "message": "blocked"}}))
    assert isinstance(err, PIIDetected)
    assert err.remediation is PIIDetected.remediation


def test_classify_token_budget_falls_back_to_the_class_default_remediation() -> None:
    err = classify(_resp(429))
    assert isinstance(err, TokenBudgetExceeded)
    assert err.remediation is TokenBudgetExceeded.remediation
