"""Pin the live v0.3 A2A policy shapes (docs/verified-apis.md section 4, #1393).

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


def test_oversized_request_refused_with_positive_remaining_tokens() -> None:
    """Section 4: an empty 429 carries the window in milliseconds, no retry-after."""
    response = _response("v03-rate-send")
    assert response.status_code == 429
    assert response.content == b""
    assert response.headers["x-token-limit"] == "1"
    assert response.headers["x-token-remaining"] == "1"
    assert response.headers["x-token-reset"] == "60000"
    assert "retry-after" not in response.headers
