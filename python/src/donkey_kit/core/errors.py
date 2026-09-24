"""Exception taxonomy (BG §1.2).

Mapping gateway policy rejections to catchable, actionable exceptions is the
SDK's clearest value over raw HTTP.

Two design points that matter:

1. :class:`PolicyViolation` must be distinguishable from a transient error at
   the framework boundary so host frameworks do not silently retry a refusal.
   It is NEVER retried by our transport.
2. ``remediation`` is a structurally-guaranteed, human-readable next step —
   worth more than a stack trace. Every :class:`PolicyViolation` carries one:
   the constructor refuses to build an instance whose remediation is empty or
   whitespace, and every concrete subclass ships a canonical default (BG §1.2,
   #182). That default is the single source ``donkey doctor`` (#202) reuses, so
   a diagnosis and the exception it stands for can never disagree.

The concrete HTTP-response → exception mapping lives in :func:`classify`, which
is driven by a table that MUST be populated from real captured fixtures (BG §1.5),
not hand-written guesses. Until fixtures exist, :func:`classify` maps only the
status-code families it can defensibly infer and otherwise returns a generic
:class:`DonkeyError`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from . import _verify
from .lastcall import request_id as read_request_id

if TYPE_CHECKING:
    from datetime import datetime

    import httpx


class DonkeyError(Exception):
    """Base for all SDK errors. Carries correlation/call/request IDs and the raw
    response so callers can inspect what actually happened.

    Three ids, three provenances (BG §1.1, #195):

    * ``correlation_id`` — the RUN id the client sent (``X-Correlation-Id``),
      shared by every request in a ``donkey.run()`` block. This is the
      client↔gateway join key, and it always equals the header that was sent.
    * ``call_id`` — the per-request id the client sent (``X-Donkey-Request-Id``),
      unique per logical request, stable across its retries. It pinpoints one
      request within a run and exists even when the request fails before any
      response.
    * ``request_id`` — the UPSTREAM PROVIDER's own id, passed through by the
      gateway and read back from a RESPONSE header whose name varies by provider
      (``x-request-id`` / ``x-amzn-requestid`` / ``apim-request-id``, #542). Quote
      it to the provider's support team. Absent on a transport error (no response)
      — and on any route where the provider forwarded none — unlike the two
      client-sent ids above. The gateway-side join key is ``correlation_id``.
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
    """Configuration is missing or invalid. Reports ALL problems at once (config resolution)."""


class AuthError(DonkeyError):
    """Rejected credentials on the Anypoint control plane or LLM-proxy data plane.

    The class-level ``remediation`` is the LLM-proxy data-plane default that
    ``donkey doctor`` (#202) reuses. Control-plane callers override it with one
    of the canonical class values below, so the next step matches the auth
    provider that failed (#484). This is not the constructor-enforced
    :class:`PolicyViolation` contract."""

    #: Single source of next-step wording for a rejected-credentials diagnosis.
    remediation: str = (
        "The gateway rejected the credentials (401/403). Check "
        "DONKEY_LLM_PROXY_CLIENT_ID / DONKEY_LLM_PROXY_CLIENT_SECRET are the "
        "consumer client_id/secret pair for this LLM-proxy instance — not an "
        "Anypoint control-plane credential and not a bearer token — and that the "
        "consumer is authorized on the instance in API Manager (docs/verified-apis.md §2)."
    )
    connected_app_remediation: str = (
        "Check the configured Anypoint control-plane auth provider. For a connected app, "
        "verify ANYPOINT_CLIENT_ID / ANYPOINT_CLIENT_SECRET and that the app has the scopes "
        "the operation needs (docs/verified-apis.md §1)."
    )
    provider_chain_remediation: str = (
        "Every configured Anypoint control-plane auth provider failed. Inspect the last "
        "error and verify each provider's credentials or token source. If the chain uses "
        "a connected app, also verify its required scopes (docs/verified-apis.md §1)."
    )

    def __init__(
        self,
        message: str,
        *,
        remediation: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        if remediation is not None:
            self.remediation = remediation


class PolicyViolation(DonkeyError):
    """Base for gateway-enforced refusals. NEVER retried.

    ``remediation`` names the concrete next step the caller can take, e.g.
    "Token budget exceeded for business group `finance`; limit resets in 42m;
    request an increase in API Manager". It is **structurally mandatory** (#182):
    the constructor raises :class:`ValueError` if the resolved remediation is
    empty or whitespace, because a typed refusal with no next step is just a
    renamed exception. When no ``remediation`` is passed, the class-level
    :attr:`remediation` default applies; every concrete subclass ships its own,
    and that default is the single source ``donkey doctor`` (#202) reuses so the
    CLI and the exception never disagree. It names the *action*, not the policy
    that fired.
    """

    policy: str = "unknown"

    #: Default next-step wording, used when the caller passes no ``remediation``.
    #: This base value is the generic-refusal fall-through (the shape ``classify``
    #: cannot pin more precisely); every concrete subclass overrides it.
    remediation: str = (
        "A gateway policy refused this request. This is terminal and was NOT "
        "retried. PII (403), token-budget (429), prompt-injection (the "
        "x-injection-protection header or the regex prompt guard's "
        "matched_patterns) and content-safety (Azure Content Safety, Amazon "
        "Bedrock Guardrails) rejections are identified specifically; only "
        "federated-guardrail verdicts and otherwise-unrecognised shapes fall "
        "through to here. Inspect .response for the raw body."
    )

    def __init__(
        self,
        message: str,
        *,
        remediation: str | None = None,
        policy: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        # An explicit remediation wins; otherwise the concrete class's default
        # (resolved via the instance type, so the most-derived default applies).
        resolved = remediation if remediation is not None else type(self).remediation
        if not resolved.strip():
            raise ValueError(
                f"{type(self).__name__} requires a non-empty remediation naming the "
                "caller's next step (BG §1.2, #182)."
            )
        self.remediation = resolved
        if policy is not None:
            self.policy = policy


class TokenBudgetExceeded(PolicyViolation):
    policy = "token-rate-limit"
    remediation: str = (
        "A token-rate-limit policy exhausted the budget window. Wait for it "
        "to reset (see retry_after / x-token-reset) or request an increase in "
        "API Manager."
    )

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after


class BudgetReserveReached(DonkeyError):
    """Raised by :meth:`Budget.pace` *before* it lets a request through that would
    cross the caller's reserve (BG §1.3, #186).

    Deliberately NOT a :class:`PolicyViolation`: that base marks a gateway-enforced
    refusal that is terminal and never retried. This is the client-side opposite —
    a pre-emptive signal, raised locally from observed budget headers before any
    request is sent. When :attr:`reset_at` is known, the caller can recover with
    ``await budget.wait_for_reset()`` then continue. When it is ``None``, waiting
    cannot make progress, so the caller must handle or propagate the signal instead
    of retrying immediately. Classifying it as a refusal would misrepresent it at
    the framework boundary.

    Carries the observed budget state at the moment pacing tripped so a handler can
    decide without re-reading the object: ``fraction_used`` (0.0-1.0), the
    ``reserve`` that was requested, and ``reset_at`` (``None`` if the reset time has
    not been observed)."""

    remediation: str = (
        "When reset_at is known, call await budget.wait_for_reset() before retrying. "
        "When reset_at is None, waiting cannot make progress; preserve the last "
        "checkpoint and handle or propagate this exception instead of retrying "
        "immediately."
    )

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


class ModelSubstituted(DonkeyError):
    """The gateway served a *different* model than the one requested — a routing
    fallback substituted the model, and ``on_model_substitution="raise"`` opted
    the caller into treating that as an error (docs/verified-apis.md §3, #309).

    Deliberately NOT a :class:`PolicyViolation`: the request was neither refused
    nor failed — it succeeded, just against a model the developer did not choose.
    It is a client-side determinism signal (like :class:`BudgetReserveReached`),
    off by default; a caller who has not opted in gets the substitution surfaced
    passively on ``donkey.last_call.substituted`` instead. Carries the response,
    so a handler can still read the served completion if it decides to accept it.

    ``request_id`` (the gateway's own id) is inherited from :class:`DonkeyError`
    and populated from the response, so a substitution can be quoted in a ticket
    on the same terms as any other gateway event."""

    def __init__(
        self,
        message: str,
        *,
        requested_model: str,
        served_model: str,
        served_provider: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.requested_model = requested_model
        self.served_model = served_model
        self.served_provider = served_provider


class PromptInjectionBlocked(PolicyViolation):
    policy = "prompt-injection-protection"
    remediation: str = (
        "The prompt-injection-protection policy flagged this request as a "
        "prompt-injection attempt. Review and sanitise the untrusted input "
        "in the prompt, or adjust the policy's sensitivity in API Manager."
    )


class ContentSafetyBlocked(PolicyViolation):
    """A provider-backed content-moderation policy (Azure Content Safety or
    Amazon Bedrock Guardrails) blocked the request or response.

    ``categories`` carries the flagged reasons the gateway reports in its
    ``x-llm-proxy-<vendor>-...-reason`` header (e.g. ``severity_hate``,
    ``blocklist``, ``denied_topic``) — the moderation analog of
    :attr:`PIIDetected.entities`. Empty when the gateway reported no reason."""

    policy = "content-safety"
    remediation: str = (
        "A content-moderation policy (Azure Content Safety or Amazon Bedrock "
        "Guardrails) blocked this request. Revise the flagged content, or "
        "adjust the policy's categories / severity thresholds in API Manager."
    )

    def __init__(self, message: str, *, categories: list[str] | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.categories = categories or []


class PIIDetected(PolicyViolation):
    policy = "pii-detection"
    remediation: str = (
        "The PII-detection policy blocked this request because the prompt "
        "(or completion) contained personally identifiable information. "
        "Remove or redact the flagged values, or relax the policy's entity "
        "list / action in API Manager."
    )

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
    caller can act (fix the model, the params, etc.).

    ``remediation`` is a class attribute so ``donkey doctor`` (#202) has one
    canonical wording for a model-rejected diagnosis (the LIVE-VERIFIED
    ``model_not_found`` passthrough, docs/verified-apis.md §4) rather than a
    second copy."""

    #: Single source of next-step wording for a rejected-request diagnosis. The
    #: common case doctor keys on is ``code == "model_not_found"``.
    remediation: str = (
        "The upstream provider rejected the request (a 4xx passed through the "
        "gateway). If `code`/`param` names the model (e.g. model_not_found), the "
        "requested model is not available on this proxy — request it in API "
        "Manager or choose a model this instance routes. Otherwise fix the "
        "flagged parameter (inspect .code / .param / .response)."
    )

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


class GatewayUnavailable(DonkeyError):
    """The gateway could not be reached at all — a transport-level failure (DNS,
    refused connection, TLS error, timeout) with NO HTTP response behind it
    (BG §1.2, #379).

    This is the one *ungoverned* failure the taxonomy names. Every other error
    here describes something the gateway told us; this one is the gateway not
    being there to tell us anything. Deliberately NOT a :class:`PolicyViolation`:
    nothing was refused — the request never reached a policy — so surfacing it as
    a refusal would misrepresent it at the framework boundary. Giving it a type
    lets a long-running agent distinguish "lost the gateway" from any other
    network fault and react — checkpoint, queue, shed load, or fall back to a
    non-AI path — instead of pattern-matching a raw ``httpx.TransportError``.

    Carries ``base_url`` (the origin that failed, so a handler can act on it
    without re-parsing the message) and ``cause`` (the underlying httpx
    exception, also chained via ``raise ... from``). ``request_id`` is inherited
    from :class:`DonkeyError` and is always ``None`` here — there was no response
    to read the gateway's own id from — while ``correlation_id`` / ``call_id``
    (the ids the client sent) are populated from the request that failed.

    ``remediation`` is a class attribute so it has a single canonical wording,
    shared with ``donkey doctor`` (#202) rather than duplicated."""

    #: The three real causes, named, plus the pointer to the startup/CI
    #: diagnostic. Single source of wording for this failure (#202, #379).
    remediation: str = (
        "The gateway could not be reached and no HTTP response came back. The "
        "three usual causes: (1) the host is unreachable — DNS failure or the "
        "gateway is down; (2) the configured base URL is wrong; or (3) network "
        "egress to the gateway is blocked — a firewall or air-gapped environment. "
        "Run `donkey doctor` to diagnose connectivity, and check `base_url` on "
        "this error against your gateway's address."
    )

    def __init__(
        self,
        message: str,
        *,
        base_url: str | None = None,
        cause: BaseException | None = None,
        remediation: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.base_url = base_url
        self.cause = cause
        if remediation is not None:
            self.remediation = remediation


def gateway_unavailable(
    *,
    base_url: str | None = None,
    cause: BaseException | None = None,
    correlation_id: str | None = None,
    call_id: str | None = None,
) -> GatewayUnavailable:
    """Build a :class:`GatewayUnavailable` with the standard message from a
    transport-level failure. The message names the origin and the underlying
    cause; the full remediation lives on the returned exception's
    :attr:`GatewayUnavailable.remediation`."""
    where = f" at {base_url}" if base_url else ""
    detail = f": {cause}" if cause is not None and str(cause) else "."
    return GatewayUnavailable(
        f"The gateway could not be reached{where}{detail}",
        base_url=base_url,
        cause=cause,
        correlation_id=correlation_id,
        call_id=call_id,
    )


class ToolInvocationError(DonkeyError):
    """An MCP tool call failed."""


class RegistryError(DonkeyError):
    """Exchange discovery / resolution failed."""


class ProvisioningError(DonkeyError):
    """plan/apply/drift failed."""


class GovernanceDrift(DonkeyError):
    """resolve(): a declared policy is not actually applied on the gateway."""


class PublicationDrift(DonkeyError):
    """verify(): the live server no longer matches the Exchange descriptor (BG §2.5)."""


def classify(
    response: httpx.Response,
    *,
    correlation_id: str | None = None,
    call_id: str | None = None,
) -> DonkeyError:
    """Map an HTTP error response to a specific exception.

    ``correlation_id`` (the run id) and ``call_id`` (the per-request id) are the
    two ids the client sent (BG §1.1, #195). When not passed explicitly they are
    read back from the response's own request headers, so a caller bridging an
    ``openai.APIStatusError`` — ``classify(err.response)`` — gets a
    :class:`DonkeyError` whose ``correlation_id`` equals the header that was sent
    with no extra wiring. An explicit argument (e.g. when the header name was
    overridden via config) always wins over the auto-derived value.

    The precise policy discrimination (BG §1.2, working instruction #4) is driven by
    real rejection captures from a live governed LLM proxy (docs/verified-apis.md §4,
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

    All three provider-backed guardrail shapes are now **live-verified**: Regex
    Prompt Guard against ``ddk-injection-guard`` and Azure Content Safety against
    ``ddk-azure-content-safety`` (both 2026-09-22, #253), and Amazon Bedrock
    Guardrails against ``ddk-bedrock-guardrails`` (2026-09-24, #568) — the same
    ``...-action: reject`` header family, now confirmed against a real Bedrock
    upstream (docs/verified-apis.md §4). These shapes have no ``_verify.py``
    constant — ``classify()`` reads them straight from the response — so the
    record is the §4 rows, not a ``verified=True`` flip. The Injection Protection
    body (``x-injection-protection: blocked``, a distinct policy) is the only
    content-moderation shape that stays documented-only — no proxy running it is
    deployed to capture (#253). Any other content-moderation / federated-guardrail
    shape still falls
    through to a generic
    :class:`PolicyViolation` whose message names what was observed and says the
    shape is unconfirmed (#184). Because auth is the verified 401 / ``www-authenticate``
    shape (docs/verified-apis.md §4), a **403 carrying no ``www-authenticate`` header** and matching
    none of the policy discriminators above is treated as one of these unconfirmed
    refusals, not mis-typed as an :class:`AuthError`.
    """

    sent_correlation, sent_call = _sent_ids(response)
    status = response.status_code
    kw: dict[str, Any] = {
        # Explicit arg wins (e.g. a config-overridden header name); else the id
        # the client actually sent, read back from the request (BG §1.1, #195).
        "correlation_id": correlation_id if correlation_id is not None else sent_correlation,
        "call_id": call_id if call_id is not None else sent_call,
        # The upstream provider's request id — resolved from the per-provider
        # header list, so a Bedrock refusal (x-amzn-requestid, no x-request-id) is
        # not silently None. Same resolver LastCall uses (#542).
        "request_id": read_request_id(response),
        "response": response,
    }

    # A nested ``{"error": {...}}`` object is emitted by BOTH the upstream
    # provider AND some gateway LLM policies (e.g. PII). The ``type`` field —
    # not the status code or the mere presence of a nested object — is the
    # authoritative discriminator (docs/verified-apis.md §4). ``body`` is the top-level JSON
    # object (used also to spot the Regex-Prompt-Guard ``matched_patterns`` key),
    # ``None`` when the body is not a JSON object — e.g. Gemini's LIST-shaped
    # error envelope (#548), whose nested error object ``_nested_error`` still
    # recovers below. ``error_obj`` is the nested ``error`` object from either
    # envelope shape, iff it is itself an object.
    body = _json_body(response)
    error_obj = _nested_error(response)
    # ``type`` is the OpenAI-format discriminator (``pii_detected``,
    # ``invalid_request_error`` …); Gemini has no ``type`` but a string
    # ``status`` (``INVALID_ARGUMENT``), which stands in for it (#548).
    error_type = (
        _str_or_none(error_obj.get("type") or error_obj.get("status"))
        if error_obj is not None
        else None
    )

    # Gateway PII policy: 403 + nested object, type == "pii_detected". Checked
    # BEFORE the 401/403 → auth rule because a PII block is not an auth failure.
    if error_type == "pii_detected":
        message = _str_or_none(error_obj.get("message")) if error_obj else None
        return PIIDetected(
            message or f"Request blocked: personally identifiable information detected ({status}).",
            entities=_pii_entities(message),
            # remediation: PIIDetected's canonical class default (#182).
            **kw,
        )

    # Content-safety / guardrails policy: 403 + a vendor `...-action: reject`
    # header (Azure Content Safety / Amazon Bedrock Guardrails, docs/verified-apis.md §4).
    # Azure verified LIVE 2026-09-22, Bedrock verified LIVE 2026-09-24 (#253/#568).
    # Checked before the 401/403 → auth rule because a moderation block is not an
    # auth failure. Keyed on the header, not the body, so a reject with an
    # unexpected or absent body is still caught.
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
    # (flat-string `error`, so NOT the nested upstream envelope; docs/verified-apis.md §4).
    # Verified LIVE 2026-09-22 against ddk-injection-guard (#253); body matched
    # the committed fixture byte-for-byte. Checked before the 401/403 → auth
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
    # blocked`` header, NOT the status code (#181, docs/verified-apis.md §4). Checked before the
    # generic 4xx / nested-error branch so an injection block wins even if its
    # body happens to be shaped like an upstream error envelope. A 400 WITHOUT
    # this header is an ordinary refusal, never PromptInjectionBlocked (AC (a)).
    if response.headers.get("x-injection-protection") == "blocked":
        return PromptInjectionBlocked(
            f"Request blocked by the injection-protection policy ({status}).",
            # remediation: PromptInjectionBlocked's canonical class default (#182).
            **kw,
        )

    # Auth is discriminated by the verified client-id-enforcement shape (docs/verified-apis.md §4):
    # a 401, or a 403 carrying a ``www-authenticate`` challenge. A 403 WITHOUT that
    # header matched none of the policy discriminators above, so it is an
    # unrecognised gateway policy refusal — not an auth failure — and falls through
    # to the honest generic PolicyViolation below rather than being mis-typed as
    # auth (#184). ``donkey doctor``'s (#202) credentials diagnosis stays intact: a
    # wrong-credential 401 still lands here.
    if status == 401 or (status == 403 and "www-authenticate" in response.headers):
        return AuthError(
            f"Authentication/authorization failed ({status}). Check the consumer "
            "client_id/client_secret pair and its API Manager authorization "
            "for this LLM-proxy instance (see docs/verified-apis.md §2).",
            **kw,
        )

    # Token rate limit: 429 with an empty body; budget state is header-only
    # (x-token-limit / x-token-remaining / x-token-reset ms). No retry-after.
    if status == 429:
        return TokenBudgetExceeded(
            "Token rate limit or budget exceeded (429).",
            retry_after=_retry_after(response),
            # remediation: TokenBudgetExceeded's canonical class default (#182).
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
                # OpenAI sends a string ``code`` (``model_not_found``); Gemini a
                # numeric one (400). Carry both, stringified (#548).
                code=_code_str(error_obj.get("code")),
                error_type=error_type,
                param=_str_or_none(error_obj.get("param")),
                **kw,
            )
        # An unrecognised non-429 4xx with no nested provider envelope: a refusal
        # whose contract we cannot pin (content-moderation / federated-guardrail
        # shapes are still under-documented, #253). Surface it honestly — name what
        # was observed and say the shape is unconfirmed — rather than coerce it into
        # a subclass we have not verified (#184). ``error_obj`` is None here (the
        # envelope branch above owns the nested case), so the observable
        # discriminators are the status and any gateway policy headers present.
        policy_headers = sorted(
            name for name in response.headers if name.lower().startswith("x-llm-proxy-")
        )
        observed = f"status {status}"
        if policy_headers:
            observed += f"; policy headers: {', '.join(policy_headers)}"
        return PolicyViolation(
            f"Request refused by a gateway policy; shape unconfirmed ({observed}). "
            "It matched no documented rejection contract.",
            policy="unknown",
            remediation=(
                "This refusal matched no documented rejection shape, so its contract "
                "is unconfirmed (#184, #253). It is terminal and was NOT retried. "
                "Please file an issue on the donkey-development-kit repo with the response "
                "status, headers and body (all carried on this exception's .response) "
                "so the shape can be typed."
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
    (BG §1.1, #195, #363).

    The transport stamps the resolved header names onto ``request.extensions``
    at injection time (``donkey_correlation_header`` / ``donkey_call_id_header``),
    so a per-``Donkey`` header-name override (``DonkeyConfig.correlation_header``
    / ``.call_id_header``, docs/verified-apis.md §3) is honoured here without ``core/errors``
    importing ``DonkeyConfig`` — it only reads a plain dict carried on the same
    object ``response.request`` returns. When the stamp is absent (a response not
    produced by our transport — e.g. a hand-built stock-client response), the
    placeholder names are used directly, **not** ``Unverified.get()``, so reading
    an id back never emits the verification discipline's unverified warning; that warning belongs at
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
    ``x-token-reset`` header, which is captured in **milliseconds** (docs/verified-apis.md §4)."""
    raw = response.headers.get("retry-after")
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            pass  # HTTP-date form; left for the fixture-driven parser (BG §1.5)
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
    objects; docs/verified-apis.md §4). Returns an empty list if none can be parsed.

    Deliberately best-effort (#289): the LLM PII Detection policy documents only
    the ``{"error":{"message","type":"pii_detected"}}`` envelope, not a structured
    entity field — the ``pii_type`` markers are observed in the live-captured
    message string, so ``PIIDetected.entities`` is populated from the message and
    is ``[]`` when the message carries no such markers, never invented. Reconcile
    against a documented entity field if one lands (#253)."""
    if not message:
        return []
    return _PII_TYPE_RE.findall(message)


def _parse_json(response: httpx.Response) -> Any:
    """The response's parsed JSON body (of any shape — object, list, scalar), or
    ``None`` when the body is absent or not JSON. Never raises on the caller's
    request path (verification discipline)."""
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        return None


def _json_body(response: httpx.Response) -> dict[str, Any] | None:
    """The response's top-level JSON *object*, or ``None`` when the body is
    absent, not JSON, or not an object. Fail-open: a list-shaped body (e.g.
    Gemini's error envelope, #548) returns ``None`` here — its nested error is
    recovered separately by :func:`_nested_error`."""
    body = _parse_json(response)
    return body if isinstance(body, dict) else None


def _nested_error(response: httpx.Response) -> dict[str, Any] | None:
    """The provider's nested ``error`` object, or ``None``.

    Two envelope shapes are seen live (docs/verified-apis.md §4):

    * an OBJECT envelope ``{"error": {...}}`` — OpenAI-format proxies and
      gateway LLM policies (e.g. PII);
    * a LIST envelope ``[{"error": {...}}]`` — Gemini's native error shape,
      whose first element is the object envelope (#548).

    Fail-open (BG §1.5): any other shape — a bare list, a list whose first
    element carries no nested ``error`` object, a scalar — returns ``None`` so an
    unrecognised body still falls through to the honest generic refusal rather
    than a guess."""
    parsed = _parse_json(response)
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else None
    if not isinstance(parsed, dict):
        return None
    error_obj = parsed.get("error")
    return error_obj if isinstance(error_obj, dict) else None


def _code_str(value: Any) -> str | None:
    """The provider's error ``code`` as a string. OpenAI sends a string
    (``model_not_found``); Gemini sends a number (400, #548). Both are carried;
    any other type (``bool`` included) is dropped."""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return None


# Content-safety / guardrails policies report their verdict in a pair of vendor
# headers — an ``...-action`` (``allow``|``reject``) and a comma-separated
# ``...-reason``. Both are ``x-llm-proxy-<vendor>-...`` (docs/verified-apis.md §4).
# Azure Content Safety verified LIVE 2026-09-22 against ddk-azure-content-safety
# (#253); Amazon Bedrock Guardrails verified LIVE 2026-09-24 against
# ddk-bedrock-guardrails (#568).
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
