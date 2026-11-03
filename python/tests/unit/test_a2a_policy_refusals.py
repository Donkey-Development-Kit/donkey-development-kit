"""Fixture-backed A2A refusal typing (BG §1.2, ADR 0021, #1394)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from donkey_kit._testing import isolated_classify
from donkey_kit.core.classifier import classify
from donkey_kit.core.errors import (
    AuthError,
    ContentSafetyBlocked,
    PolicyViolation,
    TokenBudgetExceeded,
    ToolInvocationError,
    UpstreamModelError,
)
from donkey_kit.core.jsonrpc_refusals import jsonrpc_refusal

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"


def _response(name: str) -> httpx.Response:
    lines = (_FIXTURES / f"{name}.headers.txt").read_text().splitlines()
    headers = [line.split(":", 1) for line in lines[1:] if line]
    body = _FIXTURES / f"{name}.body.json"
    return httpx.Response(
        int(lines[0].split()[1]),
        headers=[(key, value.strip()) for key, value in headers],
        content=body.read_bytes() if body.exists() else b"",
        request=httpx.Request("POST", "https://agent.example.com/"),
    )


@pytest.mark.parametrize("method", ["send", "stream"])
def test_captured_azure_block_is_typed_by_both_entry_points(method: str) -> None:
    """Section 4: the explicit guardrail header, not FORBIDDEN, identifies Azure."""
    response = _response(f"v1-azure-block-{method}")
    original = response.content
    for error in (
        classify(response, correlation_id="corr", call_id="call"),
        jsonrpc_refusal(
            response.json(),
            status=403,
            headers=response.headers,
            correlation_id="corr",
            call_id="call",
            response=response,
        ),
    ):
        assert type(error) is ContentSafetyBlocked
        assert error.provider == "azure-content-safety"
        assert error.categories == ["prompt_shield"]
        assert error.policy == "content-safety"
        assert error.remediation
        assert (error.correlation_id, error.call_id) == ("corr", "call")
        assert error.response is response
        assert not isinstance(error, UpstreamModelError)
    assert response.content == original


@pytest.mark.parametrize(
    "name",
    [
        "v03-pii-send",
        "v1-pii-send",
        "v1-pii-stream",
        "v03-schema-params",
        "v03-schema-method",
        "v03-schema-parse",
        "v1-schema-params",
        "v1-schema-method",
        "v1-schema-parse",
    ],
)
def test_captured_pii_and_schema_errors_remain_ambiguous(name: str) -> None:
    """ADR 0021: generic codes/reasons and diagnostic text do not identify a policy."""
    response = _response(name)
    original = response.content
    error = classify(response)
    assert type(error) is ToolInvocationError
    assert error.response is response
    assert error.remediation
    assert "capture@example.com" not in str(error)
    assert "capture@example.com" not in repr(error)
    assert (
        jsonrpc_refusal(response.json(), status=response.status_code, headers=response.headers)
        is None
    )
    assert response.content == original


@pytest.mark.parametrize("method", ["send", "stream"])
@pytest.mark.parametrize("provider", ["azure", "bedrock"])
def test_captured_fail_closed_guardrail_is_an_outage(provider: str, method: str) -> None:
    """Section 4: reject on HTTP 503 is not a content block or a no-response failure."""
    response = _response(f"v1-{provider}-unavailable-{method}")
    error = classify(response)
    assert type(error) is UpstreamModelError
    assert not isinstance(error, PolicyViolation)
    assert error.response is response
    assert error.remediation
    assert jsonrpc_refusal(response.json(), status=503, headers=response.headers) is None


@pytest.mark.parametrize("name", ["v03-rate-send", "v1-rate-send", "v1-rate-stream"])
def test_captured_token_budget_is_terminal_with_millisecond_reset(name: str) -> None:
    """Section 4: captured bare 429 responses already use the shared token-budget rule."""
    response = _response(name)
    error = classify(response)
    assert type(error) is TokenBudgetExceeded
    assert error.retry_after == 60.0
    assert error.remediation
    assert not isinstance(error, UpstreamModelError)
    assert error.response is response


@pytest.mark.parametrize("status", [200, 400, 401, 429, 503])
def test_azure_header_alone_cannot_turn_other_statuses_into_content_blocks(status: int) -> None:
    response = _response("v1-azure-block-send")
    assert jsonrpc_refusal(response.json(), status=status, headers=response.headers) is None
    changed = httpx.Response(status, headers=response.headers, content=response.content)
    assert not isinstance(classify(changed), ContentSafetyBlocked)


@pytest.mark.parametrize("action", [None, "allow", "REJECT", ""])
def test_azure_marker_must_be_an_exact_reject(action: str | None) -> None:
    response = _response("v1-azure-block-send")
    if action is None:
        del response.headers["x-flex-azure-guardrails-action"]
    else:
        response.headers["x-flex-azure-guardrails-action"] = action
    assert type(classify(response)) is ToolInvocationError
    assert jsonrpc_refusal(response.json(), status=403, headers=response.headers) is None


def test_auth_challenge_precedes_azure_marker() -> None:
    response = _response("v1-azure-block-send")
    response.headers["www-authenticate"] = "Bearer"
    assert type(classify(response)) is AuthError
    assert jsonrpc_refusal(response.json(), status=403, headers=response.headers) is None


def test_bedrock_content_block_remains_uncaptured() -> None:
    """Only Bedrock outages were observed; do not claim a captured block shape."""
    response = _response("v1-bedrock-unavailable-send")
    assert jsonrpc_refusal(response.json(), status=403, headers=response.headers) is None


def test_azure_matcher_never_renders_rpc_diagnostics() -> None:
    response = _response("v1-azure-block-send")
    body = response.json()
    body["error"]["message"] = "secret@example.com"
    body["error"]["data"][0]["metadata"]["detail"] = "secret@example.com"
    changed = httpx.Response(403, headers=response.headers, json=body)
    error = classify(changed)
    assert type(error) is ContentSafetyBlocked
    assert "secret@example.com" not in str(error)
    assert "secret@example.com" not in repr(error)
    assert error.response is changed


@pytest.mark.parametrize("change", [{"jsonrpc": "1.0"}, {"id": True}, {"result": {}}])
def test_rpc_helper_rejects_malformed_envelopes_even_with_azure_headers(
    change: dict[str, object],
) -> None:
    response = _response("v1-azure-block-send")
    body = {**response.json(), **change}
    assert jsonrpc_refusal(body, status=403, headers=response.headers) is None


def test_azure_builtin_cannot_be_shadowed_by_custom_rpc_registration() -> None:
    class CustomBlocked(PolicyViolation):
        policy = "custom-azure"
        remediation = "Review the custom policy before retrying."

    response = _response("v1-azure-block-send")
    with isolated_classify():
        with pytest.raises(ValueError):
            classify.register(
                match={
                    "status": 403,
                    "jsonrpc_code": -32600,
                    "headers": {"x-flex-azure-guardrails-action": "reject"},
                },
                raises=CustomBlocked,
            )
        classify.register(
            match={
                "status": 403,
                "jsonrpc_code": -32600,
                "headers": {"x-acme-policy": "blocked"},
            },
            raises=CustomBlocked,
        )
        response.headers["x-acme-policy"] = "blocked"
        assert type(classify(response)) is ContentSafetyBlocked
        assert (
            type(jsonrpc_refusal(response.json(), status=403, headers=response.headers))
            is ContentSafetyBlocked
        )
