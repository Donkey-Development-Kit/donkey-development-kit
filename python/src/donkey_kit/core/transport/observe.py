"""What a governed send records about its final response (BG §1.3/§1.6, #728).

The budget window, ``donkey.last_call``, the GenAI span's response attributes,
and the DEBUG log records of the retry loop. Each is best-effort where it must
be (telemetry never breaks a request), and no log record ever carries a header
value (#717).
"""

from __future__ import annotations

import logging

import httpx

from .._wire import LLM_MODEL_HEADER, LLM_PROVIDER_HEADER, ROUTING_TYPE_HEADER
from ..budget import Budget
from ..cost import CostTags
from ..errors import PolicyViolation
from ..lastcall import (
    observe_last_call,
    routing_fallback,
    semantic_cache,
    semantic_routing,
    usage_from_response,
)
from ..telemetry import POLICY_DECISION_REFUSE, GenAiSpan
from .policy import Finish, Retry, _request_model, _span_decision

__all__: list[str] = []

_log = logging.getLogger(__name__)


def _observe_final(request: httpx.Request, response: httpx.Response, budget: Budget | None) -> None:
    """Feed the final response to the attached :class:`Budget` from its
    ``x-token-*`` headers (#185), and record a governed model call's gateway
    identity into ``donkey.last_call`` (#362). Both are no-ops when not
    applicable.

    Only a model call feeds ``last_call``: a token fetch or a registry GET
    shares the transport but is not "the last call" a developer means (#362).
    ``_request_model`` is the same signal ``send()`` uses to open a GenAI span,
    so the two stay in step, and the requested model is threaded through so
    ``last_call`` can report a substitution against what the caller asked for
    (#309)."""
    if budget is not None:
        budget.observe(response)
    model = _request_model(request)
    if model is not None:
        observe_last_call(response, requested_model=model)


def _log_target(request: httpx.Request) -> str:
    """``METHOD scheme://host/path`` for a log record: the query string is dropped,
    and no header is ever read, so a record never carries a credential (#717)."""
    url = request.url
    return f"{request.method} {url.scheme}://{url.host}{url.path}"


def _log_retry(
    request: httpx.Request, response: httpx.Response, attempt: int, attempts: int, retry: Retry
) -> None:
    """DEBUG record for one retry: the status, which retry this is, and the delay."""
    _log.debug(
        "%s returned %d; retry %d of %d in %.2fs",
        _log_target(request),
        response.status_code,
        attempt + 1,
        attempts - 1,
        retry.delay,
    )


def _log_finish(
    request: httpx.Request, response: httpx.Response, finish: Finish, attempts: int
) -> None:
    """DEBUG record when a retryable status is returned without a retry: the
    gateway already failed over (routing fallback, #309), a 502/504 on a model
    call that is not re-sent by default (docs/adr/0009-*.md), or retries ran out."""
    target, status = _log_target(request), response.status_code
    if finish.reason == "fallback":
        _log.debug("%s returned %d after a gateway routing fallback; not retrying", target, status)
    elif finish.reason == "unsafe":
        _log.debug(
            "%s returned %d on a model call; not re-sent, because the upstream call "
            "may have completed (set retry_model_calls_on_gateway_errors to retry it)",
            target,
            status,
        )
    elif finish.reason == "exhausted":
        _log.debug("%s returned %d; giving up after %d attempt(s)", target, status, attempts)


def _record_response(
    gspan: GenAiSpan,
    request: httpx.Request,
    response: httpx.Response,
    budget: Budget | None,
    violation: PolicyViolation | None,
    *,
    correlation_header: str,
    cost_tags: CostTags,
) -> None:
    """Record the dual-namespace response attributes on the span, after
    ``_on_response`` has fed the budget. Never raises: a telemetry failure must
    not mask the caller's result.

    The span's ``donkey.correlation_id`` is the RUN id, read back from the
    request header the event hook set, so it equals the id actually sent on the
    wire — the sync and async transports source that id differently, and reading
    the header makes the recorded value correct for both. The per-call id is
    intentionally not a span attribute: the span already correlates one call, and
    the run id is the cross-call join key (BG §1.1, #195)."""
    try:
        decision, policy_type = _span_decision(response, violation)
        usage = usage_from_response(response)
        matched_topic, routing_score = semantic_routing(response)
        cache_status, cache_score = semantic_cache(response)
        gspan.record(
            system=response.headers.get(LLM_PROVIDER_HEADER),
            # Served model + routing facts (docs/verified-apis.md §3, #309):
            # request≠response model on
            # the span is the fastest read that a gateway failover happened, and
            # the fallback flag is emitted even when False.
            response_model=response.headers.get(LLM_MODEL_HEADER),
            routing_type=response.headers.get(ROUTING_TYPE_HEADER),
            fallback=routing_fallback(response),
            # Semantic-routing match detail — both None on model-based / non-proxy
            # responses, and the span omits any None field (docs/verified-apis.md
            # §3, #590).
            matched_topic=matched_topic,
            routing_score=routing_score,
            # Semantic-cache outcome — both None on a proxy with no caching policy
            # / non-proxy response, and the span omits any None field (§2, #587).
            cache_status=cache_status,
            cache_score=cache_score,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cached_tokens=usage["cached_tokens"],
            cache_write_tokens=usage["cache_write_tokens"],
            reasoning_tokens=usage["reasoning_tokens"],
            decision=decision,
            policy_type=policy_type,
            budget_remaining=budget.remaining if budget is not None else None,
            correlation_id=request.headers.get(correlation_header),
            # donkey.cost.* carries the FULL tag value regardless of the
            # (unverified) request-header name — the SDK owns the span end to end
            # (#196 AC #4). Merged config⊕run tags, so a run-scope override shows.
            **cost_tags.span_kwargs(),
        )
        # A refusal is a failed operation, not just a refuse attribute: mark the
        # span ERROR so a trace reads it as such (#193, AC #1). One place covers
        # both a buffered refusal and a refused stream request.
        if decision == POLICY_DECISION_REFUSE:
            gspan.set_error()
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        # Never raised, but never silent either: a swallowed failure here once
        # hid every streamed refusal from the span (#805).
        _log.debug("recording the GenAI span response attributes failed", exc_info=True)
