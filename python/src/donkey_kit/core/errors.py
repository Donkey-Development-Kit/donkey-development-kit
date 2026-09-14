"""Exception taxonomy (§2.4).

Mapping gateway policy rejections to catchable, actionable exceptions is the
SDK's clearest value over raw HTTP.

Two design points that matter:

1. :class:`PolicyViolation` must be distinguishable from a transient error at
   the framework boundary so host frameworks do not silently retry a refusal.
   It is NEVER retried by our transport.
2. ``remediation`` is a required, human-readable next step — worth more than a
   stack trace.

The concrete HTTP-response → exception mapping lives in :func:`classify`, which
is driven by a table that MUST be populated from real captured fixtures (§8.2),
not hand-written guesses. Until fixtures exist, :func:`classify` maps only the
status-code families it can defensibly infer and otherwise returns a generic
:class:`DonkeyError`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import _verify

if TYPE_CHECKING:
    from datetime import datetime

    import httpx


class DonkeyError(Exception):
    """Base for all SDK errors. Carries correlation/call/request IDs and the raw
    response so callers can inspect what actually happened.

    Three ids, three provenances (§2.3, #195):

    * ``correlation_id`` — the RUN id the client sent (``X-Correlation-Id``),
      shared by every request in a ``donkey.run()`` block. This is the
      client↔gateway join key, and it always equals the header that was sent.
    * ``call_id`` — the per-request id the client sent (``X-Donkey-Request-Id``),
      unique per logical request, stable across its retries. It pinpoints one
      request within a run and exists even when the request fails before any
      response.
    * ``request_id`` — the gateway's OWN id, read back from the ``x-request-id``
      RESPONSE header. Absent on a transport error (no response), unlike the two
      client-sent ids above.
    """

    def __init__(
        self,
        message: str,
        *,
        correlation_id: str | None = None,
        call_id: str | None = None,
        request_id: str | None = None,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.correlation_id = correlation_id
        self.call_id = call_id
        self.request_id = request_id
        self.response = response


class ConfigError(DonkeyError):
    """Configuration is missing or invalid. Reports ALL problems at once (§2.1)."""


class AuthError(DonkeyError):
    """401/403 on the control plane."""


class PolicyViolation(DonkeyError):
    """Base for gateway-enforced refusals. NEVER retried.

    ``remediation`` is required: it names the concrete next step, e.g.
    "Token budget exceeded for business group `finance`; limit resets in 42m;
    request an increase in API Manager".
    """

    policy: str = "unknown"

    def __init__(
        self,
        message: str,
        *,
        remediation: str,
        policy: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.remediation = remediation
        if policy is not None:
            self.policy = policy


class TokenBudgetExceeded(PolicyViolation):
    policy = "token-rate-limit"

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class BudgetReserveReached(DonkeyError):
    """Raised by :meth:`Budget.pace` *before* it lets a request through that would
    cross the caller's reserve (§1.3, #186).

    Deliberately NOT a :class:`PolicyViolation`: that base marks a gateway-enforced
    refusal that is terminal and never retried. This is the client-side opposite —
    a pre-emptive signal, raised locally from observed budget headers before any
    request is sent, that the caller is *expected* to recover from (typically
    ``await budget.wait_for_reset()`` then continue). Classifying it as a refusal
    would misrepresent it at the framework boundary.

    Carries the observed budget state at the moment pacing tripped so a handler can
    decide without re-reading the object: ``fraction_used`` (0.0-1.0), the
    ``reserve`` that was requested, and ``reset_at`` (``None`` if the window has not
    been observed yet)."""

    def __init__(
        self,
        message: str,
        *,
        fraction_used: float,
        reserve: float,
        reset_at: datetime | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.fraction_used = fraction_used
        self.reserve = reserve
        self.reset_at = reset_at


class PromptInjectionBlocked(PolicyViolation):
    policy = "prompt-injection-protection"


class ContentSafetyBlocked(PolicyViolation):
    """A provider-backed content-moderation policy (Azure Content Safety or
    Amazon Bedrock Guardrails) blocked the request or response.

    ``categories`` carries the flagged reasons the gateway reports in its
    ``x-llm-proxy-<vendor>-...-reason`` header (e.g. ``severity_hate``,
    ``blocklist``, ``denied_topic``) — the moderation analog of
    :attr:`PIIDetected.entities`. Empty when the gateway reported no reason."""

    policy = "content-safety"

    def __init__(self, message: str, *, categories: list[str] | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.categories = categories or []


class PIIDetected(PolicyViolation):
    policy = "pii-detection"

    def __init__(self, message: str, *, entities: list[str] | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.entities = entities or []


class UpstreamModelError(DonkeyError):
    """Provider-side failure (5xx). Retryable."""


class UpstreamRequestError(DonkeyError):
    """The upstream provider rejected the request (4xx), passed through the
    gateway verbatim (e.g. OpenAI ``model_not_found``). This is a client-side
    mistake, NOT a gateway policy refusal and NOT a provider outage, so it is
    terminal (never retried) but distinct from :class:`PolicyViolation`.

    Carries the provider's own ``code``/``type``/``param`` when present so the
    caller can act (fix the model, the params, etc.)."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        error_type: str | None = None,
        param: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.code = code
        self.error_type = error_type
        self.param = param


class ToolInvocationError(DonkeyError):
    """An MCP tool call failed."""


class RegistryError(DonkeyError):
    """Exchange discovery / resolution failed."""


class ProvisioningError(DonkeyError):
    """plan/apply/drift failed."""


class GovernanceDrift(DonkeyError):
    """resolve(): a declared policy is not actually applied on the gateway (§6.3)."""


class PublicationDrift(DonkeyError):
    """verify(): the live server no longer matches the Exchange descriptor (§7.4)."""


def classify(
    response: httpx.Response,
    *,
    correlation_id: str | None = None,
    call_id: str | None = None,
) -> DonkeyError:
    """Map an HTTP error response to a specific exception.

    ``correlation_id`` (the run id) and ``call_id`` (the per-request id) are the
    two ids the client sent (§2.3, #195). When not passed explicitly they are
    read back from the response's own request headers, so a caller bridging an
    ``openai.APIStatusError`` — ``classify(err.response)`` — gets a
    :class:`DonkeyError` whose ``correlation_id`` equals the header that was sent
    with no extra wiring. An explicit argument (e.g. when the header name was
    overridden via config) always wins over the auto-derived value.

    The precise policy discrimination (§2.4, working instruction #4) is driven by
    real rejection captures from a live governed LLM proxy (docs §4,
    ``tests/fixtures/anypoint/llm_proxy/reject.*``), NOT hand-written guesses.
    What the captures established:

    * **PII detection** rejects with **403** and a *nested* error object whose
      ``type`` is ``"pii_detected"`` (and, unlike a genuine auth failure, NO
      ``www-authenticate`` header). So a 403 is NOT automatically an auth error —
      the error ``type`` is checked first.
    * **Token rate limit** rejects with **429** and an **empty body**; the budget
      state is entirely in headers (``x-token-limit`` / ``x-token-remaining`` /
      ``x-token-reset`` in ms). There is NO standard ``retry-after``.
    * **client-id-enforcement** rejects with **401** + a *flat-string* ``error``
      and a ``www-authenticate`` header → auth.
    * **Upstream provider passthrough** (e.g. OpenAI ``model_not_found``) is a
      non-429 4xx with a nested error object carrying ``code``/``type``/``param``.
    * **Injection protection** rejects with the ``x-injection-protection:
      blocked`` header (the header, not the status, is the discriminator; #181)
      → :class:`PromptInjectionBlocked`.
    * **Regex Prompt Guard** rejects with **403** and a *flat-string* ``error``
      plus a top-level ``matched_patterns`` list → :class:`PromptInjectionBlocked`
      (``policy="regex-prompt-guard"``). Keyed on ``matched_patterns`` and checked
      BEFORE the 401/403→auth rule so a deny-list block is not mis-typed as auth.
    * **Content safety / guardrails** (Azure Content Safety, Amazon Bedrock
      Guardrails) reject with **403** and a vendor header
      ``x-llm-proxy-<vendor>-...-action: reject`` → :class:`ContentSafetyBlocked`
      (flagged reasons parsed from the sibling ``...-reason`` header). Also
      checked before the auth rule.

    These two shapes are **documented, not yet live-captured** (same posture as
    #181's header-based injection typing; docs §4). Exact strings are pinned from
    the policy pages; no ``_verify.py`` row flips to ``verified=True`` until a
    live sandbox round-trip confirms them (#253). Any other content-moderation /
    federated-guardrail shape still falls through to a generic
    :class:`PolicyViolation` whose message says so.
    """

    request_id = response.headers.get("x-request-id")
    sent_correlation, sent_call = _sent_ids(response)
    status = response.status_code
    kw: dict[str, Any] = {
        # Explicit arg wins (e.g. a config-overridden header name); else the id
        # the client actually sent, read back from the request (§2.3, #195).
        "correlation_id": correlation_id if correlation_id is not None else sent_correlation,
        "call_id": call_id if call_id is not None else sent_call,
        "request_id": request_id,
        "response": response,
    }

    # A nested ``{"error": {...}}`` object is emitted by BOTH the upstream
    # provider AND some gateway LLM policies (e.g. PII). The ``type`` field —
    # not the status code or the mere presence of a nested object — is the
    # authoritative discriminator (docs §4). ``body`` is the top-level JSON
    # object (used also to spot the Regex-Prompt-Guard ``matched_patterns`` key);
    # ``error_obj`` is its nested ``error`` object iff it is itself an object.
    body = _json_body(response)
    error_obj = body.get("error") if body is not None else None
    if not isinstance(error_obj, dict):
        error_obj = None
    error_type = _str_or_none(error_obj.get("type")) if error_obj is not None else None

    # Gateway PII policy: 403 + nested object, type == "pii_detected". Checked
    # BEFORE the 401/403 → auth rule because a PII block is not an auth failure.
    if error_type == "pii_detected":
        message = _str_or_none(error_obj.get("message")) if error_obj else None
        return PIIDetected(
            message or f"Request blocked: personally identifiable information detected ({status}).",
            entities=_pii_entities(message),
            remediation=(
                "The PII-detection policy blocked this request because the prompt "
                "(or completion) contained personally identifiable information. "
                "Remove or redact the flagged values, or relax the policy's entity "
                "list / action in API Manager."
            ),
            **kw,
        )

    # Content-safety / guardrails policy: 403 + a vendor `...-action: reject`
    # header (Azure Content Safety / Amazon Bedrock Guardrails, docs §4).
    # Documented, pending live capture (#253). Checked before the 401/403 → auth
    # rule because a moderation block is not an auth failure. Keyed on the
    # header, not the body, so the body-less Bedrock reject is caught too.
    cs = _content_safety_reject(response)
    if cs is not None:
        vendor, categories = cs
        cats = f" ({', '.join(categories)})" if categories else ""
        return ContentSafetyBlocked(
            f"Request blocked by {vendor}{cats} ({status}).",
            categories=categories,
            remediation=(
                f"The {vendor} content-moderation policy blocked this request. "
                "Revise the flagged content, or adjust the policy's categories / "
                "severity thresholds in API Manager."
            ),
            **kw,
        )

    # Regex Prompt Guard policy: 403 + a top-level `matched_patterns` list
    # (flat-string `error`, so NOT the nested upstream envelope; docs §4).
    # Documented, pending live capture (#253). Checked before the 401/403 → auth
    # rule so a deny-list block is not mis-typed as an auth failure.
    matched = body.get("matched_patterns") if body is not None else None
    if isinstance(matched, list):
        pats = ", ".join(str(p) for p in matched)
        return PromptInjectionBlocked(
            f"Request blocked by the regex prompt-guard policy ({status})"
            + (f": matched {pats}." if pats else "."),
            policy="regex-prompt-guard",
            remediation=(
                "The Regex Prompt Guard policy matched a denied pattern in the "
                "prompt. Remove or rephrase the flagged content, or adjust the "
                "policy's deny-list patterns in API Manager."
            ),
            **kw,
        )

    # Injection-protection policy: discriminated by the ``x-injection-protection:
    # blocked`` header, NOT the status code (#181, docs §4). Checked before the
    # generic 4xx / nested-error branch so an injection block wins even if its
    # body happens to be shaped like an upstream error envelope. A 400 WITHOUT
    # this header is an ordinary refusal, never PromptInjectionBlocked (AC (a)).
    if response.headers.get("x-injection-protection") == "blocked":
        return PromptInjectionBlocked(
            f"Request blocked by the injection-protection policy ({status}).",
            remediation=(
                "The prompt-injection-protection policy flagged this request as a "
                "prompt-injection attempt. Review and sanitise the untrusted input "
                "in the prompt, or adjust the policy's sensitivity in API Manager."
            ),
            **kw,
        )

    if status in (401, 403):
        return AuthError(
            f"Authentication/authorization failed ({status}). Check the "
            f"connected-app credentials and their scopes (see docs/verified-apis.md §1).",
            **kw,
        )

    # Token rate limit: 429 with an empty body; budget state is header-only
    # (x-token-limit / x-token-remaining / x-token-reset ms). No retry-after.
    if status == 429:
        return TokenBudgetExceeded(
            "Token rate limit or budget exceeded (429).",
            retry_after=_retry_after(response),
            remediation=(
                "A token-rate-limit policy exhausted the budget window. Wait for it "
                "to reset (see retry_after / x-token-reset) or request an increase in "
                "API Manager."
            ),
            **kw,
        )

    # Other non-429 4xx. If the body is the upstream provider envelope (nested
    # error object with code/type), it is a request mistake passed through the
    # gateway; otherwise it is an as-yet-unclassified gateway policy refusal.
    if 400 <= status < 500:
        if error_obj is not None:
            return UpstreamRequestError(
                f"The upstream model provider rejected the request ({status}): "
                f"{error_obj.get('message') or 'see .response'}",
                code=_str_or_none(error_obj.get("code")),
                error_type=error_type,
                param=_str_or_none(error_obj.get("param")),
                **kw,
            )
        return PolicyViolation(
            f"Request refused by a gateway policy ({status}).",
            policy="unknown",
            remediation=(
                "A gateway policy refused this request. This is terminal and was NOT "
                "retried. PII (403), token-budget (429) and prompt-injection "
                "(x-injection-protection) rejections are identified specifically; "
                "content-moderation / federated-guardrail shapes are still "
                "under-documented (#253) and fall through to here. Inspect "
                ".response for the raw body."
            ),
            **kw,
        )

    if 500 <= status < 600:
        return UpstreamModelError(
            f"Upstream/provider failure ({status}). Retryable.",
            **kw,
        )

    return DonkeyError(f"Unexpected response ({status}).", **kw)


def _sent_ids(response: httpx.Response) -> tuple[str | None, str | None]:
    """The ``(correlation_id, call_id)`` the client sent, read back from the
    response's own request headers under the names the transport actually used
    (§2.3, #195, #363).

    The transport stamps the resolved header names onto ``request.extensions``
    at injection time (``donkey_correlation_header`` / ``donkey_call_id_header``),
    so a per-``Donkey`` header-name override (``DonkeyConfig.correlation_header``
    / ``.call_id_header``, docs §3) is honoured here without ``core/errors``
    importing ``DonkeyConfig`` — it only reads a plain dict carried on the same
    object ``response.request`` returns. When the stamp is absent (a response not
    produced by our transport — e.g. a hand-built stock-client response), the
    placeholder names are used directly, **not** ``Unverified.get()``, so reading
    an id back never emits the §0.3 unverified warning; that warning belongs at
    injection time, in the transport.

    Returns ``(None, None)`` when the request is unavailable (httpx raises if it
    was never set on the response)."""
    try:
        request = response.request
    except RuntimeError:
        return None, None
    headers = request.headers
    corr_name = (
        request.extensions.get("donkey_correlation_header")
        or _verify.CORRELATION_ID_HEADER.placeholder
    )
    call_name = (
        request.extensions.get("donkey_call_id_header")
        or _verify.CALL_ID_HEADER.placeholder
    )
    return headers.get(corr_name), headers.get(call_name)


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds until the caller may retry. Prefers the standard ``retry-after``
    (delta-seconds) header; falls back to the LLM token-rate-limit policy's
    ``x-token-reset`` header, which is captured in **milliseconds** (docs §4)."""
    raw = response.headers.get("retry-after")
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            pass  # HTTP-date form; left for the fixture-driven parser (§8.2)
    reset_ms = response.headers.get("x-token-reset")
    if reset_ms is not None:
        try:
            return float(reset_ms) / 1000.0
        except ValueError:
            return None
    return None


_PII_TYPE_RE = re.compile(r'"pii_type"\s*:\s*"([^"]+)"')


def _pii_entities(message: str | None) -> list[str]:
    """Best-effort extraction of the flagged PII entity types from the PII
    policy's rejection message (a JSON-ish list of ``{"pii_type": "...", ...}``
    objects; docs §4). Returns an empty list if none can be parsed.

    Deliberately best-effort (#289): the LLM PII Detection policy documents only
    the ``{"error":{"message","type":"pii_detected"}}`` envelope, not a structured
    entity field — the ``pii_type`` markers are observed in the live-captured
    message string, so ``PIIDetected.entities`` is populated from the message and
    is ``[]`` when the message carries no such markers, never invented. Reconcile
    against a documented entity field if one lands (#253)."""
    if not message:
        return []
    return _PII_TYPE_RE.findall(message)


def _json_body(response: httpx.Response) -> dict[str, Any] | None:
    """The response's top-level JSON object, or ``None`` when the body is absent,
    not JSON, or not an object. Never raises on the caller's request path (§0.3)."""
    try:
        body = response.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


# Content-safety / guardrails policies report their verdict in a pair of vendor
# headers — an ``...-action`` (``allow``|``reject``) and a comma-separated
# ``...-reason``. Both are ``x-llm-proxy-<vendor>-...`` (docs §4). Pinned from the
# Azure Content Safety and Amazon Bedrock Guardrails policy pages; documented,
# pending live capture (#253).
_CONTENT_SAFETY_VENDORS: tuple[tuple[str, str, str], ...] = (
    (
        "x-llm-proxy-azure-content-safety-action",
        "x-llm-proxy-azure-content-safety-reason",
        "Azure Content Safety",
    ),
    (
        "x-llm-proxy-bedrock-guardrail-action",
        "x-llm-proxy-bedrock-guardrail-reason",
        "Amazon Bedrock Guardrails",
    ),
)


def _content_safety_reject(response: httpx.Response) -> tuple[str, list[str]] | None:
    """``(vendor, categories)`` when a content-safety policy header reports
    ``action: reject``, else ``None``. Categories are the comma-separated flagged
    reasons from the sibling ``...-reason`` header (empty list if absent)."""
    for action_h, reason_h, vendor in _CONTENT_SAFETY_VENDORS:
        if response.headers.get(action_h) == "reject":
            reason = response.headers.get(reason_h, "")
            categories = [c.strip() for c in reason.split(",") if c.strip()]
            return vendor, categories
    return None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None
