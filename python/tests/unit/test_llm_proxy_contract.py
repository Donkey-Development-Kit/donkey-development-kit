"""Pins the SDK's data-plane contract to LIVE captures from a real governed
LLM proxy (`openai-sdk`, instance 21133858) deployed to `agent-network-ingress-gw`
and called end-to-end on 2026-08-28. See tests/fixtures/anypoint/llm_proxy/README.md
and docs/verified-apis.md §2/§3/§4.

Unlike the shape-only A2D fixtures, these are the direct Anypoint data-plane
contract, so they may drive real SDK behavior (the BG §1.5 fixture-derived error
table). The raw→exception mapping under test lives in core/errors.classify().
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from donkey_kit.core.errors import (
    AuthError,
    PIIDetected,
    PolicyViolation,
    TokenBudgetExceeded,
    UpstreamRequestError,
    classify,
)
from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "llm_proxy"


def _headers(name: str) -> dict[str, str]:
    """Shared loader parser (BG §1.4): the simulator replays these same files."""
    return parse_headers((FIXTURES / name).read_text())


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def test_success_response_is_openai_passthrough_with_usage() -> None:
    """docs/verified-apis.md §2: proxy returns the OpenAI Responses object verbatim, incl. token
    usage (the basis for cost attribution) — no /v1 rewriting of the body."""
    body = _load("responses.success.body.json")
    assert isinstance(body, dict)
    assert body["object"] == "response"
    assert body["status"] == "completed"
    # Requested gpt-5.1 → provider resolved a dated snapshot; passed through.
    assert body["model"].startswith("gpt-5.1")
    usage = body["usage"]
    assert {"input_tokens", "output_tokens", "total_tokens"} <= set(usage)
    assert usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]


def test_success_headers_carry_gateway_governance_and_identity() -> None:
    """docs/verified-apis.md §3: attribution/telemetry is on the response headers, not a bespoke
    request header the SDK must invent."""
    h = _headers("responses.success.headers.txt")
    assert h["server"] == "Anypoint Flex Gateway"
    assert h["x-llm-proxy-llm-provider"] == "openai"
    assert h["x-llm-proxy-routing-type"] == "ModelBased"
    # instance id + environment id are embedded here (api-instance-<id>.<envId>.svc)
    assert h["x-envoy-decorator-operation"].startswith("api-instance-21133858.")
    assert "x-correlation-id" in h


def test_client_id_enforcement_rejection_classifies_as_auth() -> None:
    """docs/verified-apis.md §4 family 1 — Anypoint policy rejection: flat string `error`, 401 +
    www-authenticate. classify() maps 401/403 → AuthError."""
    h = _headers("reject.client-id-missing.headers.txt")
    body = _load("reject.client-id-missing.body.json")
    assert h["www-authenticate"] == "Client-ID-Enforcement"
    assert body == {"error": "Client ID is not present"}  # flat string envelope

    resp = httpx.Response(401, headers=h, json=body)
    err = classify(resp)
    assert isinstance(err, AuthError)
    assert "consumer client_id/client_secret pair" in str(err)
    assert "docs/verified-apis.md §2" in str(err)
    assert "connected-app" not in str(err)


def test_two_error_envelope_families_are_distinguishable() -> None:
    """docs/verified-apis.md §4: the fixture-derived discriminator the BG §1.5 table needs.

    - Anypoint policy rejection → `error` is a STRING.
    - Upstream provider (OpenAI) passthrough → `error` is an OBJECT with
      type/code, and the x-llm-proxy-* routing headers are present (the request
      reached the provider). This is NOT a gateway policy refusal.
    """
    policy_err = _load("reject.client-id-missing.body.json")
    upstream_err = _load("reject.model-not-found.body.json")
    assert isinstance(policy_err["error"], str)
    assert isinstance(upstream_err["error"], dict)
    assert upstream_err["error"]["code"] == "model_not_found"
    assert upstream_err["error"]["type"] == "invalid_request_error"


def test_upstream_400_classifies_as_upstream_request_error() -> None:
    """docs/verified-apis.md §4 discriminator (fixture-driven, BG §1.5): the model-not-found 400 is
    an upstream provider passthrough (nested `error` object), so classify() returns
    UpstreamRequestError — NOT a gateway PolicyViolation — carrying the provider
    code/type for actionability."""
    body = _load("reject.model-not-found.body.json")
    resp = httpx.Response(400, json=body)
    err = classify(resp)
    assert isinstance(err, UpstreamRequestError)
    assert not isinstance(err, PolicyViolation)
    assert err.code == "model_not_found"
    assert err.error_type == "invalid_request_error"
    assert err.param == "model"


def test_pii_detection_rejection_classifies_as_pii_not_auth() -> None:
    """docs/verified-apis.md §4 live capture: the PII policy rejects with 403 + a NESTED error
    object whose type is ``pii_detected`` and NO ``www-authenticate`` header. Despite
    the 403, this is NOT an auth failure — classify() must return PIIDetected and
    surface the flagged entity types parsed from the message."""
    h = _headers("reject.pii-detected.headers.txt")
    body = _load("reject.pii-detected.body.json")
    assert isinstance(body, dict)
    assert body["error"]["type"] == "pii_detected"
    assert "www-authenticate" not in h  # discriminator vs. client-id-enforcement

    resp = httpx.Response(403, headers=h, json=body)
    err = classify(resp)
    assert isinstance(err, PIIDetected)
    assert not isinstance(err, AuthError)
    assert err.policy == "pii-detection"
    assert "Email" in err.entities  # parsed from the "pii_type" entries


def test_token_rate_limit_rejection_is_429_with_header_only_budget() -> None:
    """docs/verified-apis.md §4 live capture: the token-rate-limit policy rejects with 429 and an
    EMPTY body; the reset window is header-only (x-token-reset, in ms) with NO
    standard retry-after. classify() → TokenBudgetExceeded with retry_after
    derived from x-token-reset."""
    h = _headers("reject.token-rate-limit.headers.txt")
    assert h["x-token-limit"] == "1"
    assert h["x-token-remaining"] == "0"
    assert "retry-after" not in h
    reset_ms = float(h["x-token-reset"])

    resp = httpx.Response(429, headers=h)  # deliberately no body
    err = classify(resp)
    assert isinstance(err, TokenBudgetExceeded)
    assert err.policy == "token-rate-limit"
    assert err.retry_after == reset_ms / 1000.0


def test_anypoint_flat_error_4xx_stays_policy_violation() -> None:
    """A non-auth 4xx whose `error` is a flat STRING is an Anypoint gateway
    policy rejection, still surfaced as PolicyViolation (terminal, not retried)."""
    resp = httpx.Response(400, json={"error": "Request rejected by policy"})
    err = classify(resp)
    assert isinstance(err, PolicyViolation)
    assert err.policy == "unknown"
