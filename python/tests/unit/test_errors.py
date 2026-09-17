"""errors.classify behaviour (§2.4). Policy rejections must be terminal."""

from __future__ import annotations

import httpx

from donkey_kit.core.errors import (
    AuthError,
    ContentSafetyBlocked,
    DonkeyError,
    GatewayUnavailable,
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


def test_generic_4xx_is_terminal_policy_violation() -> None:
    err = classify(_resp(400))
    assert isinstance(err, PolicyViolation)
    assert err.remediation  # required, non-empty (§2.4)


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
    assert err.remediation  # required, non-empty (§2.4)


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
    a 403 is an ordinary auth failure, not a moderation block (#289)."""
    err = classify(_resp(403, {"x-llm-proxy-azure-content-safety-action": "allow"}))
    assert not isinstance(err, ContentSafetyBlocked)
    assert isinstance(err, AuthError)


def test_5xx_is_retryable_upstream() -> None:
    assert isinstance(classify(_resp(503)), UpstreamModelError)


def test_policy_violation_is_not_a_retryable_type() -> None:
    # A PolicyViolation must never be an UpstreamModelError (which the transport
    # would retry). Distinct branches of the taxonomy (§2.4).
    assert not issubclass(PolicyViolation, UpstreamModelError)


# --- correlation/call id read-back (§2.3, #195) -----------------------------
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
