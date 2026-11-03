"""Framework-free JSON-RPC refusals, not protocol implementation (BG §2.2, #1385).

Only explicit policy markers or opt-in registrations name a refusal. Generic
JSON-RPC codes, ErrorInfo reasons and diagnostic text never establish a policy.
Built-ins cover legacy MCP access denial, MCP PII and captured content-safety headers.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from . import _wire
from ._refusal_body import content_safety_headers
from ._response import ResponseLike
from ._tool_access import match_tool_access
from .errors import (
    _JSONRPC_MATCHER,
    _POLICY_MATCHERS,
    AuthError,
    ContentSafetyBlocked,
    DonkeyError,
    PIIDetected,
    PolicyViolation,
    ToolInvocationError,
    _match_request_rate_limit,
    _match_token_budget,
    _match_upstream_failure,
    _Shape,
)

__all__ = [
    "PROVIDER_VALUES",
    "REGISTERED_MATCHER",
    "RpcShape",
    "jsonrpc_refusal",
    "policy_header",
    "policy_identity",
    "policy_marked",
    "rpc_error",
    "rpc_shape",
    "valid_envelope",
]

# Filled by classifier, which owns the single process-wide registry (ADR 0021).
REGISTERED_MATCHER: list[Callable[[RpcShape], PolicyViolation | None]] = []

# Captured provider values (docs/verified-apis.md §4), shared with HTTP validation.
PROVIDER_VALUES = frozenset(
    {
        "",
        "400",
        "404",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "decimal_above_max_value",
        "invalid_request_error",
        "model_not_found",
        "upstream_error",
    }
)
_GENERIC_IDENTIFIERS = frozenset(value.casefold() for value in PROVIDER_VALUES) | {
    "forbidden",
    "invalid_params",
    "method_not_found",
    "parse_error",
    "invalid_request",
    "internal_error",
    "permission_denied",
    "unauthenticated",
    "unknown",
}
_STANDARD_X_HEADERS = frozenset(
    {
        "x-frame-options",
        "x-content-type-options",
        "x-xss-protection",
        _wire.REQUEST_ID_HEADER.lower(),
        _wire.CORRELATION_ID_HEADER.lower(),
        "x-powered-by",
        "x-real-ip",
        "x-cache",
        "x-cache-hits",
        "x-runtime",
        "x-served-by",
        "x-download-options",
        "x-permitted-cross-domain-policies",
        "x-dns-prefetch-control",
        "x-robots-tag",
        "x-ua-compatible",
    }
)


@dataclass(frozen=True)
class RpcShape:
    """Normalized envelope and optional transport context for registry matching."""

    body: dict[str, object]
    error: Mapping[str, object]
    status: int | None
    headers: Mapping[str, str]
    kw: dict[str, Any]


def rpc_error(value: object) -> Mapping[str, object] | None:
    """Read a raw mapping or duck-typed error; reject malformed protocol fields."""
    if isinstance(value, Mapping):
        fields = value
    else:
        fields = {
            key: getattr(value, key, None) for key in ("code", "message", "data", "type", "status")
        }
    if type(fields.get("code")) is not int or not isinstance(fields.get("message"), str):
        return None
    return fields


def valid_envelope(value: Mapping[str, object]) -> bool:
    """Validate explicit protocol metadata without descending malformed responses."""
    return (
        value.get("jsonrpc") == "2.0"
        and "id" in value
        and (value.get("id") is None or type(value.get("id")) in (str, int))
        and "error" in value
        and "result" not in value
        and value.get("isError") is not True
        and rpc_error(value["error"]) is not None
    )


def policy_identity(path: str, value: object) -> bool:
    """Only exact nongeneric policy/reason identifiers establish policy identity."""
    return (
        path in {"error.data.policy", "error.data.reason"}
        and isinstance(value, str)
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", value) is not None
        and value.casefold() not in _GENERIC_IDENTIFIERS
    )


def policy_header(name: str, value: str) -> bool:
    """Exclude standard transport/security headers from custom policy evidence."""
    return (
        name.startswith("x-")
        and name not in _STANDARD_X_HEADERS
        and not name.startswith("x-forwarded-")
        and bool(value.strip())
    )


def policy_marked(error: Mapping[str, object]) -> bool:
    """An explicit structured policy identity, never a diagnostic inference.

    MCP captures name data.policy / policy_* reasons. Generic ErrorInfo values
    FORBIDDEN and INVALID_PARAMS remain ambiguous even inside an ErrorInfo list.
    """
    data = error.get("data")
    details = data if isinstance(data, (list, tuple)) else (data,)
    for detail in details:
        if not isinstance(detail, Mapping):
            continue
        policy, reason = detail.get("policy"), detail.get("reason")
        if policy_identity("error.data.policy", policy):
            return True
        if (
            policy_identity("error.data.reason", reason)
            and isinstance(reason, str)
            and reason.startswith("policy_")
        ):
            return True
    return False


def rpc_shape(
    error: Mapping[str, object],
    *,
    status: int | None,
    headers: Mapping[str, str] | None,
    body: Mapping[str, object] | None = None,
    correlation_id: str | None = None,
    call_id: str | None = None,
    request_id: str | None = None,
    response: ResponseLike | None = None,
) -> RpcShape:
    """Build canonical error.* paths without serialization of untrusted data."""
    return RpcShape(
        {**(body or {}), "error": dict(error)},
        error,
        status,
        {name.lower(): value for name, value in (headers or {}).items()},
        dict(
            correlation_id=correlation_id, call_id=call_id, request_id=request_id, response=response
        ),
    )


def jsonrpc_refusal(
    error: object,
    *,
    status: int | None = None,
    headers: Mapping[str, str] | None = None,
    tool: str | None = None,
    server: str | None = None,
    correlation_id: str | None = None,
    call_id: str | None = None,
    request_id: str | None = None,
    response: ResponseLike | None = None,
) -> PolicyViolation | None:
    """Recover a JSON-RPC policy refusal from mappings or exception.error ducks.

    Walks cause, context, exception groups and nested error envelopes, with cycle
    detection. Registrations require their exact status; absent status is not
    guessed as 200. HTTP 401, 429 and auth challenges are left to HTTP classify.
    Ordinary or ambiguous errors return None. Raw message/data are never rendered,
    except the existing access-denial tool-name parsing (docs/verified-apis.md §9).
    This helper does not wire new policies into framework bindings (#1388).
    """
    lowered = {name.lower(): value for name, value in (headers or {}).items()}
    if status in (401, 429) or _wire.WWW_AUTHENTICATE_HEADER in lowered:
        return None
    if status is not None and (
        type(status) is not int or not (status == 200 or 400 <= status < 500)
    ):
        return None
    stack: list[tuple[object, Mapping[str, object] | None]] = [(error, None)]
    seen: set[int] = set()
    candidates: list[RpcShape] = []
    while stack:
        current, envelope = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        fields = (
            current
            if isinstance(current, Mapping)
            else {
                key: getattr(current, key)
                for key in ("jsonrpc", "id", "error", "result", "isError")
                if hasattr(current, key)
            }
        )
        if ("jsonrpc" in fields and not valid_envelope(fields)) or (
            "error" in fields and ("result" in fields or fields.get("isError") is True)
        ):
            continue
        found = rpc_error(current)
        if found is not None:
            denied = match_tool_access(found, tool=tool, server=server)
            if denied is not None:
                denied.correlation_id, denied.call_id = correlation_id, call_id
                denied.request_id, denied.response = request_id, response
                return denied
            candidates.append(
                rpc_shape(
                    found,
                    status=status,
                    headers=lowered,
                    body=envelope,
                    correlation_id=correlation_id,
                    call_id=call_id,
                    request_id=request_id,
                    response=response,
                )
            )
        nested = (
            current.get("error")
            if isinstance(current, Mapping)
            else getattr(current, "error", None)
        )
        if nested is not None:
            stack.append((nested, fields))
        if isinstance(current, BaseException):
            members = getattr(current, "exceptions", ())
            if isinstance(members, (tuple, list)):
                stack.extend(
                    (member, None) for member in members if isinstance(member, BaseException)
                )
            stack.extend(
                (e, None) for e in (current.__cause__, current.__context__) if e is not None
            )
    for shape in candidates:
        if (pii := _match_mcp_pii(shape)) is not None:
            return pii
        if (verdict := content_safety_headers(shape.status, shape.headers)) is not None:
            provider, vendor, categories = verdict
            return ContentSafetyBlocked(
                f"Request blocked by {vendor} (403).",
                provider=provider,
                categories=categories,
                **shape.kw,
            )
    for shape in candidates:
        if REGISTERED_MATCHER and (matched := REGISTERED_MATCHER[0](shape)) is not None:
            return matched
    if any(policy_marked(shape.error) for shape in candidates):
        return PolicyViolation(
            "Request refused by an unrecognised JSON-RPC policy; shape unconfirmed.",
            policy="unknown",
            correlation_id=correlation_id,
            call_id=call_id,
            request_id=request_id,
            response=response,
        )
    return None


def _match_mcp_pii(s: RpcShape) -> PIIDetected | None:
    """Capture-backed policy/reason AND phase/code pairs (ledger §9, #1386).

    Metadata is a projection, never gateway diagnostics. Canonical enum names
    follow pii-core's PiiType; custom pattern names and raw/part-masked values
    are untrusted. Unknown fields stay only on the original response.
    """
    data = s.error.get("data")
    if not isinstance(data, Mapping):
        return None
    phase = data.get("phase")
    if (
        data.get("policy") != "mcp-pii-detector"
        or data.get("reason") != "policy_pii_detected"
        or not (
            (phase == "request" and s.error.get("code") == -32602)
            or (phase == "response" and s.error.get("code") == -32603)
        )
    ):
        return None
    findings: list[dict[str, str]] = []
    raw = data.get("findings")
    for finding in raw if isinstance(raw, list) else ():
        if not isinstance(finding, Mapping):
            continue
        kind, masked = finding.get("pii_type"), finding.get("masked_value")
        if isinstance(kind, Mapping) and set(kind) == {"Custom"}:
            kind = "Custom" if isinstance(kind["Custom"], str) else None
        if (
            not isinstance(kind, str)
            or kind not in {"SSN", "Email", "CreditCard", "PhoneNumber", "Custom"}
            or not isinstance(masked, str)
        ):
            continue
        findings.append({"pii_type": kind, "masked_value": "*" * min(len(masked), 256)})
    return PIIDetected(
        f"MCP {phase} blocked: personally identifiable information detected. Values withheld.",
        policy="mcp-pii-detector",
        phase=phase,
        findings=findings,
        entities=[finding["pii_type"] for finding in findings],
        **s.kw,
    )


def _match_http(s: _Shape) -> DonkeyError:
    """Shared HTTP dispatch: built-ins first, safe RPC matching, no provider fallthrough."""
    failure = _match_upstream_failure(s)
    if failure is not None:
        return failure
    if s.status == 429:
        limited = _match_request_rate_limit(s) or _match_token_budget(s)
        assert limited is not None
        return limited
    if _wire.WWW_AUTHENTICATE_HEADER in s.response.headers:
        return AuthError(f"Authentication/authorization failed ({s.status}).", **s.kw)
    # Preserve built-in discriminators, never parse RPC diagnostics as labels.
    safe_error: dict[str, Any] | None = (
        {key: s.error_obj[key] for key in ("code", "type", "status") if key in s.error_obj}
        if s.error_obj is not None
        else None
    )
    safe_body = {**(s.body or {}), "error": safe_error}
    if isinstance(safe_body.get("matched_patterns"), list):
        safe_body["matched_patterns"] = []
    builtin = (
        replace(s, body=None, error_obj=None, error_type=None)
        if s.status in (401, 429) or not valid_envelope(s.body or {})
        else replace(s, body=safe_body, error_obj=safe_error)
    )
    for matcher in _POLICY_MATCHERS:
        if (matched := matcher(builtin)) is not None:
            return matched
    refusal = jsonrpc_refusal(s.body, status=s.status, headers=s.response.headers, **s.kw)
    return refusal or ToolInvocationError(
        f"JSON-RPC operation failed ({s.status}); inspect .response.", **s.kw
    )


_JSONRPC_MATCHER[:] = [_match_http]
