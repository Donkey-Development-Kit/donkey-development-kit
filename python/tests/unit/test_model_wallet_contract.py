"""Pins the model-wallet JWT ingress contract to a LIVE capture from a real
wallet-backed proxy (`ddk-model-wallet`, instance 21186246) deployed to the
shared omni Flex Gateway and called end-to-end on 2026-09-21. See
tests/fixtures/anypoint/model_wallet/README.md and docs/verified-apis.md §2/§3
(#372).

This is the PARALLEL ingress model to the `client_id`/`client_secret` pair in
test_llm_proxy_contract.py: a wallet-backed proxy identifies the caller from an
IdP-issued JWT (`Authorization: Bearer`) + the wallet-selector `X-Client-Id`
header, with NO `client_secret`. These tests pin the observed request/response
*shape*; wiring the raw JWT-rejection → exception mapping into
core/errors.classify() (and the SDK-side auth mode) is #509, deliberately not
done here.
"""

from __future__ import annotations

import json
from pathlib import Path

from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "model_wallet"


def _headers(name: str) -> dict[str, str]:
    return parse_headers((FIXTURES / name).read_text())


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def test_success_body_is_openai_chat_completion_passthrough_with_usage() -> None:
    """docs/verified-apis.md §2: a wallet proxy returns the OpenAI object verbatim,
    incl. the token usage block that the wallet counts against its budget."""
    body = _load("responses.success.body.json")
    assert isinstance(body, dict)
    assert body["object"] == "chat.completion"
    assert body["model"].startswith("gpt-5-mini")
    usage = body["usage"]
    assert {"prompt_tokens", "completion_tokens", "total_tokens"} <= set(usage)
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_success_headers_echo_matched_wallet_and_no_auth_challenge() -> None:
    """docs/verified-apis.md §3: on a wallet hit the gateway echoes which wallet
    matched (`x-model-wallet-selected`) alongside the usual routing headers, and
    there is NO `www-authenticate` challenge — the JWT + X-Client-Id passed."""
    h = _headers("responses.success.headers.txt")
    assert h["server"] == "Anypoint Flex Gateway"
    assert h["x-model-wallet-selected"] == "ddk-model-wallet"
    assert h["x-llm-proxy-llm-provider"] == "openai"
    assert h["x-llm-proxy-routing-type"] == "ModelBased"
    assert h["x-envoy-decorator-operation"].startswith("api-instance-21186246.")
    assert "www-authenticate" not in h


def test_request_carries_bearer_jwt_and_x_client_id_but_no_secret() -> None:
    """docs/verified-apis.md §2: the caller sends `Authorization: Bearer <JWT>` +
    `X-Client-Id`, and NO client_id/client_secret request-header pair — the CIE
    and DataWeave Headers Transformation policies are disabled on a wallet proxy.
    (`Authorization: Bearer` was an assumption in the doc read; the live capture
    confirms it.)"""
    req = (FIXTURES / "request.success.http").read_text().lower()
    assert "authorization: bearer" in req
    assert "x-client-id: ddk-model-wallet" in req
    assert "client_secret" not in req
    # the CIE header pair must not appear as request headers on this ingress
    assert "\nclient_id:" not in req


def test_missing_jwt_is_flat_string_400_with_bearer_challenge() -> None:
    """docs/verified-apis.md §3 live capture: no `Authorization` header → 400 with a
    flat-string Anypoint envelope and `www-authenticate: Bearer`, and NO
    `x-llm-proxy-*` routing headers (rejected before routing). Pins the shape
    classify() will type in #509; no classify() assertion here."""
    h = _headers("reject.jwt-missing.headers.txt")
    body = _load("reject.jwt-missing.body.json")
    assert h["www-authenticate"] == "Bearer"
    assert not any(k.startswith("x-llm-proxy-") for k in h)
    assert body == {"error": "JWT Token is required."}
    assert isinstance(body["error"], str)


def test_invalid_jwt_is_flat_string_401_with_bearer_challenge() -> None:
    """docs/verified-apis.md §3 live capture: a malformed/unverifiable/expired Bearer
    token → 401, flat-string envelope `{"error":"Invalid token."}`, and
    `www-authenticate: Bearer` (distinct from the CIE path's
    `www-authenticate: Client-ID-Enforcement`)."""
    h = _headers("reject.jwt-invalid.headers.txt")
    body = _load("reject.jwt-invalid.body.json")
    assert h["www-authenticate"] == "Bearer"
    assert body == {"error": "Invalid token."}
    assert isinstance(body["error"], str)


def test_wallet_definition_predicates_match_the_jwt_claims() -> None:
    """docs/verified-apis.md §3: the caller is the JWT whose claims satisfy every
    wallet predicate (group=ddk AND client_id=ddk-model-wallet-client), and the
    wallet selector value equals its generated clientId."""
    wallet = _load("wallet.definition.json")
    claims = _load("jwt.claims.json")
    assert wallet["clientId"] == "ddk-model-wallet"
    preds = {p["claim"]: p["values"] for p in wallet["predicates"]}
    for claim, values in preds.items():
        assert claims[claim] in values
