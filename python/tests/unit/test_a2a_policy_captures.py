"""Pin live v0.3 and v1 A2A policy shapes (docs/verified-apis.md section 4, #1393).

These tests verify fixture contracts, not runtime typing (#1394) or simulator
support (#1397). No a2a-sdk or framework dependency is needed.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"


def _response(name: str) -> httpx.Response:
    lines = (_FIXTURES / f"{name}.headers.txt").read_text().splitlines()
    headers = [line.split(":", 1) for line in lines[1:] if line]
    body = _FIXTURES / f"{name}.body.json"
    return httpx.Response(
        int(lines[0].split()[1]),
        headers=[(key, value.strip()) for key, value in headers],
        content=body.read_bytes() if body.exists() else b"",
    )


def test_pii_refusal_embeds_synthetic_findings_in_string_data() -> None:
    """Section 4: v0.3 PII echoes findings in data, not a v1 ErrorInfo list."""
    response = _response("v03-pii-send")
    assert response.status_code == 403
    assert "www-authenticate" not in response.headers
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == 1393
    error = payload["error"]
    assert error["code"] == -32602
    assert error["message"] == "Invalid params"
    assert isinstance(error["data"], str)
    prefix = "Request contains PII data: "
    assert error["data"].startswith(prefix)
    assert json.loads(error["data"][len(prefix) :]) == [
        {"pii_type": "Email", "value": "capture@example.com", "start": 6, "end": 25}
    ]


@pytest.mark.parametrize(
    ("name", "code", "request_id", "detail"),
    [
        ("v03-schema-params", -32602, 1393, "missing field `parts`"),
        ("v03-schema-method", -32601, 1393, "Invalid methods: `unknown/method`"),
        ("v03-schema-parse", -32700, 1, "EOF while parsing a value"),
    ],
)
def test_schema_refusals_have_string_details(
    name: str, code: int, request_id: int, detail: str
) -> None:
    """Section 4: v0.3 schema errors are HTTP 400, including unknown methods."""
    response = _response(name)
    assert response.status_code == 400
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == request_id
    assert payload["error"]["code"] == code
    assert isinstance(payload["error"]["data"], str)
    assert detail in payload["error"]["data"]


@pytest.mark.parametrize("name", ["v03-rate-send", "v1-rate-send", "v1-rate-stream"])
def test_oversized_request_refused_with_positive_remaining_tokens(name: str) -> None:
    """Section 4: an empty 429 carries the window in milliseconds, no retry-after."""
    response = _response(name)
    assert response.status_code == 429
    assert response.content == b""
    assert response.headers["x-token-limit"] == "1"
    assert response.headers["x-token-remaining"] == "1"
    assert response.headers["x-token-reset"] == "60000"
    assert "retry-after" not in response.headers


@pytest.mark.parametrize("name", ["v1-pii-send", "v1-pii-stream"])
def test_v1_pii_echoes_findings_in_message_and_error_info(name: str) -> None:
    """V1 PII is FORBIDDEN, not an authentication challenge; both detail fields need redaction."""
    response = _response(name)
    assert response.status_code == 403
    assert "www-authenticate" not in response.headers
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "1393"
    error = payload["error"]
    assert error["code"] == -32600
    info = error["data"][0]
    assert info["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"
    assert info["domain"] == "a2a-protocol.org"
    assert info["reason"] == "FORBIDDEN"
    assert info["metadata"]["detail"] == error["message"]
    prefix = "Request contains PII data: "
    assert error["message"].startswith(prefix)
    assert json.loads(error["message"][len(prefix) :]) == [
        {"pii_type": "Email", "value": "capture@example.com", "start": 6, "end": 25}
    ]


@pytest.mark.parametrize(
    ("name", "status", "code", "request_id", "reason", "detail"),
    [
        ("v1-schema-params", 400, -32602, "1393", "INVALID_PARAMS", "missing field `messageId`"),
        ("v1-schema-method", 404, -32601, "1393", "METHOD_NOT_FOUND", "UnknownMethod"),
        ("v1-schema-parse", 400, -32700, None, "PARSE_ERROR", "EOF while parsing a value"),
    ],
)
def test_v1_schema_refusals_use_error_info(
    name: str, status: int, code: int, request_id: str | None, reason: str, detail: str
) -> None:
    """V1 unknown methods are HTTP 404; malformed JSON cannot retain a request ID."""
    response = _response(name)
    assert response.status_code == status
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == request_id
    assert payload["error"]["code"] == code
    info = payload["error"]["data"][0]
    assert info["@type"] == "type.googleapis.com/google.rpc.ErrorInfo"
    assert info["domain"] == "a2a-protocol.org"
    assert info["reason"] == reason
    assert detail in info["metadata"]["detail"]


@pytest.mark.parametrize("method", ["send", "stream"])
@pytest.mark.parametrize(
    ("provider", "kind", "status", "code", "reason", "detail"),
    [
        ("azure", "block", 403, -32600, "FORBIDDEN", "prompt_shield"),
        ("azure", "unavailable", 503, -32603, "UNAVAILABLE", "service_unavailable"),
        (
            "bedrock",
            "unavailable",
            503,
            -32603,
            "UNAVAILABLE",
            "Amazon Bedrock Guardrails service unavailable",
        ),
    ],
)
def test_v1_guardrail_refusals_use_error_info_and_observability_headers(
    method: str, provider: str, kind: str, status: int, code: int, reason: str, detail: str
) -> None:
    """Pre-upstream stream refusals are JSON, and unavailable is not a content block."""
    response = _response(f"v1-{provider}-{kind}-{method}")
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert "www-authenticate" not in response.headers
    prefix = f"x-flex-{provider}-guardrails"
    assert response.headers[f"{prefix}-action"] == "reject"
    assert response.headers[f"{prefix}-phase"] == "request"
    assert response.headers[f"{prefix}-reason"] == (
        "prompt_shield" if kind == "block" else "service_unavailable"
    )
    payload = response.json()
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == "1393"
    assert payload["error"]["code"] == code
    assert payload["error"]["data"] == [
        {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "domain": "a2a-protocol.org",
            "reason": reason,
            "metadata": {"detail": detail},
        }
    ]
