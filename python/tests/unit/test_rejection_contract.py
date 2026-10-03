"""The nine documented rejection shapes (#181, +#289, +#694), asserted from the shared
``tests/fixtures/rejections/`` index so the local gateway simulator (#187) can
replay the identical files and any contract drift fails both at once (AC #4).

Rows 1/2/5 alias the live captures under ``anypoint/llm_proxy/`` (referenced, not
moved); rows 3/4/6 (injection, content-moderation, upstream-5xx) and rows 7/8
(regex-prompt-guard, content-safety — #289) live in ``rejections/``. Rows 3/7/8
are now LIVE-VERIFIED: row 3 (injection-protection, ``x-injection-protection:
blocked``) was captured 2026-09-27 (#669) against ``ddk-injection-protection``
(instance 21200898) — a real 79-byte body, replacing the honest empty
placeholder; rows 7/8 were captured 2026-09-22 (#253) — the regex-prompt-guard
body matched the committed fixture byte-for-byte against ``ddk-injection-guard``
(instance 21179713), and the content-safety discriminator headers + body shape
were confirmed against ``ddk-azure-content-safety`` (instance 21180957) — see
``rejections/README.md`` and docs/verified-apis.md §4. Row 4 (fall-through)
stays documented-only: it is a synthetic minimal 4xx proving the fall-through
stays generic, not a captured policy shape (#253). Row 9 (Agent Kill Switch,
nested ``error.code == "agent_killed"``) was live-captured 2026-09-29 against
``ddk-agent-kill-switch`` (instance 21206201, #694).
The discriminator is the error ``type`` + specific headers, NEVER the status code
alone — which is exactly why rows 7/8 (both 403) must not be swallowed by the
401/403 → auth rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from donkey_kit.core.errors import (
    AgentKilled,
    AuthError,
    ContentSafetyBlocked,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
    UpstreamModelError,
    UpstreamRequestError,
    classify,
)
from donkey_kit.simulator.fixtures import parse_headers

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REJECTIONS = _FIXTURES / "rejections"
LIVE = _FIXTURES / "anypoint" / "llm_proxy"


def _headers(path: Path) -> dict[str, str]:
    """Delegate to the simulator's shared loader so the classify() contract and
    the #187 replay parse the identical files with the identical rule — BG §1.4's
    "same files, both fail together" holds by construction, not convention."""
    return parse_headers(path.read_text())


def _response(base: Path, slug: str, status: int) -> httpx.Response:
    """Build an httpx.Response from a ``reject.<slug>`` fixture pair. An empty
    body is a real captured shape (``.body.empty``), never a guessed body."""
    headers = _headers(base / f"reject.{slug}.headers.txt")
    body_json = base / f"reject.{slug}.body.json"
    if body_json.exists():
        return httpx.Response(status, headers=headers, json=json.loads(body_json.read_text()))
    return httpx.Response(status, headers=headers)  # .body.empty → no body


def test_row1_token_rate_limit_is_token_budget() -> None:
    err = classify(_response(LIVE, "token-rate-limit", 429))
    assert isinstance(err, TokenBudgetExceeded)
    assert err.policy == "token-rate-limit"


def test_row2_pii_detection_is_pii_not_auth() -> None:
    err = classify(_response(LIVE, "pii-detected", 403))
    assert isinstance(err, PIIDetected)
    assert err.policy == "pii-detection"


def test_row3_injection_protection_is_prompt_injection_blocked() -> None:
    err = classify(_response(REJECTIONS, "injection-protection", 400))
    assert isinstance(err, PromptInjectionBlocked)
    assert err.policy == "prompt-injection-protection"
    assert err.remediation  # required, non-empty
    # #669: the live-captured body is carried on .response even though
    # classify() types this shape from the header alone, not the body.
    assert err.response is not None
    assert err.response.json() == {
        "message": "Injection attack detected - Rule: 'SQL Injection', Location: Body"
    }


def test_row4_content_moderation_falls_through_to_generic_policy_violation() -> None:
    # Under-documented shape: a plain 4xx with no nested error and no injection
    # header must stay a generic PolicyViolation — not be misrouted to
    # PromptInjectionBlocked or UpstreamRequestError (ordering guard).
    err = classify(_response(REJECTIONS, "content-moderation", 400))
    assert type(err) is PolicyViolation
    assert err.policy == "unknown"


def test_row5_upstream_4xx_is_upstream_request_error() -> None:
    body = json.loads((LIVE / "reject.model-not-found.body.json").read_text())
    err = classify(httpx.Response(400, json=body))
    assert isinstance(err, UpstreamRequestError)
    assert not isinstance(err, PolicyViolation)


def test_row6_upstream_5xx_is_retryable_upstream_model_error() -> None:
    err = classify(_response(REJECTIONS, "upstream-5xx", 503))
    assert isinstance(err, UpstreamModelError)


def test_row7_regex_prompt_guard_is_prompt_injection_not_auth() -> None:
    # A 403 with a top-level matched_patterns list is a Regex Prompt Guard block,
    # not an auth failure — it must be typed BEFORE the 401/403 → auth rule (#289).
    err = classify(_response(REJECTIONS, "regex-prompt-guard", 403))
    assert isinstance(err, PromptInjectionBlocked)
    assert err.policy == "regex-prompt-guard"
    assert err.remediation  # required, non-empty


def test_row8_content_safety_is_content_safety_blocked_not_auth() -> None:
    # A 403 with a vendor `...-action: reject` header is a content-safety /
    # guardrails block, not an auth failure — typed BEFORE the 401/403 → auth rule
    # and keyed on the header so a body-less Bedrock reject is still caught (#289).
    err = classify(_response(REJECTIONS, "content-safety", 403))
    assert isinstance(err, ContentSafetyBlocked)
    assert err.policy == "content-safety"
    # Flagged reasons are parsed from the sibling `...-reason` header. The values
    # are the live-captured set (Azure returned severity_hate,severity_violence
    # on a hate-speech probe against ddk-azure-content-safety, #253).
    assert err.categories == ["severity_hate", "severity_violence"]
    assert err.remediation  # required, non-empty


def test_row8_bedrock_guardrails_variant_is_content_safety_blocked() -> None:
    # The Bedrock Guardrails sibling of row 8: a different vendor header family
    # (x-llm-proxy-bedrock-guardrail-*) and a single `content_filter` category,
    # live-captured against ddk-bedrock-guardrails on 2026-09-24 (#568). It must
    # type identically to the Azure capture — same ContentSafetyBlocked, keyed on
    # the `...-action: reject` header — which exercises the bedrock branch of
    # _CONTENT_SAFETY_VENDORS the Azure fixture never reaches. This capture is a
    # classify()-contract sibling of row 8, not a simulator-served shape, so it is
    # deliberately absent from fixtures.lock (see rejections/README.md).
    err = classify(_response(REJECTIONS, "content-safety-bedrock", 403))
    assert isinstance(err, ContentSafetyBlocked)
    assert err.policy == "content-safety"
    assert err.categories == ["content_filter"]
    assert err.remediation  # required, non-empty


def test_row9_agent_kill_switch_is_agent_killed_not_upstream_or_auth() -> None:
    # docs/verified-apis.md §4, live capture 2026-09-29 (#694): the kill switch
    # rejects with a 403 whose nested error carries `code: agent_killed` and no
    # `type`. Before #694 it fell into the generic 4xx branch as an
    # UpstreamRequestError — wrong twice over: the upstream was never called, and
    # the agent was shut off by an administrator, not a fixable request mistake.
    body = json.loads((REJECTIONS / "reject.agent-killed.body.json").read_text())
    assert body["error"]["code"] == "agent_killed"
    assert "type" not in body["error"]
    headers = _headers(REJECTIONS / "reject.agent-killed.headers.txt")
    assert "www-authenticate" not in headers  # discriminator vs. auth

    request = httpx.Request(
        "POST",
        "https://gw.example/ddk-agent-kill-switch/chat/completions",
        headers={"x-correlation-id": "00000000-0000-4000-8000-ede8f2783ba7"},
    )
    err = classify(httpx.Response(403, headers=headers, json=body, request=request))
    assert isinstance(err, AgentKilled)
    assert isinstance(err, PolicyViolation)
    assert not isinstance(err, (AuthError, UpstreamRequestError, UpstreamModelError))
    assert err.policy == "agent-kill-switch"
    assert err.remediation.strip()  # required, non-empty
    assert "Governance > Security" in err.remediation
    assert str(err) == "This agent has been blocked by an active kill switch."
    assert err.correlation_id == "00000000-0000-4000-8000-ede8f2783ba7"
