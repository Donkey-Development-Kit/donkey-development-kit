"""The sans-IO decisions behind every governed send (BG §1.1/§1.2, #728).

Each function here takes a request, a response and the config, and returns what
to do: re-send after a token refresh, retry after a delay, or finish; whether
the final response is a policy refusal and what the span records for it;
whether a served model counts as a substitution to raise. None of them sends,
sleeps, reads a body or closes anything, so the async and blocking clients run
the same decision and differ only in how they wait (docs/adr/0009-*.md).
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass

import httpx

from .._wire import LLM_MODEL_HEADER, LLM_PROVIDER_HEADER, RETRY_AFTER_HEADER
from ..config import DonkeyConfig
from ..errors import ModelSubstituted, PolicyViolation, classify, parse_retry_after
from ..lastcall import is_fallback, is_substitution, request_id, routing_fallback
from ..telemetry import POLICY_DECISION_ALLOW, POLICY_DECISION_REFUSE, policy_type_slug

__all__ = ["Finish", "Refresh", "Retry", "RetryDecision", "decide_retry"]

# 429 is deliberately NOT here: on this proxy every 429 is a rate-limit refusal
# that classify() maps to TokenBudgetExceeded or RequestRateLimitExceeded (#974),
# both PolicyViolations, and a PolicyViolation is terminal — retrying it only
# burns the same exhausted window (BG §1.2, #183). Only genuinely transient
# upstream/gateway failures retry.
_RETRYABLE_STATUS = frozenset({502, 503, 504})
# The two retryable statuses that do not say the request went unprocessed: a
# 502 or 504 can follow an upstream call that completed and was billed, so a
# model ``POST`` is re-sent on them only with
# ``DonkeyConfig.retry_model_calls_on_gateway_errors`` (docs/adr/0009-*.md). A
# 503 means the gateway or upstream refused the work, so it always retries.
# docs/verified-apis.md records no gateway idempotency key that would make the
# re-send safe, so none is sent.
_AMBIGUOUS_STATUS = frozenset({502, 504})
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 30.0
# The openai and anthropic SDKs read this response header before their own retry
# decision, and do not retry when it is "false" (#734).
_SHOULD_RETRY_HEADER = "x-should-retry"


@dataclass(frozen=True)
class Refresh:
    """Re-send once with a fresh token: a 401 on a client that can refresh. It
    does not consume the retry budget (BG §1.1: "retry exactly once on 401")."""


@dataclass(frozen=True)
class Retry:
    """Re-send after ``delay`` seconds: a transient status with retries left."""

    delay: float


@dataclass(frozen=True)
class Finish:
    """Return this response. ``reason`` says why a retryable status was not
    retried (``"fallback"``, ``"exhausted"`` or ``"unsafe"``), and is ``None``
    for every other response."""

    reason: str | None = None


#: What the retry loop does with one response.
RetryDecision = Refresh | Retry | Finish


def decide_retry(
    cfg: DonkeyConfig,
    request: httpx.Request,
    response: httpx.Response,
    *,
    attempt: int,
    attempts: int,
    can_refresh: bool,
) -> RetryDecision:
    """What the retry loop does with ``response`` to send ``attempt`` (0-based)
    of ``attempts``.

    A 401 with a refreshable token is re-sent once. A retryable status is
    retried with backoff unless the gateway already failed it over (a routing
    fallback: its Enhanced Resilience routing is the first recovery layer, and
    an SDK retry on top multiplies latency, docs/verified-apis.md §3, #309,
    #183), the retries ran out, or it is a 502/504 on a model ``POST`` that was
    not opted in to the re-send (see :data:`_AMBIGUOUS_STATUS`). ``is_fallback`` is
    definitive-True-only, so a non-proxy 5xx with no routing header still
    retries. Everything else, every 4xx included, finishes (BG §1.2)."""
    status = response.status_code
    if status == 401 and can_refresh:
        return Refresh()
    if status not in _RETRYABLE_STATUS:
        return Finish()
    if is_fallback(response):
        return Finish("fallback")
    if (
        status in _AMBIGUOUS_STATUS
        and not cfg.retry_model_calls_on_gateway_errors
        and _is_model_post(request)
    ):
        return Finish("unsafe")
    if attempt >= attempts - 1:
        return Finish("exhausted")
    return Retry(_retry_delay(attempt, response))


def _is_model_post(request: httpx.Request) -> bool:
    """Whether ``request`` is a model call: a ``POST`` that bills when it lands,
    so re-sending it is not idempotent."""
    return request.method == "POST" and _request_model(request) is not None


def _mark_terminal(response: httpx.Response, decision: Finish) -> None:
    """Prevent provider SDK retries on final 4xx and unsafe model POSTs (#734, #953).

    Other 5xx finishes are left alone: a caller may choose to retry a transient
    error after the transport's own retry budget is exhausted.
    """
    if 400 <= response.status_code < 500 or decision.reason == "unsafe":
        response.headers[_SHOULD_RETRY_HEADER] = "false"


def _retry_delay(attempt: int, response: httpx.Response) -> float:
    # Floored at 0 by the parser: a negative Retry-After (e.g. "-1") must retry
    # immediately, never become a negative sleep — asyncio.sleep()/time.sleep()
    # raise ValueError on a negative argument, which would turn the retryable
    # status the loop exists to absorb into an unhandled exception (#286). The
    # HTTP-date form parses to None and falls through to backoff.
    retry_after = parse_retry_after(response.headers.get(RETRY_AFTER_HEADER))
    if retry_after is not None:
        return min(retry_after, _BACKOFF_CAP_S)
    exp = min(_BACKOFF_BASE_S * (2.0**attempt), _BACKOFF_CAP_S)
    return exp * (0.5 + random.random() / 2.0)  # full-ish jitter


def _body_model(request: httpx.Request) -> str | None:
    """The ``model`` from the request's JSON body, or ``None`` when the body is
    absent, unreadable, not JSON, or carries no ``model``."""
    try:
        raw = request.content
    except Exception:  # noqa: BLE001 — streaming/unread body is not a model call
        return None
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if isinstance(body, dict):
        model = body.get("model")
        return model if isinstance(model, str) else None
    return None


# A Format=Gemini proxy carries the model in the URL path, never the body
# (docs/verified-apis.md §2, #540/#691): ``/models/<model>:generateContent``
# and its SSE twin ``:streamGenerateContent``. The ingress ignores
# a body ``model``, so this is read for the SDK's own bookkeeping only — the
# request on the wire is never changed.
_GEMINI_MODEL_PATH = re.compile(r"/models/([^/:]+):(?:generateContent|streamGenerateContent)$")


def _request_model(request: httpx.Request) -> str | None:
    """The requested model (``gen_ai.request.model``): the JSON body's ``model``,
    else the model segment of a native Gemini ``POST`` path. ``None`` marks "not a
    GenAI call": no span is opened, so GETs, token fetches and bodyless POSTs
    stay byte-identical."""
    model = _body_model(request)
    if model is not None:
        return model
    if request.method != "POST":
        return None
    match = _GEMINI_MODEL_PATH.search(request.url.path)
    return match.group(1) if match else None


def refusal(response: httpx.Response) -> PolicyViolation | None:
    """The typed :class:`~.errors.PolicyViolation` a final response carries, or
    ``None`` for a 2xx or a non-policy error (auth, upstream, 5xx). One
    :func:`classify` per final response feeds both the span decision and the
    ``_on_refusal`` hook (#208)."""
    if response.status_code // 100 == 2:
        return None
    error = classify(response)
    return error if isinstance(error, PolicyViolation) else None


def _span_decision(
    response: httpx.Response, violation: PolicyViolation | None
) -> tuple[str | None, str | None]:
    """``(donkey.policy.decision, donkey.policy.type)`` for the final response:
    ``allow`` on 2xx; ``refuse`` + a policy-type slug for a refusal; and
    ``(None, None)`` for a non-policy error (auth / upstream / 5xx), so the
    transport omits the decision rather than misreporting an allow or a refuse."""
    if response.status_code // 100 == 2:
        return POLICY_DECISION_ALLOW, None
    if violation is None:
        return None, None
    return POLICY_DECISION_REFUSE, policy_type_slug(violation)


def _substitution_error(
    cfg: DonkeyConfig, request: httpx.Request, response: httpx.Response
) -> ModelSubstituted | None:
    """The :class:`ModelSubstituted` to raise for this response, or ``None``.

    Off unless ``on_model_substitution="raise"`` (docs/verified-apis.md §3, #309):
    the default surfaces
    a substitution passively on ``donkey.last_call.substituted`` and the span.
    Only a **2xx** is checked — a refusal or upstream error is classified on its
    own terms elsewhere and is not a "silent substitution". A substitution
    requires both the requested model (from the body) and the served model (the
    gateway header) to be known and to differ — by the same prefix-aware rule as
    ``last_call.substituted`` (:func:`is_substitution`, #586); a missing either
    side is never a guess (verification discipline). Never raises here — it
    returns the error for the caller path to raise once telemetry has been
    recorded."""
    if cfg.on_model_substitution != "raise":
        return None
    if response.status_code // 100 != 2:
        return None
    requested = _request_model(request)
    served = response.headers.get(LLM_MODEL_HEADER)
    provider = response.headers.get(LLM_PROVIDER_HEADER)
    if requested is None or served is None or not is_substitution(requested, served, provider):
        return None
    # A semantic route can serve another model without failing over, so only
    # name a fallback when the gateway reported one.
    cause = " (routing fallback)" if routing_fallback(response) else ""
    return ModelSubstituted(
        f"Gateway served model {served!r}, but {requested!r} was requested"
        f"{cause}; raised because on_model_substitution='raise'.",
        requested_model=requested,
        served_model=served,
        served_provider=provider,
        request_id=request_id(response),
        response=response,
    )
