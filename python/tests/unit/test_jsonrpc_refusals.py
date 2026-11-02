"""Shared JSON-RPC refusal matching, not per-policy or binding support (BG §2.2, #1385)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from donkey_kit._testing import isolated_classify
from donkey_kit.core.classifier import classify
from donkey_kit.core.errors import AuthError, DonkeyError, PolicyViolation, TokenBudgetExceeded
from donkey_kit.core.jsonrpc_refusals import jsonrpc_refusal
from donkey_kit.core.tool_access import ToolNotInProfile, tool_access_denial

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"


class AcmeBlocked(PolicyViolation):
    policy = "acme_rpc"
    remediation = "Ask the Acme administrator to grant this operation."


class SpecificBlocked(PolicyViolation):
    policy = "acme_specific"
    remediation = "Use the approved Acme operation."


def _error(**extra: object) -> dict[str, object]:
    return {"code": -32099, "message": "secret@example.com", "data": {"policy": "acme"}, **extra}


def _envelope(error: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "error": error}


def _register(status: int = 200, **extra: object):
    return classify.register(
        match={
            "status": status,
            "jsonrpc_code": -32099,
            "body": {"error.data.policy": "acme"},
            **extra,
        },
        raises=AcmeBlocked,
    )


@pytest.mark.parametrize("status", [200, 400, 403, 451])
def test_custom_rpc_matches_raw_envelope_duck_error_and_http(status: int) -> None:
    reg = _register(status)
    error = _error()
    wrapped = RuntimeError("framework secret")
    wrapped.error = SimpleNamespace(**error)
    for source in (error, _envelope(error), wrapped):
        result = jsonrpc_refusal(source, status=status, correlation_id="corr", call_id="call")
        assert type(result) is AcmeBlocked
        assert (result.correlation_id, result.call_id) == ("corr", "call")
        assert "secret" not in str(result)
        assert result.remediation
    assert type(jsonrpc_refusal(error)) is PolicyViolation  # status is never guessed
    assert reg.jsonrpc_code == -32099
    assert reg.conditions == 2
    assert reg.example().json()["jsonrpc"] == "2.0"
    assert type(classify(reg.example())) is AcmeBlocked
    response = httpx.Response(status, json=_envelope(error))
    result = classify(response)
    assert type(result) is AcmeBlocked and result.response is response


@pytest.mark.parametrize("code", [True, False, "-32099", -32099.0, None, [], {}])
def test_rpc_code_is_strict_in_registration_and_input(code: object) -> None:
    with pytest.raises(TypeError, match="jsonrpc_code"):
        _register(jsonrpc_code=code)
    _register()
    assert jsonrpc_refusal(_error(code=code), status=200) is None


@pytest.mark.parametrize("status", [201, 302, 401, 429, 500])
def test_rpc_registration_rejects_other_statuses(status: int) -> None:
    with pytest.raises(ValueError, match="status"):
        _register(status)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"error.code": -32099},
        {"error.message": "pii"},
        {"error.data": "pii"},
        {"error.data.reason": "INVALID_PARAMS"},
    ],
)
def test_rpc_requires_policy_identifying_discriminator(body: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="discriminator"):
        _register(body=body)


def test_rpc_requires_status_and_class_policy_even_with_error_type() -> None:
    class Unslugged(PolicyViolation):
        remediation = "Ask an administrator."

    with pytest.raises(TypeError, match="status"):
        classify.register(
            match={"jsonrpc_code": -32099, "body": {"error.data.policy": "acme"}},
            raises=AcmeBlocked,
        )
    with pytest.raises(ValueError, match="policy"):
        classify.register(
            match={
                "status": 200,
                "jsonrpc_code": -32099,
                "error_type": "acme",
                "body": {"error.data.policy": "acme"},
            },
            raises=Unslugged,
        )


def test_headers_patterns_type_strict_paths_and_http_opt_in() -> None:
    import re

    reg = classify.register(
        match={
            "status": 200,
            "jsonrpc_code": -32099,
            "headers": {"X-Acme-Policy": "reject"},
            "body": {"error.data.rule": re.compile(r"rule-\d+"), "error.data.flag": True},
        },
        raises=AcmeBlocked,
    )
    error = _error(data={"rule": "rule-12", "flag": True})
    assert (
        type(jsonrpc_refusal(error, status=200, headers={"x-acme-policy": "reject"})) is AcmeBlocked
    )
    assert (
        jsonrpc_refusal(
            _error(data={"rule": "rule-12", "flag": 1}),
            status=200,
            headers={"x-acme-policy": "reject"},
        )
        is None
    )
    assert type(classify(reg.example())) is AcmeBlocked
    assert (
        type(
            classify(
                httpx.Response(200, json={"error": error}, headers={"x-acme-policy": "reject"})
            )
        )
        is DonkeyError
    )
    with pytest.raises(ValueError, match="status"):
        classify.register(
            match={"status": 200, "headers": {"x-acme-policy": "reject"}}, raises=AcmeBlocked
        )


def test_specificity_ties_unregister_and_isolation(caplog: pytest.LogCaptureFixture) -> None:
    _register()
    with isolated_classify():
        reg = classify.register(
            match={
                "status": 200,
                "jsonrpc_code": -32099,
                "body": {"error.data.policy": "acme", "error.data.reason": "acme_specific"},
            },
            raises=SpecificBlocked,
        )
        assert (
            type(
                jsonrpc_refusal(
                    _error(data={"policy": "acme", "reason": "acme_specific"}), status=200
                )
            )
            is SpecificBlocked
        )
        assert classify.unregister(SpecificBlocked) == (reg,)
        classify.register(
            match={"status": 200, "jsonrpc_code": -32099, "headers": {"x-acme-policy": "reject"}},
            raises=SpecificBlocked,
        )
        with caplog.at_level(logging.WARNING):
            for _ in range(2):
                result = jsonrpc_refusal(_error(), status=200, headers={"x-acme-policy": "reject"})
                assert type(result) is PolicyViolation and result.policy == "unknown"
        assert len([r for r in caplog.records if "registrations match" in r.message]) == 1
        assert "secret" not in caplog.text
    assert type(jsonrpc_refusal(_error(), status=200)) is AcmeBlocked
    classify.unregister(AcmeBlocked)
    assert type(jsonrpc_refusal(_error(), status=200)) is PolicyViolation


def test_built_in_denial_wins_generic_registration_and_legacy_adapter_filters() -> None:
    classify.register(
        match={"status": 200, "jsonrpc_code": -32008, "headers": {"x-acme-policy": "reject"}},
        raises=AcmeBlocked,
    )
    error = {"code": -32008, "message": "Unable to access", "data": 'Access denied to: "add".'}
    result = jsonrpc_refusal(error, status=200, headers={"x-acme-policy": "reject"}, server="g/a/1")
    assert type(result) is ToolNotInProfile
    assert result.tool_name == "add" and result.server == "g/a/1" and result.profile is None
    _register()
    assert tool_access_denial(_error()) is None


def test_nested_groups_causes_context_and_cycles() -> None:
    _register()
    leaf = RuntimeError("secret")
    leaf.error = _error()
    group = RuntimeError("group")
    group.exceptions = (ValueError("other"), leaf)
    root = RuntimeError("root")
    root.__cause__ = group
    leaf.__context__ = root
    assert type(jsonrpc_refusal(root, status=200)) is AcmeBlocked
    cycle = {}
    cycle["error"] = cycle
    assert jsonrpc_refusal(cycle) is None
    root.exceptions = "malformed"
    assert type(jsonrpc_refusal(root, status=200)) is AcmeBlocked


@pytest.mark.parametrize(
    "error",
    [
        None,
        [],
        "pii",
        {"code": -32602, "message": "PII schema violation", "data": "pii_type: Email"},
        {"code": -32602, "message": "Invalid params", "data": {"reason": "INVALID_PARAMS"}},
        {
            "code": -32600,
            "message": "PII",
            "data": [{"reason": "FORBIDDEN", "metadata": {"detail": "pii"}}],
        },
        {"isError": True, "content": []},
    ],
)
def test_ordinary_diagnostic_and_generic_reason_errors_are_not_refusals(error: object) -> None:
    assert jsonrpc_refusal(error, status=403) is None


@pytest.mark.parametrize(
    "name",
    [
        "pii-request",
        "pii-response-sse",
        "schema-invalid-args",
        "schema-missing-id",
        "progressive-invalid-params",
        "abac-denied",
    ],
)
def test_mcp_captures_only_explicit_markers_or_existing_denial(name: str) -> None:
    capture = json.loads((_FIXTURES / "mcp_policies" / f"{name}.json").read_text())
    body = json.loads(capture["body"])
    result = jsonrpc_refusal(body, status=capture["status"], headers=capture["headers"])
    if name.startswith("pii-"):
        assert type(result) is PolicyViolation and result.policy == "unknown"
        assert "example.com" not in str(result)
    elif name == "abac-denied":
        assert type(result) is ToolNotInProfile
    else:
        assert result is None


@pytest.mark.parametrize(
    "name",
    [
        "v03-pii-send",
        "v03-schema-params",
        "v03-schema-method",
        "v03-schema-parse",
        "v1-pii-send",
        "v1-pii-stream",
        "v1-schema-params",
        "v1-schema-method",
        "v1-schema-parse",
    ],
)
def test_a2a_captured_diagnostics_remain_ambiguous_and_http_safe(name: str) -> None:
    body = json.loads((_FIXTURES / "a2a_policies" / f"{name}.body.json").read_text())
    assert jsonrpc_refusal(body, status=403) is None
    result = classify(httpx.Response(403, json=body))
    assert not isinstance(result, PolicyViolation)
    assert "capture@example.com" not in str(result)


@pytest.mark.parametrize(
    "status,headers,kind",
    [
        (401, {}, AuthError),
        (403, {"www-authenticate": "Bearer"}, AuthError),
        (429, {}, TokenBudgetExceeded),
    ],
)
def test_http_auth_and_rate_limits_win(status: int, headers: dict[str, str], kind: type) -> None:
    assert type(classify(httpx.Response(status, json=_envelope(_error()), headers=headers))) is kind
    assert jsonrpc_refusal(_error(), status=status, headers=headers) is None


def test_normal_http_registrations_do_not_claim_rpc_and_success_stays_success_shape() -> None:
    classify.register(
        match={"status": 403, "headers": {"x-acme-policy": "reject"}}, raises=AcmeBlocked
    )
    rpc = httpx.Response(
        403, json=_envelope(_error(data=None)), headers={"x-acme-policy": "reject"}
    )
    assert not isinstance(classify(rpc), PolicyViolation)
    normal = httpx.Response(
        403, json={"error": {"code": "acme"}}, headers={"x-acme-policy": "reject"}
    )
    assert type(classify(normal)) is AcmeBlocked
    assert (
        type(classify(httpx.Response(200, json={"jsonrpc": "2.0", "result": {"ok": True}})))
        is DonkeyError
    )


def test_envelope_body_paths_duplicates_and_contradictions() -> None:
    reg = _register(body={"error.data.policy": "acme", "tenant.policy": "acme"})
    body = {**_envelope(_error()), "tenant": {"policy": "acme"}}
    assert type(jsonrpc_refusal(body, status=200)) is AcmeBlocked
    assert type(classify(reg.example())) is AcmeBlocked
    with pytest.raises(ValueError, match="Duplicate"):
        _register(body={"error.data.policy": "acme", "tenant.policy": "acme"})
    with pytest.raises(ValueError, match="Contradictory"):
        _register(body={"error.data.policy": "acme", "error.code": -1})


@pytest.mark.parametrize("status,kind", [(401, AuthError), (429, TokenBudgetExceeded)])
def test_http_precedence_never_uses_llm_body_discriminators(status: int, kind: type) -> None:
    error = _error(type="pii_detected")
    assert type(classify(httpx.Response(status, json=_envelope(error)))) is kind


def test_generic_rpc_registration_and_malformed_types_are_rejected() -> None:
    with pytest.raises(ValueError, match="discriminator"):
        _register(body={}, headers={"content-type": "application/json"})
    for message in (None, True, [], {}):
        assert jsonrpc_refusal(_error(message=message), status=200) is None
    for status in (True, "200", 500):
        assert jsonrpc_refusal(_error(), status=status) is None


def test_registration_cannot_shadow_exact_builtin() -> None:
    with pytest.raises(ValueError, match="tool_access"):
        classify.register(
            match={
                "status": 200,
                "jsonrpc_code": -32008,
                "body": {"error.message": "Unable to access"},
                "headers": {"x-acme-policy": "reject"},
            },
            raises=AcmeBlocked,
        )


def test_rpc_example_cannot_contradict_protocol_version() -> None:
    with pytest.raises(ValueError, match="Contradictory"):
        _register(body={"error.data.policy": "acme", "jsonrpc": "1.0"})


def test_builtin_precedes_custom_across_group_leaves() -> None:
    _register()
    custom = RuntimeError("custom")
    custom.error = _error()
    denied = RuntimeError("denied")
    denied.error = {"code": -32008, "message": "Unable to access"}
    group = RuntimeError("group")
    group.exceptions = (denied, custom)
    assert type(jsonrpc_refusal(group, status=200, tool="add")) is ToolNotInProfile


def test_captured_sse_poisoning_diagnostics_are_not_policy_identity() -> None:
    capture = json.loads((_FIXTURES / "mcp_policies" / "poisoning-block-list.json").read_text())
    # BG §2.2: framing is already decoded by the caller, not a new protocol parser.
    body = json.loads(capture["body"].split("data: ", 1)[1])
    assert jsonrpc_refusal(body, status=200) is None


def test_internal_http_dispatch_matches_public_and_redacts_ordinary_errors() -> None:
    from donkey_kit.core.errors import classify as internal_classify

    _register(403)
    response = httpx.Response(403, json=_envelope(_error()))
    assert type(internal_classify(response)) is AcmeBlocked
    for dispatch in (classify, internal_classify):
        result = dispatch(httpx.Response(403, json=_envelope(_error(data=None))))
        assert not isinstance(result, PolicyViolation)
        assert "secret@example.com" not in str(result)


@pytest.mark.parametrize(
    "body",
    [
        {"error.data.foo": "acme"},
        {"error.data.@type": "type.googleapis.com/google.rpc.ErrorInfo"},
        {"error.data.domain": "acme.example"},
        {"error.data.reason": re.compile("FORBIDDEN|acme")},
        {"error.data.policy": ""},
        {"error.data.reason": "pii_type: Email"},
        {"error.data.reason": re.compile("pii_type: Email")},
        {"tenant.policy": "acme"},
    ],
)
def test_nonidentifying_rpc_conditions_are_rejected(body: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="discriminator"):
        _register(body=body)


@pytest.mark.parametrize(
    "extra",
    [
        {"jsonrpc": "1.0"},
        {"id": True},
        {"id": []},
        {"result": {}},
        {"isError": True},
    ],
)
def test_invalid_explicit_envelopes_do_not_descend(extra: dict[str, object]) -> None:
    _register(403)
    body = {**_envelope(_error()), **extra}
    assert jsonrpc_refusal(body, status=403) is None
    result = classify(httpx.Response(403, json=body))
    assert not isinstance(result, PolicyViolation)
    assert "secret@example.com" not in str(result)


def test_duck_error_type_is_normalized_for_all_input_forms() -> None:
    _register(error_type="acme_error")
    error = _error(type="acme_error")
    duck = SimpleNamespace(**error)
    wrapped = RuntimeError("wrapper")
    wrapped.error = duck
    for source in (error, duck, _envelope(duck), wrapped):
        assert type(jsonrpc_refusal(source, status=200)) is AcmeBlocked


def test_missing_id_and_malformed_builtin_code_do_not_claim_policy() -> None:
    body = {"jsonrpc": "2.0", "error": _error()}
    assert jsonrpc_refusal(body, status=403) is None
    body = _envelope(_error(code="agent_killed"))
    result = classify(httpx.Response(403, json=body))
    assert not isinstance(result, PolicyViolation)
    assert "secret@example.com" not in str(result)


def test_http_builtin_headers_and_5xx_keep_precedence() -> None:
    from donkey_kit.core.errors import PromptInjectionBlocked, UpstreamModelError

    _register(403)
    response = httpx.Response(
        403, json=_envelope(_error()), headers={"x-injection-protection": "blocked"}
    )
    assert type(classify(response)) is PromptInjectionBlocked
    assert type(classify(httpx.Response(503, json=_envelope(_error())))) is UpstreamModelError
    with pytest.raises(ValueError, match="injection_protection"):
        _register(400, headers={"x-injection-protection": "blocked"})


def test_rpc_simulation_supports_error_http_statuses_but_not_200() -> None:
    from donkey_kit.simulator.inject import resolve_fixture

    _register(403)
    assert resolve_fixture(AcmeBlocked).status == 403
    classify.unregister(AcmeBlocked)
    _register(200)
    with pytest.raises(ValueError, match="HTTP 200"):
        resolve_fixture(AcmeBlocked)


def test_helper_has_canonical_public_exports() -> None:
    import donkey_kit
    import donkey_kit.core

    assert donkey_kit.jsonrpc_refusal is jsonrpc_refusal
    assert donkey_kit.core.jsonrpc_refusal is jsonrpc_refusal
    assert "jsonrpc_refusal" in donkey_kit.__all__
    assert "jsonrpc_refusal" in donkey_kit.core.__all__


def test_403_rpc_simulation_runs_handler_and_bridges_framework_error() -> None:
    from donkey_kit import Donkey
    from donkey_kit.core.config import DonkeyConfig
    from donkey_kit.core.refusals import translate

    _register(403)
    seen = []
    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy.example/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
        max_retries=0,
    )
    with Donkey(cfg) as donkey:
        donkey.on(AcmeBlocked).call(lambda ctx: seen.append(ctx.violation))
        with donkey.simulate(AcmeBlocked):
            response = donkey.http_client(sync=True).post(
                "https://proxy.example/chat/completions", json={"model": "m"}
            )
        wrapped = RuntimeError("framework")
        wrapped.response = response
        wrapped.status_code = 403
        assert type(translate(wrapped)) is AcmeBlocked
    assert [type(error) for error in seen] == [AcmeBlocked]


def test_rpc_builtin_pii_never_renders_or_exposes_raw_message(caplog) -> None:
    from donkey_kit.core.errors import PIIDetected
    from donkey_kit.core.errors import classify as internal_classify

    message = '[{"pii_type":"secret@example.com","value":"other secret"}]'
    response = httpx.Response(403, json=_envelope(_error(type="pii_detected", message=message)))
    for dispatch in (classify, internal_classify):
        with caplog.at_level(logging.DEBUG):
            result = dispatch(response)
        assert type(result) is PIIDetected
        assert result.response is response
        assert result.entities == []
        assert not result.gateway_message
        assert "secret" not in str(result)
    assert "secret" not in caplog.text


@pytest.mark.parametrize(
    "leaf",
    [
        "NOT_FOUND",
        "INVALID_ARGUMENT",
        "not_found",
        "invalid_argument",
        re.compile("acme"),
        re.compile("acme|pii_type: Email"),
        re.compile(".*"),
    ],
)
@pytest.mark.parametrize("path", ["error.data.policy", "error.data.reason"])
def test_rpc_identity_requires_exact_nongeneric_literal(path: str, leaf: object) -> None:
    with pytest.raises(ValueError, match="discriminator"):
        _register(body={path: leaf})


@pytest.mark.parametrize("identity", ["policy", "header"])
def test_reason_patterns_can_refine_an_independent_identity(identity: str) -> None:
    body = {"error.data.reason": re.compile("acme|pii_type: Email")}
    headers = {}
    if identity == "policy":
        body["error.data.policy"] = "acme"
    else:
        headers["x-acme-policy"] = "reject"
    _register(body=body, headers=headers)
    error = _error(data={"policy": "acme", "reason": "pii_type: Email"})
    assert type(jsonrpc_refusal(error, status=200, headers=headers)) is AcmeBlocked


@pytest.mark.parametrize(
    "header",
    [
        "x-frame-options",
        "x-content-type-options",
        "x-xss-protection",
        "x-request-id",
        "x-correlation-id",
        "x-powered-by",
        "x-forwarded-for",
        "x-cache",
    ],
)
def test_standard_x_headers_are_not_policy_identity(header: str) -> None:
    with pytest.raises(ValueError, match="discriminator"):
        _register(body={}, headers={header: "nosniff"})


@pytest.mark.parametrize(
    "metadata",
    [
        {"jsonrpc": "1.0"},
        {"jsonrpc": "2.0", "id": True},
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"isError": True, "content": []},
        {"result": {}},
    ],
)
def test_duck_envelope_metadata_is_validated_before_descending(metadata: dict) -> None:
    _register()
    source = SimpleNamespace(error=_error(), **metadata)
    assert jsonrpc_refusal(source, status=200) is None
    assert type(jsonrpc_refusal(SimpleNamespace(error=_error()), status=200)) is AcmeBlocked
    assert type(jsonrpc_refusal(SimpleNamespace(**_envelope(_error())), status=200)) is AcmeBlocked


@pytest.mark.parametrize("status", [200, 400, 403, 429])
def test_rpc_auth_challenge_is_auth_except_for_rate_limit(status: int) -> None:
    from donkey_kit.core.errors import classify as internal_classify

    response = httpx.Response(
        status, json=_envelope(_error()), headers={"WWW-Authenticate": "Bearer"}
    )
    for dispatch in (classify, internal_classify):
        result = dispatch(response)
        assert type(result) is (TokenBudgetExceeded if status == 429 else AuthError)
        assert result.response is response
        assert "secret@example.com" not in str(result)


def test_rpc_regex_builtin_keeps_discriminator_not_raw_pattern_labels() -> None:
    from donkey_kit.core.errors import PromptInjectionBlocked

    body = {**_envelope(_error()), "matched_patterns": ["secret@example.com"]}
    response = httpx.Response(403, json=body)
    result = classify(response)
    assert type(result) is PromptInjectionBlocked
    assert result.policy == "regex-prompt-guard"
    assert "secret@example.com" not in str(result)
    assert response.json() == body


def test_rpc_request_rate_limit_wins_auth_challenge_and_pii_body() -> None:
    from donkey_kit.core.errors import RequestRateLimitExceeded

    response = httpx.Response(
        429,
        json=_envelope(_error(type="pii_detected")),
        headers={
            "www-authenticate": "Bearer",
            "x-ratelimit-limit": "10",
            "x-ratelimit-remaining": "0",
        },
    )
    assert type(classify(response)) is RequestRateLimitExceeded
