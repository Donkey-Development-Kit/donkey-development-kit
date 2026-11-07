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
        ("pii-response-json", 403, -32603),
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


def test_json_only_response_pii_capture_and_provenance() -> None:
    """Ledger §9: response-only Email Reject evidence, not SDK classification (BG §2.2)."""
    capture, body = _capture("pii-response-json")
    provenance = json.loads((_FIXTURES / "pii-response-json.provenance.json").read_text("utf-8"))
    assert capture["headers"] == {
        "content-type": "application/json",
        "server": "Anypoint Flex Gateway",
    }
    assert body == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {
            "code": -32603,
            "message": "Internal error",
            "data": {
                "findings": [{"masked_value": "*******@example.com", "pii_type": "Email"}],
                "phase": "response",
                "policy": "mcp-pii-detector",
                "reason": "policy_pii_detected",
            },
        },
    }
    assert provenance["request"] == {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "capture_contact", "arguments": {"sample": "synthetic"}},
        "id": body["id"],
    }
    assert provenance["captured_on"] == "2026-11-05"
    assert provenance["definition"] == {"assetId": "mcp-pii-detector", "version": "1.0.1"}
    assert provenance["implementation"]["assetId"] == "mcp-pii-detector-policy-flex"
    assert provenance["implementation"]["version"] == "1.0.2"
    assert provenance["configuration"] == {"entities": ["Email"], "action": "Reject"}
    assert provenance["upstream"]["content_type"] == "application/json"
    assert provenance["upstream"]["version"] == "1.0.2"
    assert provenance["upstream"]["synthetic_response_text"] == (
        "Synthetic contact: capture@example.com"
    )
    assert "capture@example.com" not in capture["body"]
    assert provenance["phase"] == body["error"]["data"]["phase"]
    assert provenance["response_id_scrubbed"] is False
    assert provenance["wire_body_modified"] is False
    assert provenance["verification"] == {
        "response_phase_proven": True,
        "exact_original_chain_restored": True,
        "final_instance_status": "active",
        "final_deployment_status": "APPLIED/RUNNING",
        "temporary_policies_remaining": 0,
        "final_direct_gateway_verified": True,
    }


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


def _text(name: str) -> tuple[dict[str, Any], str]:
    capture, body = _capture(name)
    assert "error" not in body
    return body, body["result"]["content"][0]["text"]


def test_payload_optimization_strips_a_base64_data_uri_but_not_bare_base64() -> None:
    """#1639: the control returns the data URI untouched; the policy replaces it."""
    _, control = _text("optimization-base64-control")
    assert "data:image/png;base64," in control
    result, stripped = _text("optimization-base64")
    assert stripped == "calculate failed: not an arithmetic expression: '[base64 data removed]'"
    assert result["result"]["isError"] is True  # still a tool result, not a refusal
    # The same policy left a bare base64 string alone: only the data URI form was seen stripped.
    _, bare = _text("optimization-base64-bare")
    assert "RERLIHN5bnRoZXRpYyBiYXJl" in bare and "removed" not in bare


def test_payload_optimization_remove_nulls_drops_null_fields_and_compacts_json() -> None:
    """#1639: ``remove_nulls`` removes null-valued keys at every depth of a JSON result."""
    _, control = _text("optimization-nulls-control")
    original = json.loads(control)
    assert original["nickname"] is None and original["owner"] == {"id": 7, "email": None}
    result, optimized = _text("optimization-nulls")
    expected = '{"id":1,"name":"capture","tags":["a","b"],"owner":{"id":7}}'
    assert optimized == expected  # null keys gone, and the JSON is re-serialized compact
    assert result["result"]["isError"] is False


def test_payload_optimization_toon_encodes_a_uniform_array_and_leaves_other_json_alone() -> None:
    """#1640: TOON replaces a uniform array of objects with a ``TOON:``-prefixed text block."""
    _, control = _text("optimization-toon-control")
    assert isinstance(json.loads(control), list) and len(json.loads(control)) == 20
    result, toon = _text("optimization-toon")
    assert result["result"]["isError"] is False
    assert toon.startswith("TOON: [20]{id,name,status,score}:\n  1,")
    assert len(toon) < len(control)
    with pytest.raises(json.JSONDecodeError):  # an agent expecting JSON gets text, not JSON
        json.loads(toon)
    # A JSON object with nulls is not an eligible shape: the same policy leaves it as the control.
    _, untouched = _text("optimization-toon-ineligible")
    assert untouched == _text("optimization-nulls-control")[1]
