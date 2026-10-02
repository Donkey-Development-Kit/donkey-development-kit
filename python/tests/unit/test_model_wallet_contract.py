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

The wallet spend-state / exhaustion captures (2026-09-30, instance 21189395,
#301) are pinned at the bottom of this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from donkey_kit.core.budget import Budget
from donkey_kit.core.errors import TokenBudgetExceeded, classify
from donkey_kit.simulator.fixtures import parse_headers

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "model_wallet"
_TOKEN_WINDOW_429 = parse_headers(
    (FIXTURES.parent / "llm_proxy" / "reject.token-rate-limit.headers.txt").read_text()
)


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


# --- wallet spend state + exhaustion (docs/verified-apis.md §4, #301) --------
# A live run drove `ddk-model-wallet`'s 2000 tokens/day openai budget to refusal.
# These pin what the wire does and does NOT carry, so #315 builds against the
# observed contract rather than the FinOps roadmap's description of it.

_BUDGET_HEADER_KEYS = (
    "x-token-limit",
    "x-token-remaining",
    "x-token-reset",
    "x-llm-proxy-ratelimit",
)


def test_wallet_success_carries_no_in_band_wallet_state() -> None:
    """§4 wallet row: a 200 through a wallet names the matched wallet and nothing
    else — no limit/remaining/spent value, no currency, and neither of the
    budget-window shapes `Budget.observe()` parses."""
    for which in ("first", "last"):
        h = _headers(f"responses.exhaustion-run.{which}.headers.txt")
        assert h["x-model-wallet-selected"] == "ddk-model-wallet"
        assert not any(k in h for k in _BUDGET_HEADER_KEYS)
        wallet_keys = {k for k in h if "wallet" in k or k.startswith("x-mw-")}
        assert wallet_keys == {"x-model-wallet-selected"}


def test_no_threshold_warning_precedes_the_wallet_refusal() -> None:
    """§4 wallet row: every 200 in the run up to the refusal carries the same
    header keys as the first — no warning appears as the budget is approached."""
    run = _load("probe.exhaustion-run.json")
    assert isinstance(run, dict)
    calls = run["calls"]
    ok = [c for c in calls if c["status"] == 200]
    refused = [c for c in calls if c["status"] != 200]
    assert refused and all(c["status"] == 429 for c in refused)
    assert calls.index(refused[0]) == len(ok)  # all successes precede the first refusal
    # budgets are approximate: the refusal lands after the limit is overshot
    assert ok[-1]["cumulative_total_tokens"] > run["wallet_limit"]["value"]
    assert all(c["header_keys"] == ok[0]["header_keys"] for c in ok)


def test_wallet_exhaustion_429_is_distinguishable_from_token_window_429() -> None:
    """§4 wallet row: the wallet refusal is a 429 with a flat-string body, a
    standard `retry-after` (seconds) and `x-model-wallet-selected` — and none of
    the `x-token-*` trio. The token-window 429 (../llm_proxy/) is the inverse:
    empty body, the trio, no `retry-after`."""
    h = _headers("reject.wallet-exhausted.headers.txt")
    body = _load("reject.wallet-exhausted.body.json")
    assert h["x-model-wallet-selected"] == "ddk-model-wallet"
    assert h["retry-after"] == "86400"
    assert h["content-type"] == "text/plain"
    assert not any(k in h for k in _BUDGET_HEADER_KEYS)
    assert body == {"error": "token rate limit exceeded"}

    window = _TOKEN_WINDOW_429
    assert "retry-after" not in window
    assert "x-model-wallet-selected" not in window
    assert {"x-token-limit", "x-token-remaining", "x-token-reset"} <= set(window)


def test_wallet_exhaustion_classifies_as_token_budget_exceeded_today() -> None:
    """Characterises current behaviour for #315: the wallet 429 lands in the
    generic 429 branch (TokenBudgetExceeded, retry_after from `retry-after`), and
    `Budget.observe()` is a no-op because no budget-window header is present."""
    h = _headers("reject.wallet-exhausted.headers.txt")
    body = (FIXTURES / "reject.wallet-exhausted.body.json").read_bytes()
    resp = httpx.Response(429, headers=h, content=body)
    err = classify(resp)
    assert isinstance(err, TokenBudgetExceeded)
    assert err.retry_after == 86400.0

    budget = Budget()
    budget.observe(resp)
    assert budget.observed_at is None


def test_wallet_is_enforced_by_mw_policies_with_no_threshold_setting() -> None:
    """§4 wallet row: wallets materialise into the auto-attached `mw-find-key-policy`
    (keyed on arbitrary `jwtClaim` predicates) and are enforced by
    `mw-token-rate-limit-policy` — neither config carries a threshold/alert."""
    doc = _load("mw-policies.json")
    assert isinstance(doc, dict)
    by_asset = {p["assetId"]: p for p in doc["policies"]}
    assert set(by_asset) == {"mw-find-key-policy", "mw-token-rate-limit-policy"}
    keys = by_asset["mw-find-key-policy"]["configurationData"]["clientKeys"]
    assert {p["type"] for k in keys for p in k["predicates"]} == {"jwtClaim"}
    blob = json.dumps(doc["policies"]).lower()
    assert not any(word in blob for word in ("threshold", "alert", "warn"))
