"""Transport — the single place headers get injected (§2.3).

This is the most important piece of engineering in the SDK. Every framework has
a different mechanism for setting request headers, and several have none. The
solution is one shared HTTP client that every adapter is handed.

The client:
  * injects, via a request event hook, on every outbound request:
      - the run correlation ID (uuid4 per logical agent run, from a contextvar,
        §2.5) — shared by every request in a ``donkey.run()`` block, the
        client↔gateway join key
      - attribution headers (application, business group) — header NAMES are
        UNVERIFIED (docs/verified-apis.md §3), emitted via loud placeholders
      - bearer token, refreshed lazily
  * pins a per-call ID ONCE, before the retry loop, so it is unique per logical
    request yet stable across that request's retries and 401 refresh (§2.3,
    #195). Two ids, two headers: the run id (``X-Correlation-Id``) groups a run;
    the call id (``X-Donkey-Request-Id``) pinpoints one request within it. Both
    header NAMES are UNVERIFIED placeholders (docs §3), overridable via config.
  * retries transient upstream/gateway failures (502/503/504) with exponential
    backoff + jitter, honouring Retry-After
  * does NOT retry 4xx — gateway policy rejections are terminal (§2.4). This
    includes 429: on this proxy a 429 is a token-budget refusal
    (TokenBudgetExceeded), and retrying it only burns the same exhausted window
    (§2.4, #183). retry_after is still surfaced for wait_for_reset() (#186).
  * refreshes the token and retries exactly once on 401 (§2.2)

For frameworks that only accept a ``default_headers`` dict (not a client), pass
:func:`attribution_headers` — a snapshot — and accept that the correlation ID is
per-client rather than per-run. Document that degradation per adapter (§3.3).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Iterator

import httpx

from . import _verify
from .auth import AuthProvider
from .budget import Budget
from .config import DonkeyConfig
from .cost import CostTags
from .errors import classify
from .telemetry import (
    POLICY_DECISION_ALLOW,
    POLICY_DECISION_REFUSE,
    GenAiSpan,
    current_cost_tags,
    ensure_correlation_id,
    genai_span,
    new_call_id,
    policy_type_slug,
    request_correlation_id,
    start_genai_span,
)

# Default request-header NAMES for the two correlation ids (§2.3, #195). Both are
# UNVERIFIED placeholders: the gateway ECHOES ``x-correlation-id`` on RESPONSES
# (verified), but whether it READS an inbound correlation/call-id header — and
# under what name — is not. A customer overrides them per-Donkey via config
# (``DonkeyConfig.correlation_header`` / ``.call_id_header``), resolved by each
# client at construction (see :func:`_resolve_header_names`). Referencing the
# placeholder VALUE (not ``.get()``) keeps these in sync with ``core/_verify``
# without emitting the one-time unverified warning at import; the warning fires
# once, at client construction, when a name is left un-overridden.
CORRELATION_HEADER = _verify.CORRELATION_ID_HEADER.placeholder
CALL_ID_HEADER = _verify.CALL_ID_HEADER.placeholder
# The OpenAI-compatible SDKs reject an empty ``api_key``. The governed proxy
# authenticates on the client_id/client_secret headers (client-id-enforcement,
# §2/§3) and ignores the bearer, so we fill the slot with a harmless sentinel
# whenever no explicit key is configured.
PROXY_API_KEY_SENTINEL = "client-id-enforced"
# 429 is deliberately NOT here: on this proxy every 429 is a token-budget
# refusal that classify() maps to TokenBudgetExceeded (a PolicyViolation), and a
# PolicyViolation is terminal — retrying it only burns the same exhausted budget
# window (§2.4, #183). Only genuinely transient upstream/gateway failures retry.
_RETRYABLE_STATUS = frozenset({502, 503, 504})
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 30.0


# The four cost dimensions → the config field that overrides that header name →
# the UNVERIFIED placeholder used when there is no override (§3, #196). The
# gateway-side names are the highest-priority unknown, so each is a loud,
# overridable placeholder resolved here.
_COST_HEADER_SOURCES: tuple[tuple[str, str, _verify.Unverified], ...] = (
    ("team", "cost_team_header", _verify.COST_TEAM_HEADER),
    ("project", "cost_project_header", _verify.COST_PROJECT_HEADER),
    ("env", "cost_env_header", _verify.COST_ENV_HEADER),
    ("enduser_id", "cost_enduser_header", _verify.COST_ENDUSER_HEADER),
)


def cost_headers(cfg: DonkeyConfig, tags: CostTags) -> dict[str, str]:
    """The request headers for the SET dimensions of ``tags`` (§3, BG §1.7, #196).

    Each header NAME is the config override (``cost_*_header``) if set, else the
    UNVERIFIED placeholder from ``core/_verify`` — the gateway-side cost header
    name is the single highest-priority unknown (docs §3), so an un-overridden
    name emits the one-time §0.3 warning. Values are pre-validated by
    :class:`CostTags`, so they are always header-safe."""
    override = {
        field: getattr(cfg, attr) for field, attr, _placeholder in _COST_HEADER_SOURCES
    }
    placeholder = {field: ph for field, _attr, ph in _COST_HEADER_SOURCES}
    headers: dict[str, str] = {}
    for field, value in tags.items():
        name = override[field] or placeholder[field].get()
        headers[name] = value
    return headers


def effective_cost_tags(cfg: DonkeyConfig) -> CostTags:
    """The cost tags in force for the current call: the configured tags with any
    ``donkey.run(...)`` per-run overrides merged on top, per field (#196). Read
    at send/record time so a run-scope override reaches every call in its block."""
    run = current_cost_tags()
    return cfg.cost.merge(run) if run is not None else cfg.cost


def attribution_headers(cfg: DonkeyConfig) -> dict[str, str]:
    """A snapshot of attribution headers for frameworks that only accept a
    ``default_headers`` dict. Does NOT include the correlation ID (which must be
    per-run) or the bearer token (which must be refreshed lazily).

    Header NAMES are UNVERIFIED (§0.3 / §3): the live direct-proxy path did NOT
    surface application/business-group as request headers (docs §3), so these
    remain loud, overridable placeholders. The verified per-agent attribution
    unit is the ``client_id`` credential — see :func:`proxy_auth_headers`.

    Includes the CONFIG-LEVEL cost tags (§3, #196). A static ``default_headers``
    snapshot cannot see a later ``donkey.run(...)`` override — that binding is a
    contextvar the live client reads per send — so the snapshot path carries the
    set-once tags only, a documented degradation (like the per-run correlation
    ID, §3.3)."""

    headers: dict[str, str] = {}
    if cfg.application_name:
        headers[_verify.ATTRIBUTION_APP_HEADER.get()] = cfg.application_name
    if cfg.business_group:
        headers[_verify.ATTRIBUTION_BUSINESS_GROUP_HEADER.get()] = cfg.business_group
    headers.update(cost_headers(cfg, cfg.cost))
    return headers


def proxy_auth_headers(cfg: DonkeyConfig) -> dict[str, str]:
    """The LLM-proxy consumer-auth request headers, LIVE-VERIFIED (docs §2/§3):
    a ``client_id`` + ``client_secret`` pair enforced by ``client-id-enforcement``.
    This pair IS the per-agent attribution identity, NOT a bearer token.

    Combined here with :func:`attribution_headers` so a single ``default_headers``
    snapshot carries both when handed to a native framework client. Missing
    credentials are simply omitted — :meth:`DonkeyConfig.validated` is where the
    absence is reported with actionable guidance.
    """

    headers = attribution_headers(cfg)
    if cfg.llm_proxy_client_id:
        headers[_verify.LLM_PROXY_CLIENT_ID_HEADER] = cfg.llm_proxy_client_id
    if cfg.llm_proxy_client_secret:
        headers[_verify.LLM_PROXY_CLIENT_SECRET_HEADER] = cfg.llm_proxy_client_secret
    return headers


def proxy_api_key(cfg: DonkeyConfig) -> str:
    """The value for the OpenAI-compatible SDK's mandatory ``api_key`` slot: the
    configured key if any, else :data:`PROXY_API_KEY_SENTINEL` (the proxy ignores
    it and enforces the client_id/secret headers instead)."""
    return cfg.llm_proxy_key or PROXY_API_KEY_SENTINEL


def _resolve_header_names(cfg: DonkeyConfig) -> tuple[str, str]:
    """The ``(correlation, call_id)`` request-header NAMES for this config (§2.3,
    #195): each is the config override if set, else the UNVERIFIED placeholder
    from ``core/_verify``. Called ONCE per client at construction, so the
    one-time unverified warning for an un-overridden name fires there, not on
    every request."""
    correlation = cfg.correlation_header or _verify.CORRELATION_ID_HEADER.get()
    call_id = cfg.call_id_header or _verify.CALL_ID_HEADER.get()
    return correlation, call_id


def _apply_base_headers(
    cfg: DonkeyConfig,
    request: httpx.Request,
    correlation_id: str,
    *,
    correlation_header: str,
) -> None:
    """The run correlation ID + attribution — everything both transports inject
    on EVERY send without needing to await anything. The correlation ID is
    passed in because the two transports source it differently (see
    :func:`request_correlation_id`); it is deterministic per run (a contextvar),
    so re-setting it on each retry is idempotent. The header NAME is resolved
    once by the client. The per-call ID is deliberately NOT set here — being
    random, it must be pinned once before the retry loop
    (:func:`_apply_call_id_header`), never re-rolled per send.

    ``attribution_headers`` already carries the CONFIG-LEVEL cost tags; any
    ``donkey.run(...)`` per-run overrides are applied on top here (read from the
    contextvar per send), so a run-scope dimension wins for its block (#196)."""
    request.headers[correlation_header] = correlation_id
    # Stamp the resolved name so read-back (errors._sent_ids) honours a
    # header-name override without core/errors importing DonkeyConfig (#363).
    # Idempotent across retries — same name each send.
    request.extensions["donkey_correlation_header"] = correlation_header
    for name, value in attribution_headers(cfg).items():
        request.headers[name] = value
    run = current_cost_tags()
    if run is not None:
        for name, value in cost_headers(cfg, run).items():
            request.headers[name] = value


def _apply_call_id_header(request: httpx.Request, call_id_header: str) -> None:
    """Pin a FRESH per-call ID on the request, ONCE, before the retry loop
    (§2.3, #195).

    The run/correlation id is deterministic per run (a contextvar), so the
    per-send event hook can safely re-set it on every retry. The call id is
    random and must be UNIQUE per logical request yet STABLE across that
    request's retries and 401 refresh — so it is pinned here, on the single
    request object that is re-sent, exactly once. It is therefore already on the
    request even when the send fails at the transport layer before any response,
    so an error built from ``response.request`` can always read it back."""
    request.headers[call_id_header] = new_call_id()
    # Stamp the resolved name (once, beside the header) so read-back honours a
    # header-name override — the read-back counterpart of the header write (#363).
    request.extensions["donkey_call_id_header"] = call_id_header


def _retry_delay(attempt: int, response: httpx.Response) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after is not None:
        try:
            return min(float(retry_after), _BACKOFF_CAP_S)
        except ValueError:
            pass  # HTTP-date form not handled here; fall through to backoff
    exp = min(_BACKOFF_BASE_S * (2.0**attempt), _BACKOFF_CAP_S)
    return exp * (0.5 + random.random() / 2.0)  # full-ish jitter


# --- GenAI span extraction (#192, BG §1.6) ----------------------------------
# Shared by both transports (sync + async). Each takes a plain response/request,
# so the span-recording logic lives in one place and cannot drift between the
# two clients. The response header carrying the resolved upstream provider is
# VERIFIED (LIVE) — docs/verified-apis.md §2 "Model routing" and §3 "Gateway
# identity on response" — and is the SOLE source of gen_ai.system; absent → the
# attribute is omitted, never guessed (§0.3), because the proxy routes to
# several providers and defaulting one would misattribute the call.
_PROVIDER_HEADER = "x-llm-proxy-llm-provider"
# Streaming (SSE) responses carry no usage on the envelope; it lives in a
# terminal event, captured by the span-closing stream wrapper (#193).
_STREAM_CONTENT_TYPE = "text/event-stream"


def _request_model(request: httpx.Request) -> str | None:
    """The requested model from the request's JSON body (``gen_ai.request.model``),
    or ``None`` when the body is absent, unreadable, not JSON, or carries no
    ``model``. A ``None`` marks "not a GenAI call": no span is opened, so GETs,
    token fetches and bodyless POSTs stay byte-identical."""
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


def _first_int(mapping: dict[str, object], *keys: str) -> int | None:
    """The first key present as an ``int`` (``bool`` excluded — it subclasses int)."""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _usage_tokens(response: httpx.Response) -> tuple[int | None, int | None]:
    """``(input, output)`` token counts from a buffered 2xx JSON body, else
    ``(None, None)``. Handles the Responses API (``input_tokens`` /
    ``output_tokens``) and Chat Completions (``prompt_tokens`` /
    ``completion_tokens``). SSE bodies and non-2xx refusals carry no usage here."""
    if response.status_code // 100 != 2:
        return None, None
    if _STREAM_CONTENT_TYPE in response.headers.get("content-type", ""):
        return None, None
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 — unread/streaming/invalid body: no usage
        return None, None
    if not isinstance(body, dict):
        return None, None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None, None
    return (
        _first_int(usage, "input_tokens", "prompt_tokens"),
        _first_int(usage, "output_tokens", "completion_tokens"),
    )


def _span_decision(response: httpx.Response) -> tuple[str | None, str | None]:
    """``(donkey.policy.decision, donkey.policy.type)`` for the final response:
    ``allow`` on 2xx; ``refuse`` + a policy-type slug when :func:`classify` maps
    the refusal to a :class:`~.errors.PolicyViolation`; ``(None, None)`` for a
    non-policy error (auth / upstream / 5xx), so the transport omits the decision
    rather than misreporting an allow or a refuse."""
    if response.status_code // 100 == 2:
        return POLICY_DECISION_ALLOW, None
    slug = policy_type_slug(classify(response))
    if slug is None:
        return None, None
    return POLICY_DECISION_REFUSE, slug


def _record_response(
    gspan: GenAiSpan,
    request: httpx.Request,
    response: httpx.Response,
    budget: Budget | None,
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
    the run id is the cross-call join key (§2.3, #195)."""
    try:
        decision, policy_type = _span_decision(response)
        input_tokens, output_tokens = _usage_tokens(response)
        gspan.record(
            system=response.headers.get(_PROVIDER_HEADER),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
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
        pass


# --- streaming span lifecycle (#193, BG §1.6) -------------------------------
# A streamed completion carries its usage in the TERMINAL SSE event, read by the
# caller long after send() has returned. So the streaming span is DETACHED
# (start_genai_span) and handed to a wrapper around the response's byte stream:
# httpx routes BOTH iteration and response.aclose()/close() through
# ``response.stream`` (verified against httpx 0.28), so a wrapper there sees every
# close path — full drain, mid-iteration abandonment, and exception — and is the
# one place that can fill usage and end the span exactly once.


def _is_streaming_success(response: httpx.Response) -> bool:
    """True when a ``stream=True`` request returned a streamable 2xx SSE body —
    the only case whose span must outlive ``send()``. A non-2xx refusal or a
    buffered non-SSE 2xx on a stream request is finished inline instead."""
    if response.status_code // 100 != 2:
        return False
    return _STREAM_CONTENT_TYPE in response.headers.get("content-type", "")


def _extract_usage_tokens(obj: object) -> tuple[int | None, int | None] | None:
    """``(input, output)`` from a parsed SSE ``data:`` object that carries a
    ``usage`` block, else ``None``. Handles Chat Completions (top-level ``usage``
    with ``prompt_tokens``/``completion_tokens``) and the Responses API (``usage``
    nested under ``response`` with ``input_tokens``/``output_tokens``)."""
    if not isinstance(obj, dict):
        return None
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        nested = obj.get("response")
        usage = nested.get("usage") if isinstance(nested, dict) else None
    if not isinstance(usage, dict):
        return None
    return (
        _first_int(usage, "input_tokens", "prompt_tokens"),
        _first_int(usage, "output_tokens", "completion_tokens"),
    )


class _SseUsageScanner:
    """Incrementally scans an SSE byte stream for the terminal ``usage`` event,
    keeping the latest observed token counts (#193).

    Line-buffered, so it reconstructs ``data:`` lines across arbitrary chunk
    boundaries, and it only parses JSON for lines that mention ``usage`` — a
    cheap substring test skips the vast majority of delta events, so memory and
    CPU stay bounded no matter how long the completion is (buffering the whole
    body would defeat the point of streaming)."""

    __slots__ = ("_buf", "input_tokens", "output_tokens")

    def __init__(self) -> None:
        self._buf: bytes = b""
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None

    def feed(self, chunk: bytes) -> None:
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        for line in lines:
            self._scan(line)

    def close(self) -> None:
        """Scan any trailing partial line (a final event without a newline)."""
        if self._buf:
            self._scan(self._buf)
            self._buf = b""

    def _scan(self, line: bytes) -> None:
        if b'"usage"' not in line:
            return
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            return
        payload = stripped[len(b"data:") :].strip()
        try:
            obj = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        tokens = _extract_usage_tokens(obj)
        if tokens is None:
            return
        input_tokens, output_tokens = tokens
        if input_tokens is not None:
            self.input_tokens = input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens


class _SpanClosingStream:
    """Shared finalize logic for the stream wrappers: record the scanned usage
    onto the detached span and end it, exactly once and best-effort — telemetry
    must never break stream teardown."""

    def __init__(self, gspan: GenAiSpan) -> None:
        self._gspan = gspan
        self._scanner = _SseUsageScanner()
        self._finalized = False

    def _finalize(self) -> None:
        if self._finalized:
            return
        self._finalized = True
        try:
            self._scanner.close()
            self._gspan.record(
                input_tokens=self._scanner.input_tokens,
                output_tokens=self._scanner.output_tokens,
            )
        except Exception:  # noqa: BLE001 — telemetry must never break teardown
            pass
        self._gspan.end()


class _SpanClosingAsyncStream(_SpanClosingStream, httpx.AsyncByteStream):
    """Wraps a streaming response's byte stream so the GenAI span closes when the
    stream does — on full drain, mid-iteration abandonment, or exception — with
    ``gen_ai.usage.*`` filled from the terminal SSE event (#193)."""

    def __init__(self, inner: httpx.AsyncByteStream, gspan: GenAiSpan) -> None:
        super().__init__(gspan)
        self._inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            self._scanner.feed(chunk)
            yield chunk

    async def aclose(self) -> None:
        try:
            self._finalize()
        finally:
            await self._inner.aclose()


class _SpanClosingSyncStream(_SpanClosingStream, httpx.SyncByteStream):
    """Blocking twin of :class:`_SpanClosingAsyncStream`."""

    def __init__(self, inner: httpx.SyncByteStream, gspan: GenAiSpan) -> None:
        super().__init__(gspan)
        self._inner = inner

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self._inner:
            self._scanner.feed(chunk)
            yield chunk

    def close(self) -> None:
        try:
            self._finalize()
        finally:
            self._inner.close()


class DonkeyAsyncClient(httpx.AsyncClient):
    """An ``httpx.AsyncClient`` that injects attribution/correlation/auth headers
    and applies the SDK's retry policy. Every adapter that accepts a custom HTTP
    client MUST be given one of these."""

    def __init__(
        self,
        cfg: DonkeyConfig,
        auth: AuthProvider | None,
        *,
        budget: Budget | None = None,
        **kw: object,
    ) -> None:
        self._cfg = cfg
        # The two correlation request-header NAMES, resolved once (config override
        # → UNVERIFIED placeholder). The one-time §0.3 warning for an un-overridden
        # name fires here, at construction, not per request (§2.3, #195).
        self._correlation_header, self._call_id_header = _resolve_header_names(cfg)
        # NB: httpx.AsyncClient uses ``self._auth`` internally, so we must NOT
        # store our token provider there — super().__init__() would clobber it.
        self._token_provider = auth
        # Optional in-band budget collaborator (§1.3, #185). When attached, the
        # response hook feeds it; when None the hook stays a byte-identical no-op,
        # so the control-plane token-fetch client tracks no budget.
        self._budget = budget
        super().__init__(
            timeout=cfg.timeout_s,
            event_hooks={"request": [self._inject_headers]},
            **kw,  # type: ignore[arg-type]
        )

    async def _inject_headers(self, request: httpx.Request) -> None:
        _apply_base_headers(
            self._cfg,
            request,
            ensure_correlation_id(),
            correlation_header=self._correlation_header,
        )
        if self._token_provider is not None:
            token = await self._token_provider.token()
            # Control plane uses OAuth2 client_credentials → ``Authorization:
            # Bearer`` (VERIFIED §12.1). The LLM proxy (data plane) instead uses
            # client_id/client_secret headers and gets NO token provider, so it
            # never reaches here; ``setdefault`` also yields to the OpenAI SDK's
            # own Authorization if one was set at the call site.
            request.headers.setdefault("Authorization", f"Bearer {token}")

    # --- lifecycle hooks (the skeleton's attachment points, BG §1.1) --------
    # These are the *logical* per-``send()`` seams the Phase 1 six-piece minimum
    # plugs into, distinct from the per-wire ``_inject_headers`` event hook.
    # Internal (underscore-prefixed) extension points for the SDK's own layers,
    # NOT public API. Defaults are no-ops: a hookless client behaves exactly as
    # before. Subclasses/layers override; do not call these directly.

    async def _on_request(self, request: httpx.Request) -> None:
        """Called once, before the retry loop. Attachment point for correlation
        IDs, cost tags and span start (budget/telemetry issues plug in here)."""

    async def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        """Called once with the final response returned to the caller (after
        retries and any 401 refresh settle). Attachment point for budget parsing,
        span end and ``classify()``.

        Feeds the attached :class:`Budget` from the response's ``x-token-*``
        headers (#185); a no-op when none is attached. A subclass that overrides
        this hook must call ``super()._on_response(...)`` to keep budget tracking."""
        if self._budget is not None:
            self._budget.observe(response)

    async def _on_refusal(self, violation: object) -> None:
        """Refusal seam for Phase 2 reaction handlers. Defined here so the
        attachment point exists; there is no caller until ``classify()`` (#181)
        produces a typed violation. ``violation`` is typed ``object`` until then."""

    def _swap_transport(self, transport: httpx.AsyncBaseTransport) -> None:
        """Replace the underlying transport on a live client. httpx resolves the
        transport per-send from ``self._transport`` (we mount nothing), so the
        next request uses ``transport`` with no reconstruction. This is the seam
        ``simulate()`` (#190) and ``donkey mock`` (#187) swap a fixture into."""
        self._transport = transport

    async def send(
        self,
        request: httpx.Request,
        **kwargs: object,
    ) -> httpx.Response:
        model = _request_model(request)
        # A GenAI span is opened only for a model call (a JSON body carrying a
        # ``model``); GETs, token fetches and bodyless POSTs open none and stay
        # byte-identical (#192).
        enabled = self._cfg.telemetry and model is not None
        # ``stream=True`` on the transport is the streaming path (#193): the usage
        # lands in the terminal SSE event, read by the caller long after send()
        # returns. That span must OUTLIVE the ``with`` block, so it is detached
        # (started here, ended by the stream wrapper in ``_finish``); a buffered
        # call keeps the auto-closing context manager (#192).
        if enabled and bool(kwargs.get("stream")):
            gspan = start_genai_span(
                enabled=True, capture_content=self._cfg.telemetry_capture_content
            )
            try:
                gspan.record(request_model=model)
                return await self._send_with_retries(request, gspan, kwargs, streaming=True)
            except BaseException:
                # A transport error (or cancellation) before a response settles:
                # no stream wrapper will ever be created to close the detached
                # span, so mark it failed and end it here (#193, AC #4).
                gspan.set_error()
                gspan.end()
                raise
        # Buffered path. The span is a context manager so it closes on the way out
        # even when ``super().send()`` raises before a response exists — a
        # transport error escapes ``_finish``, so the lifecycle cannot rely on it
        # (see ``_finish``'s note, #179/#192).
        with genai_span(
            enabled=enabled, capture_content=self._cfg.telemetry_capture_content
        ) as gspan:
            gspan.record(request_model=model)
            return await self._send_with_retries(request, gspan, kwargs, streaming=False)

    async def _send_with_retries(
        self,
        request: httpx.Request,
        gspan: GenAiSpan,
        kwargs: dict[str, object],
        *,
        streaming: bool,
    ) -> httpx.Response:
        """The retry / 401-refresh loop, shared by the buffered and streaming
        paths. ``streaming`` is threaded through to :meth:`_finish` so a streamed
        2xx gets its span-closing stream wrapper while a buffered response keeps
        the context-manager lifecycle."""
        # Pin the per-call id ONCE, before the loop, so it is stable across
        # retries and the 401 refresh (§2.3, #195). The run correlation id is set
        # per-send by the event hook (deterministic, so idempotent).
        _apply_call_id_header(request, self._call_id_header)
        await self._on_request(request)
        attempts = self._cfg.max_retries + 1
        refreshed_once = False
        last_response: httpx.Response | None = None

        attempt = 0
        while attempt < attempts:
            response = await super().send(request, **kwargs)  # type: ignore[arg-type]
            last_response = response

            provider = self._token_provider
            can_refresh = provider is not None and not refreshed_once
            if response.status_code == 401 and can_refresh:
                assert provider is not None  # narrowed by can_refresh
                refreshed_once = True
                await response.aclose()
                await provider.invalidate()
                # A 401 refresh is an auth re-send, not a rate-limit backoff, so it
                # does NOT consume the retry budget (§2.2: "retry exactly once on
                # 401"): re-send once with the fresh token regardless of `attempt`,
                # so the retry still happens on the final attempt / max_retries=0.
                # Event hooks re-run on send() → fresh token.
                continue

            if response.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                delay = _retry_delay(attempt, response)
                await response.aclose()
                await asyncio.sleep(delay)
                attempt += 1
                continue

            return await self._finish(request, response, gspan, streaming=streaming)

        assert last_response is not None  # attempts >= 1
        return await self._finish(request, last_response, gspan, streaming=streaming)

    async def _finish(
        self,
        request: httpx.Request,
        response: httpx.Response,
        gspan: GenAiSpan,
        *,
        streaming: bool,
    ) -> httpx.Response:
        """Fire the response hook exactly once, on the final response returned to
        the caller, then record the response attributes on the GenAI span. Retry/
        refresh ``continue`` branches close their intermediate response and loop
        instead of funnelling through here, so ``_on_response`` only ever sees the
        response actually returned — never a closed one.

        ``_record_response`` runs after ``_on_response`` so the span's
        ``donkey.budget.remaining`` reflects the budget the same response just fed
        (#185/#192); it never raises, so telemetry can't mask the caller's result.

        Buffered path (``streaming=False``): a transport-level error escapes
        ``super().send()`` before we reach here, so ``_on_response``/
        ``_record_response`` cannot run to mask the underlying HTTP error (AC #4).
        The span still closes: it is opened as a context manager around the whole
        retry loop in :meth:`send`, so a network failure that skips ``_finish``
        closes the span (with the request model already recorded) on the way out —
        the span lifecycle does NOT rely on this hook (#179/#192).

        Streaming path (``streaming=True``, #193): the span is detached, so it is
        ended HERE. A streamed 2xx SSE body has its usage in a terminal event the
        caller reads later, so the still-open span is handed to a
        :class:`_SpanClosingAsyncStream` that fills ``gen_ai.usage.*`` and ends it
        when the stream closes — on drain, mid-iteration abandonment, or exception.
        A buffered 2xx or a refusal on a stream request has no SSE body to scan, so
        the span is ended inline (``_record_response`` already set its decision,
        usage and — for a refusal — ERROR status)."""
        await self._on_response(request, response)
        _record_response(
            gspan,
            request,
            response,
            self._budget,
            correlation_header=self._correlation_header,
            cost_tags=effective_cost_tags(self._cfg),
        )
        if streaming:
            if _is_streaming_success(response):
                # An async client's response.stream is an AsyncByteStream; httpx
                # types the attribute as the sync|async union, hence the narrowing.
                response.stream = _SpanClosingAsyncStream(response.stream, gspan)  # type: ignore[arg-type]
            else:
                gspan.end()
        return response


class DonkeyClient(httpx.Client):
    """The blocking twin of :class:`DonkeyAsyncClient`, for ``donkey.llm.client(
    sync=True)``.

    It injects the same correlation/attribution headers and applies the same
    retry policy, so a synchronous caller is governed identically to an async
    one. Without it, a sync caller would fall back to whatever bare client the
    framework builds for itself and quietly lose both.

    It takes **no** :class:`AuthProvider`: that protocol is async-only
    (``async def token()``), and there is no correct way to await it from here.
    That costs nothing on the LLM data plane, which authenticates with the
    ``client_id``/``client_secret`` header pair (LIVE-VERIFIED §2/§3) rather than
    a fetched token. It does mean the control-plane surfaces — ``registry`` and
    ``tools`` — stay async-only; see §2.2 for why the two credentials are
    deliberately not conflated.
    """

    def __init__(self, cfg: DonkeyConfig, *, budget: Budget | None = None, **kw: object) -> None:
        self._cfg = cfg
        # Resolved once; see DonkeyAsyncClient.__init__ (§2.3, #195).
        self._correlation_header, self._call_id_header = _resolve_header_names(cfg)
        self._budget = budget  # see DonkeyAsyncClient.__init__ (§1.3, #185)
        super().__init__(
            timeout=cfg.timeout_s,
            event_hooks={"request": [self._inject_headers]},
            **kw,  # type: ignore[arg-type]
        )

    def _inject_headers(self, request: httpx.Request) -> None:
        _apply_base_headers(
            self._cfg,
            request,
            request_correlation_id(),
            correlation_header=self._correlation_header,
        )

    # --- lifecycle hooks (BG §1.1) ------------------------------------------
    # Synchronous twins of the async seams, kept in lockstep so a blocking caller
    # is governed identically. Defaults are no-ops; internal, not public API.

    def _on_request(self, request: httpx.Request) -> None:
        """Called once, before the retry loop (see :meth:`DonkeyAsyncClient._on_request`)."""

    def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        """Called once with the final response returned to the caller. Feeds the
        attached :class:`Budget` (#185); a no-op when none is attached. A subclass
        that overrides this must call ``super()._on_response(...)``."""
        if self._budget is not None:
            self._budget.observe(response)

    def _on_refusal(self, violation: object) -> None:
        """Refusal seam for Phase 2; no caller until ``classify()`` (#181)."""

    def _swap_transport(self, transport: httpx.BaseTransport) -> None:
        """Replace the underlying transport on a live client; the next request
        uses it (see :meth:`DonkeyAsyncClient._swap_transport`)."""
        self._transport = transport

    def send(self, request: httpx.Request, **kwargs: object) -> httpx.Response:
        model = _request_model(request)
        enabled = self._cfg.telemetry and model is not None  # see DonkeyAsyncClient.send
        if enabled and bool(kwargs.get("stream")):
            # Streaming: a detached span the stream wrapper ends (see
            # DonkeyAsyncClient.send, #193).
            gspan = start_genai_span(
                enabled=True, capture_content=self._cfg.telemetry_capture_content
            )
            try:
                gspan.record(request_model=model)
                return self._send_with_retries(request, gspan, kwargs, streaming=True)
            except BaseException:
                gspan.set_error()
                gspan.end()
                raise
        with genai_span(
            enabled=enabled, capture_content=self._cfg.telemetry_capture_content
        ) as gspan:
            gspan.record(request_model=model)
            return self._send_with_retries(request, gspan, kwargs, streaming=False)

    def _send_with_retries(
        self,
        request: httpx.Request,
        gspan: GenAiSpan,
        kwargs: dict[str, object],
        *,
        streaming: bool,
    ) -> httpx.Response:
        """The retry loop, shared by the buffered and streaming paths (see
        :meth:`DonkeyAsyncClient._send_with_retries`)."""
        # Pin the per-call id once, before the loop (see the async twin, #195).
        _apply_call_id_header(request, self._call_id_header)
        self._on_request(request)
        attempts = self._cfg.max_retries + 1
        last_response: httpx.Response | None = None

        for attempt in range(attempts):
            response = super().send(request, **kwargs)  # type: ignore[arg-type]
            last_response = response

            # No 401-refresh branch: with no token provider there is nothing to
            # refresh, so a 401 here is a real credential failure and terminal.
            if response.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                delay = _retry_delay(attempt, response)
                response.close()
                time.sleep(delay)
                continue

            return self._finish(request, response, gspan, streaming=streaming)

        assert last_response is not None  # attempts >= 1
        return self._finish(request, last_response, gspan, streaming=streaming)

    def _finish(
        self,
        request: httpx.Request,
        response: httpx.Response,
        gspan: GenAiSpan,
        *,
        streaming: bool,
    ) -> httpx.Response:
        """Fire the response hook exactly once, on the response actually returned,
        then record the response attributes on the GenAI span. Streaming (#193)
        hands a 2xx SSE body to a :class:`_SpanClosingSyncStream` that ends the
        detached span on close; a buffered/refused stream response ends it inline
        (see :meth:`DonkeyAsyncClient._finish`)."""
        self._on_response(request, response)
        _record_response(
            gspan,
            request,
            response,
            self._budget,
            correlation_header=self._correlation_header,
            cost_tags=effective_cost_tags(self._cfg),
        )
        if streaming:
            if _is_streaming_success(response):
                # A sync client's response.stream is a SyncByteStream; httpx types
                # the attribute as the sync|async union, hence the narrowing.
                response.stream = _SpanClosingSyncStream(response.stream, gspan)  # type: ignore[arg-type]
            else:
                gspan.end()
        return response


def build_http_client(
    cfg: DonkeyConfig,
    auth: AuthProvider | None,
    *,
    budget: Budget | None = None,
) -> DonkeyAsyncClient:
    """Factory for the shared client (§2.3). Pass ``budget`` to track the in-band
    token window on every response (§1.3, #185); omit it for the control-plane
    token-fetch client, which observes no budget."""
    return DonkeyAsyncClient(cfg, auth, budget=budget)


def build_sync_http_client(cfg: DonkeyConfig, *, budget: Budget | None = None) -> DonkeyClient:
    """Factory for the shared blocking client (§2.3). See :class:`DonkeyClient`
    for why it takes no :class:`AuthProvider`. Pass ``budget`` to share one budget
    object with the async client (§1.3, #185)."""
    return DonkeyClient(cfg, budget=budget)
