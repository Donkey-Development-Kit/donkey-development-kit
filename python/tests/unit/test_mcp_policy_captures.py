"""Pin the live #1384 MCP policy envelopes (BG §2.2; verification ledger §9).

These tests assert wire evidence, not SDK classification, which belongs to
the dependent issues. Ordinary failed tool results remain distinct from refusals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_policies"


def _capture(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    capture = json.loads((_FIXTURES / f"{name}.json").read_text("utf-8"))
    body = capture["body"]
    if capture["headers"]["content-type"] == "text/event-stream":
        assert body.startswith("event: message\ndata: ") and body.endswith("\n\n")
        body = body.removeprefix("event: message\ndata: ").removesuffix("\n\n")
    return capture, json.loads(body)


@pytest.mark.parametrize(
    ("name", "status", "code"),
    [
        ("pii-request", 403, -32602),
        ("pii-response-sse", 403, -32603),
        ("schema-invalid-args", 200, -32602),
        ("schema-missing-id", 200, -32600),
        ("poisoning-block-list", 200, -32603),
        ("progressive-invalid-params", 200, -32602),
        ("abac-denied", 200, -32008),
    ],
)
def test_refusal_status_and_rpc_code(name: str, status: int, code: int) -> None:
    capture, body = _capture(name)
    assert capture["status"] == status
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == code
    assert "result" not in body


@pytest.mark.parametrize(
    ("name", "phase"), [("pii-request", "request"), ("pii-response-sse", "response")]
)
def test_pii_discriminator_and_masked_findings(name: str, phase: str) -> None:
    capture, body = _capture(name)
    assert capture["headers"]["content-type"] == "application/json"
    data = body["error"]["data"]
    assert data["policy"] == "mcp-pii-detector"
    assert data["reason"] == "policy_pii_detected"
    assert data["phase"] == phase
    assert all("masked_value" in finding and "value" not in finding for finding in data["findings"])
    assert "capture@example.com" not in capture["body"]


def test_schema_data_is_not_a_safe_policy_discriminator() -> None:
    _, body = _capture("schema-invalid-args")
    assert isinstance(body["error"]["data"], str)
    assert "value 42" in body["error"]["data"]
    _, missing = _capture("schema-missing-id")
    assert missing["id"] == 1


def test_poisoning_is_schema_drift_and_remove_tool_empties_catalog() -> None:
    _, blocked = _capture("poisoning-block-list")
    findings = blocked["error"]["data"]["findings"]
    assert len(findings) == 4
    assert all(f["type"] == "drift" and f["changedFields"] == ["inputSchema"] for f in findings)
    _, removed = _capture("poisoning-remove-list")
    assert removed["result"]["tools"] == []


def test_progressive_catalog_and_invalid_params_have_no_policy_id() -> None:
    _, listed = _capture("progressive-list")
    assert [tool["name"] for tool in listed["result"]["tools"]] == ["search_tools", "invoke_tool"]
    _, refused = _capture("progressive-invalid-params")
    assert refused["error"]["data"] == {"reason": "invalid_params"}


def test_mapped_name_is_callable_and_transforms_are_not_refusals() -> None:
    _, listed = _capture("mapping-list")
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert "arithmetic" in names and "calculate" not in names
    _, called = _capture("mapping-call")
    assert called["result"]["content"] == [{"type": "text", "text": "42"}]
    _, masked = _capture("pii-mask-sse")
    assert masked["result"]["content"] == [{"text": "**", "type": "text"}]
    _, optimized = _capture("optimization-html")
    assert "error" not in optimized and optimized["result"]["isError"] is True
    assert "DDK capture" in optimized["result"]["content"][0]["text"]
    assert "<p>" not in optimized["result"]["content"][0]["text"]
