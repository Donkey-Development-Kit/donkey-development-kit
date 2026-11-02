"""MCP PII captures and safe metadata projection (BG §1.2/§2.2, ledger §9, #1386)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from donkey_kit import PIIDetected, PolicyViolation, classify, jsonrpc_refusal
from donkey_kit._testing import swap_transport
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import (
    AuthError,
    PromptInjectionBlocked,
    RequestRateLimitExceeded,
    TokenBudgetExceeded,
    UpstreamModelError,
)
from donkey_kit.core.transport import DonkeyClient

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_policies"


def _capture(name: str = "pii-request") -> tuple[dict, httpx.Response]:
    capture = json.loads((_FIXTURES / f"{name}.json").read_text())
    body = json.loads(capture["body"])
    request = httpx.Request(
        "POST",
        "https://gateway.example/mcp/",
        headers={
            "x-correlation-id": "corr",
            "x-donkey-request-id": "call",
        },
    )
    response = httpx.Response(
        capture["status"], headers=capture["headers"], content=capture["body"], request=request
    )
    return body, response


@pytest.mark.parametrize(
    "name,phase,entity,length",
    [
        ("pii-request", "request", "Email", 19),
        ("pii-response-sse", "response", "Custom", 2),
    ],
)
def test_captures_helper_and_http(name, phase, entity, length) -> None:
    """Ledger §9: both captures are HTTP 403 JSON; no SSE error was captured."""
    body, response = _capture(name)
    for result in (
        jsonrpc_refusal(body, response=response, correlation_id="corr", call_id="call"),
        classify(response),
    ):
        assert type(result) is PIIDetected
        assert isinstance(result, PolicyViolation) and not isinstance(result, UpstreamModelError)
        assert result.policy == "mcp-pii-detector"
        assert result.phase == phase and result.entities == [entity]
        assert result.findings == [{"pii_type": entity, "masked_value": "*" * length}]
        assert result.gateway_message is None and result.remediation
        assert (result.correlation_id, result.call_id) == ("corr", "call")
        assert result.response is response


def test_duck_chained_context_and_group_helper() -> None:
    body, _ = _capture()
    duck = SimpleNamespace(**body["error"])
    leaf = RuntimeError("untrusted framework text")
    leaf.error = duck
    root = RuntimeError("wrapper")
    root.__cause__ = leaf
    context = RuntimeError("context")
    context.__context__ = root
    group = RuntimeError("group")
    group.exceptions = (ValueError("ordinary"), context)
    for source in (body["error"], duck, leaf, root, context, group):
        result = jsonrpc_refusal(source, request_id="req")
        assert type(result) is PIIDetected and result.request_id == "req"


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy", None),
        ("policy", "other"),
        ("policy", "MCP-PII-DETECTOR"),
        ("reason", None),
        ("reason", "other"),
        ("reason", "POLICY_PII_DETECTED"),
        ("phase", None),
        ("phase", "REQUEST"),
        ("phase", "response"),
        ("phase", "secret@example.com"),
        ("phase", []),
        ("code", -32603),
        ("code", -32000),
        ("code", "-32602"),
        ("code", True),
    ],
)
def test_missing_unknown_conflicting_markers_are_not_pii(field, value) -> None:
    body, _ = _capture()
    target = body["error"] if field == "code" else body["error"]["data"]
    if value is None:
        target.pop(field)
    else:
        target[field] = value
    for result in (jsonrpc_refusal(body), classify(httpx.Response(403, json=body))):
        assert not isinstance(result, PIIDetected)
        assert "secret@example.com" not in str(result)


@pytest.mark.parametrize(
    "findings",
    [
        None,
        "secret",
        {},
        [None],
        ["secret"],
        [{"pii_type": "unknown", "masked_value": "secret"}],
        [{"pii_type": "Email", "masked_value": 123}],
        [{"pii_type": {"Custom": []}, "masked_value": "secret"}],
        [{"pii_type": {"Custom": "secret", "extra": "secret"}, "masked_value": "secret"}],
    ],
)
def test_malformed_metadata_keeps_typed_refusal_and_empty_findings(findings) -> None:
    body, _ = _capture()
    body["error"]["data"]["findings"] = findings
    result = jsonrpc_refusal(body)
    assert type(result) is PIIDetected
    assert result.phase == "request" and result.entities == [] and result.findings == []
    assert result.gateway_message is None


def test_untrusted_findings_message_and_labels_never_escape(caplog) -> None:
    body, _ = _capture()
    secret = "unsafe@example.com"
    body["error"]["message"] = secret
    body["error"]["data"]["findings"] = [
        {"pii_type": kind, "masked_value": secret, "value": secret, "start": secret}
        for kind in ("Email", "SSN", "CreditCard", "PhoneNumber", {"Custom": secret})
    ] + [
        {"pii_type": secret, "masked_value": secret},
        {"pii_type": "Email", "masked_value": secret * 1000},
    ]
    response = httpx.Response(403, json=body)
    with caplog.at_level(logging.DEBUG):
        results = (jsonrpc_refusal(body, response=response), classify(response))
    for result in results:
        assert type(result) is PIIDetected
        assert result.entities == ["Email", "SSN", "CreditCard", "PhoneNumber", "Custom", "Email"]
        assert result.gateway_message is None
        assert result.findings[-1] == {"pii_type": "Email", "masked_value": "*" * 256}
        assert all(set(finding) == {"pii_type", "masked_value"} for finding in result.findings)
        assert all(set(finding["masked_value"]) <= {"*"} for finding in result.findings)
        safe_attrs = {key: value for key, value in vars(result).items() if key != "response"}
        assert secret not in repr((result, result.args, safe_attrs, result.remediation))
        assert result.response is response and secret in response.text
    assert secret not in caplog.text
    assert response.json() == body


@pytest.mark.parametrize(
    "status,headers,kind",
    [
        (401, {}, AuthError),
        (403, {"www-authenticate": "Bearer"}, AuthError),
        (429, {}, TokenBudgetExceeded),
        (503, {}, UpstreamModelError),
        (429, {"x-ratelimit-limit": "10", "x-ratelimit-remaining": "0"}, RequestRateLimitExceeded),
        (403, {"x-injection-protection": "blocked"}, PromptInjectionBlocked),
    ],
)
def test_http_precedence_unchanged(status, headers, kind) -> None:
    body, _ = _capture()
    assert type(classify(httpx.Response(status, json=body, headers=headers))) is kind
    if kind is not PromptInjectionBlocked:
        assert jsonrpc_refusal(body, status=status, headers=headers) is None


@pytest.mark.parametrize("status", [200, 403])
def test_http200_is_explicit_only_and_403_is_terminal(status) -> None:
    body, _ = _capture()
    assert type(jsonrpc_refusal(body, status=200)) is PIIDetected
    assert type(classify(httpx.Response(200, json=body))) is PIIDetected
    cfg = DonkeyConfig(llm_proxy_url="https://gateway.example/", max_retries=3)
    calls, refusals = [], []

    class Client(DonkeyClient):
        def _on_refusal(self, request, response, violation, options):
            refusals.append(violation)
            return super()._on_refusal(request, response, violation, options)

    def respond(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    with Client(cfg) as client:
        swap_transport(client, httpx.MockTransport(respond))
        response = client.post("https://gateway.example/mcp/")
    assert response.status_code == status and len(calls) == 1
    assert [type(r) for r in refusals] == ([PIIDetected] if status == 403 else [])


def test_builtin_wins_custom_registration_across_leaves() -> None:
    class CustomBlocked(PolicyViolation):
        policy = "custom-mcp"
        remediation = "Ask the administrator."

    classify.register(
        match={
            "status": 403,
            "jsonrpc_code": -32602,
            "body": {"error.data.policy": "mcp-pii-detector"},
        },
        raises=CustomBlocked,
    )
    body, response = _capture()
    ordinary_custom = RuntimeError("custom")
    ordinary_custom.error = {
        "code": -32602,
        "message": "secret",
        "data": {"policy": "mcp-pii-detector"},
    }
    builtin = RuntimeError("builtin")
    builtin.error = body["error"]
    group = RuntimeError("group")
    group.exceptions = (builtin, ordinary_custom)
    assert type(jsonrpc_refusal(group, status=403)) is PIIDetected
    assert type(classify(response)) is PIIDetected
    with pytest.raises(ValueError, match="built-in.*PIIDetected"):
        classify.register(
            match={
                "status": 403,
                "jsonrpc_code": -32602,
                "body": {
                    "error.data.policy": "mcp-pii-detector",
                    "error.data.reason": "policy_pii_detected",
                    "error.data.phase": "request",
                },
            },
            raises=CustomBlocked,
        )


@pytest.mark.parametrize("pattern", ["request|response", "response|request"])
def test_phase_regex_overlap_registers_and_builtin_wins_only_captured_pair(pattern) -> None:
    class CustomBlocked(PolicyViolation):
        policy = "custom-mcp"
        remediation = "Ask the administrator."

    classify.register(
        match={
            "status": 403,
            "jsonrpc_code": -32602,
            "body": {
                "error.data.policy": "mcp-pii-detector",
                "error.data.reason": "policy_pii_detected",
                "error.data.phase": re.compile(pattern),
            },
        },
        raises=CustomBlocked,
    )
    body, response = _capture()
    assert type(classify(response)) is PIIDetected
    assert type(jsonrpc_refusal(body, status=403)) is PIIDetected
    body["error"]["data"]["phase"] = "response"
    assert type(classify(httpx.Response(403, json=body))) is CustomBlocked
    assert type(jsonrpc_refusal(body, status=403)) is CustomBlocked


@pytest.mark.parametrize("phase,code", [("request", -32602), ("response", -32603)])
def test_literal_captured_spec_is_rejected(phase, code) -> None:
    class CustomBlocked(PolicyViolation):
        policy = "custom-mcp"
        remediation = "Ask the administrator."

    with pytest.raises(ValueError, match="built-in.*PIIDetected"):
        classify.register(
            match={
                "status": 403,
                "jsonrpc_code": code,
                "body": {
                    "error.data.policy": "mcp-pii-detector",
                    "error.data.reason": "policy_pii_detected",
                    "error.data.phase": phase,
                },
            },
            raises=CustomBlocked,
        )


@pytest.mark.parametrize("slug", ["mcp-pii-detector", "pii-detection"])
def test_builtin_pii_policy_slugs_are_reserved(slug) -> None:
    class CustomBlocked(PolicyViolation):
        policy = slug
        remediation = "Ask the administrator."

    with pytest.raises(ValueError, match="built-in policy name"):
        classify.register(
            match={
                "status": 403,
                "jsonrpc_code": -32000,
                "body": {"error.data.policy": "custom-mcp"},
            },
            raises=CustomBlocked,
        )
    assert PIIDetected.policy == "pii-detection"


def test_additive_defaults_are_independent_and_legacy_values_unchanged() -> None:
    legacy = PIIDetected("safe", entities=["legacy"], gateway_message="legacy raw")
    other = PIIDetected("safe")
    assert legacy.phase is None and legacy.findings == []
    assert legacy.entities == ["legacy"] and legacy.gateway_message == "legacy raw"
    legacy.findings.append({"pii_type": "Email", "masked_value": "***"})
    assert other.findings == []
