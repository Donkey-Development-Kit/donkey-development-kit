"""The governed pipeline both clients share (BG §1.1, #728).

:class:`_GovernedPipeline` is the mixin under
:class:`~donkey_kit.core.transport.DonkeyAsyncClient` and
:class:`~donkey_kit.core.transport.DonkeyClient`. It holds the config, budget
and header names, stamps the per-send headers, runs the sans-IO retry decision
(:mod:`.policy`) with its log record, types a failed send, and settles the
final response: refusal, span attributes and substitution. Inside a
:func:`~donkey_kit.core.refusals.capture_refusals` block it also records each
send's typed outcome there (#969). The clients add
only what differs between them, which is how they wait and how they close.
Before #728 each client carried its own copy of all of this, and the two had
drifted.
"""

from __future__ import annotations

import logging
import ssl
from collections.abc import Mapping
from typing import TypedDict

import httpx

from ..budget import Budget
from ..config import DonkeyConfig
from ..errors import (
    ConfigError,
    DonkeyError,
    GatewayUnavailable,
    ModelSubstituted,
    PolicyViolation,
    classify,
)
from ..refusals import capturing, record_outcome
from ..telemetry import GenAiSpan
from .failures import _gateway_unavailable, _lifecycle_error
from .headers import (
    Origin,
    _apply_base_headers,
    _CheckedEndpoints,
    _request_correlation_id,
    _resolve_header_names,
    effective_cost_tags,
)
from .observe import _log_finish, _log_retry, _record_response
from .policy import (
    Finish,
    Retry,
    RetryDecision,
    _mark_terminal,
    _substitution_error,
    decide_retry,
    refusal,
)

__all__ = ["AsyncClientOptions", "ClientOptions"]

_log = logging.getLogger(__name__)


class _CommonOptions(TypedDict, total=False):
    """The ``httpx`` constructor options a governed client passes through."""

    proxy: str | httpx.URL | httpx.Proxy
    verify: ssl.SSLContext | str | bool
    trust_env: bool
    limits: httpx.Limits
    base_url: str | httpx.URL
    headers: Mapping[str, str]
    follow_redirects: bool
    http2: bool


class AsyncClientOptions(_CommonOptions, total=False):
    """Extra ``httpx.AsyncClient`` options for a :class:`DonkeyAsyncClient`."""

    transport: httpx.AsyncBaseTransport
    mounts: Mapping[str, httpx.AsyncBaseTransport | None]


class ClientOptions(_CommonOptions, total=False):
    """Extra ``httpx.Client`` options for a :class:`DonkeyClient`."""

    transport: httpx.BaseTransport
    mounts: Mapping[str, httpx.BaseTransport | None]


class _GovernedPipeline(_CheckedEndpoints):
    """The IO-free halves of a governed send, shared by both clients."""

    _cfg: DonkeyConfig
    _budget: Budget | None
    _control_plane: bool
    _correlation_header: str
    _call_id_header: str

    def _init_pipeline(
        self,
        cfg: DonkeyConfig,
        *,
        budget: Budget | None,
        control_plane: bool,
        origins: set[Origin] | None,
    ) -> None:
        self._cfg = cfg
        self._control_plane = control_plane
        self._init_origins(cfg.control_plane_url if control_plane else cfg.llm_proxy_url, origins)
        # The two correlation request-header NAMES, resolved once (config override
        # → UNVERIFIED placeholder). The one-time verification-discipline warning
        # for an un-overridden name fires here, at construction, not per request
        # (BG §1.1, #195).
        self._correlation_header, self._call_id_header = _resolve_header_names(cfg)
        # Optional in-band budget collaborator (BG §1.3, #185). When attached, the
        # response hook feeds it; when None the hook stays a byte-identical no-op,
        # so the control-plane token-fetch client tracks no budget.
        self._budget = budget

    def _stamp(self, request: httpx.Request) -> bool:
        """Stamp the per-send headers on every wire send, a redirect hop
        included, and report whether ``request`` may carry credentials."""
        _apply_base_headers(
            self._cfg,
            request,
            _request_correlation_id(request),
            correlation_header=self._correlation_header,
            control_plane=self._control_plane,
        )
        return self._guard(request)

    def _decide(
        self,
        request: httpx.Request,
        response: httpx.Response,
        *,
        attempt: int,
        attempts: int,
        can_refresh: bool,
    ) -> RetryDecision:
        """The retry decision for one response, logged at DEBUG."""
        decision = decide_retry(
            self._cfg,
            request,
            response,
            attempt=attempt,
            attempts=attempts,
            can_refresh=can_refresh,
        )
        if isinstance(decision, Retry):
            _log_retry(request, response, attempt, attempts, decision)
        elif isinstance(decision, Finish):
            _mark_terminal(response, decision)
            _log_finish(request, response, decision, attempts)
        return decision

    def _unreachable(self, request: httpx.Request, exc: httpx.TransportError) -> GatewayUnavailable:
        """A transport-level failure (DNS, refused, TLS, timeout) as the typed
        :class:`GatewayUnavailable`, carrying the ids already on the request (#379)."""
        typed = _gateway_unavailable(
            request,
            exc,
            correlation_header=self._correlation_header,
            call_id_header=self._call_id_header,
        )
        record_outcome(typed)
        return typed

    def _send_error(
        self, request: httpx.Request, exc: Exception, *, client_closed: bool
    ) -> GatewayUnavailable | ConfigError | None:
        """The typed error for a send that raised, or ``None`` to re-raise ``exc``.

        A transport-level failure never yields a response, so it is not retried
        (retries key off a status) and is not a governed refusal (#379). A closed
        client or event loop is a :class:`ConfigError` (#813)."""
        if isinstance(exc, httpx.TransportError):
            return self._unreachable(request, exc)
        if isinstance(exc, RuntimeError):
            typed = _lifecycle_error(
                request,
                exc,
                client_closed=client_closed,
                correlation_header=self._correlation_header,
                call_id_header=self._call_id_header,
            )
            if typed is not None:
                record_outcome(typed)
            return typed
        return None

    def _settle(
        self, request: httpx.Request, response: httpx.Response, gspan: GenAiSpan
    ) -> tuple[PolicyViolation | None, ModelSubstituted | None]:
        """Classify the final response once and record the span, after the
        response hook has fed the budget, so ``donkey.budget.remaining`` reflects
        the budget this response just fed (#185/#192). Returns the refusal for
        ``_on_refusal`` (#208) and the substitution to raise, if any (#309).
        Never raises: telemetry must not mask the caller's result."""
        try:
            violation = refusal(response)
        except Exception:  # noqa: BLE001 — classification must never break the request
            _log.debug("classifying the final response failed", exc_info=True)
            violation = None
        _record_response(
            gspan,
            request,
            response,
            self._budget,
            violation,
            correlation_header=self._correlation_header,
            cost_tags=effective_cost_tags(self._cfg),
        )
        substitution = _substitution_error(self._cfg, request, response)
        if capturing():
            _record_settled(response, substitution)
        return violation, substitution


def _record_settled(response: httpx.Response, substitution: ModelSubstituted | None) -> None:
    """Record a final response's typed outcome in the open refusal capture
    (#969): the substitution to raise, else the classified error of a non-2xx
    response, else ``None``, so a success clears an earlier failure in the same
    framework call. Never raises."""
    outcome: DonkeyError | None = substitution
    try:
        if outcome is None and response.status_code >= 400:
            outcome = classify(response)
    except Exception:  # noqa: BLE001 — classification must never break the request
        _log.debug("classifying the final response for the refusal capture failed", exc_info=True)
        return
    record_outcome(outcome)
