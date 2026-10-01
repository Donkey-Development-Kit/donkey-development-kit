"""Transport — the single place headers get injected (BG §1.1).

This is the most important piece of engineering in the SDK. Every framework has
a different mechanism for setting request headers, and several have none. The
solution is one shared HTTP client per credential plane: every adapter is handed
the data-plane (LLM proxy) client, and Anypoint platform calls use a separate
control-plane client, so neither credential ever rides the other's requests.

The client:
  * injects, via a request event hook, on every outbound request:
      - the run correlation ID (uuid4 per logical agent run, from a contextvar,
        BG §1.6) — shared by every request in a ``donkey.run()`` block, the
        client↔gateway join key
      - attribution headers (application, business group) — header NAMES are
        UNVERIFIED (docs/verified-apis.md §3), emitted via loud placeholders
      - bearer token, refreshed lazily
  * pins a per-call ID ONCE, before the retry loop, so it is unique per logical
    request yet stable across that request's retries and 401 refresh (BG §1.1,
    #195). Two ids, two headers: the run id (``X-Correlation-Id``) groups a run;
    the call id (``X-Donkey-Request-Id``) pinpoints one request within it. Both
    header NAMES are UNVERIFIED placeholders (docs/verified-apis.md §3), overridable via config.
  * retries transient upstream/gateway failures (502/503/504) with exponential
    backoff + jitter, honouring Retry-After
  * does NOT retry 4xx — gateway policy rejections are terminal (BG §1.2). This
    includes 429: on this proxy a 429 is a token-budget refusal
    (TokenBudgetExceeded), and retrying it only burns the same exhausted window
    (BG §1.2, #183). retry_after is still surfaced for wait_for_reset() (#186).
  * refreshes the attached provider's token and retries exactly once on 401
    (BG §1.1); a client-id data-plane client has no provider, so its 401 is
    terminal

For frameworks that only accept a ``default_headers`` dict (not a client), pass
:func:`attribution_headers` — a snapshot — and accept that the correlation ID is
per-client rather than per-run. Document that degradation per adapter (BG §1.8).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Iterable, Iterator, Sized
from types import TracebackType
from typing import Any, Protocol

import httpx

from . import _verify
from .auth import AuthProvider
from .budget import Budget
from .cachecontrol import current_cache_controls
from .config import TOKEN_AUTH_MODES, DonkeyConfig, LlmProxyAuth
from .cost import CostTags
from .endpoints import require_secure_url
from .errors import (
    ConfigError,
    GatewayUnavailable,
    ModelSubstituted,
    classify,
    gateway_unavailable,
    parse_retry_after,
)
from .lastcall import (
    LLM_MODEL_HEADER,
    LLM_PROVIDER_HEADER,
    ROUTING_TYPE_HEADER,
    is_fallback,
    is_substitution,
    observe_last_call,
    observe_usage,
    parse_usage,
    request_id,
    routing_fallback,
    semantic_cache,
    semantic_routing,
    usage_from_response,
    usage_mapping,
)
from .masking import SENSITIVE_NAMES, masked
from .telemetry import (
    POLICY_DECISION_ALLOW,
    POLICY_DECISION_REFUSE,
    GenAiSpan,
    current_correlation_id,
    current_cost_tags,
    genai_span,
    new_call_id,
    policy_type_slug,
    request_correlation_id,
    start_genai_span,
)

# Default request-header NAMES for the two correlation ids (BG §1.1, #195;
# docs/verified-apis.md §3): the gateway reads and echoes ``X-Correlation-Id``,
# and ``X-Donkey-Request-Id`` is the SDK's own per-call id. A customer overrides
# them per-Donkey via config (``DonkeyConfig.correlation_header`` /
# ``.call_id_header``), resolved by each client at construction (see
# :func:`_resolve_header_names`).
CORRELATION_HEADER = _verify.CORRELATION_ID_HEADER
CALL_ID_HEADER = _verify.CALL_ID_HEADER
# The OpenAI-compatible SDKs reject an empty ``api_key``. The governed proxy
# authenticates on the client_id/client_secret headers (client-id-enforcement,
# docs/verified-apis.md §2/§3) and ignores the bearer, so we fill the slot with a harmless sentinel
# whenever no explicit key is configured.
PROXY_API_KEY_SENTINEL = "client-id-enforced"
# 429 is deliberately NOT here: on this proxy every 429 is a token-budget
# refusal that classify() maps to TokenBudgetExceeded (a PolicyViolation), and a
# PolicyViolation is terminal — retrying it only burns the same exhausted budget
# window (BG §1.2, #183). Only genuinely transient upstream/gateway failures retry.
_RETRYABLE_STATUS = frozenset({502, 503, 504})
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 30.0
# A non-2xx body on a stream request is read up to this many bytes before the
# span is recorded, so classify() sees the same JSON a buffered refusal has
# (#805). Proxy error envelopes are a few hundred bytes; past the cap the body is
# left for the caller and the span falls back to what the headers say.
_ERROR_BODY_CAP = 64 * 1024

_log = logging.getLogger(__name__)


# The four cost dimensions → the config field that overrides that header name →
# the default name used when there is no override (docs/verified-apis.md §3, #196).
_COST_HEADER_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("team", "cost_team_header", _verify.COST_TEAM_HEADER),
    ("project", "cost_project_header", _verify.COST_PROJECT_HEADER),
    ("env", "cost_env_header", _verify.COST_ENV_HEADER),
    ("enduser_id", "cost_enduser_header", _verify.COST_ENDUSER_HEADER),
)


def cost_headers(cfg: DonkeyConfig, tags: CostTags) -> dict[str, str]:
    """The request headers for the SET dimensions of ``tags``
    (docs/verified-apis.md §3, BG §1.7, #196).

    Each header NAME is the config override (``cost_*_header``) if set, else the
    default from ``core/_verify``. The LLM Gateway ingests no cost-tag header
    (docs/verified-apis.md §3), so the name is a forward-looking convention — the
    authoritative carrier is the ``donkey.cost.*`` span attribute. The transport therefore
    sends these only when ``DonkeyConfig.send_cost_headers`` is enabled; this
    builder itself does not check the flag. Values are pre-validated by
    :class:`CostTags`, so they are always header-safe."""
    override = {
        field: getattr(cfg, attr) for field, attr, _default in _COST_HEADER_SOURCES
    }
    default = {field: name for field, _attr, name in _COST_HEADER_SOURCES}
    headers: dict[str, str] = {}
    for field, value in tags.items():
        name = override[field] or default[field]
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

    Header NAMES are UNVERIFIED (verification discipline / docs/verified-apis.md §3):
    the live direct-proxy path did NOT
    surface application/business-group as request headers (docs/verified-apis.md §3), so these
    remain loud placeholders with no config override. The verified per-agent attribution
    unit is the ``client_id`` credential — see :func:`proxy_auth_headers`.

    Includes the CONFIG-LEVEL cost tags only when ``cfg.send_cost_headers`` is
    enabled (docs/verified-apis.md §3, #196): nothing on the gateway reads them.
    A static ``default_headers``
    snapshot cannot see a later ``donkey.run(...)`` override — that binding is a
    contextvar the live client reads per send — so the snapshot path carries the
    set-once tags only, a documented degradation (like the per-run correlation
    ID, BG §1.8)."""

    headers: dict[str, str] = {}
    if cfg.application_name:
        headers[_verify.ATTRIBUTION_APP_HEADER.get()] = cfg.application_name
    if cfg.business_group:
        headers[_verify.ATTRIBUTION_BUSINESS_GROUP_HEADER.get()] = cfg.business_group
    if cfg.send_cost_headers:
        headers.update(cost_headers(cfg, cfg.cost))
    return headers


def proxy_auth_headers(cfg: DonkeyConfig) -> dict[str, str]:
    """The LLM-proxy consumer-auth request headers for the current auth mode
    (BG §1.1), combined with :func:`attribution_headers` so a single
    ``default_headers`` snapshot carries both when handed to a native framework
    client. Missing credentials are simply omitted — :meth:`DonkeyConfig.validated`
    is where the absence is reported with actionable guidance.

    * ``client-id`` mode (default): the ``client_id`` +
      ``client_secret`` pair enforced by ``client-id-enforcement``
      (docs/verified-apis.md §2/§3). This pair IS the per-agent attribution identity.
    * ``jwt`` mode (model-wallet ingress, #372/#509): the durable wallet-selector
      ``X-Client-Id`` only — and NEVER the CIE pair (Client ID Enforcement is
      disabled on a wallet proxy). The rotating JWT is NOT in this snapshot: it is
      injected per-send by :meth:`DonkeyAsyncClient._inject_headers` from the
      attached ``AuthProvider``, because a static snapshot cannot carry a
      credential that rotates.
    * ``bearer`` mode (#836): no consumer-auth header at all. The token is
      injected per-send exactly like the ``jwt`` one, with no wallet selector.

    Returns a :class:`~donkey_kit.core.masking.MaskedDict`: a plain ``dict`` in
    use, but printing it shows ``'***'`` for the secret header.
    """

    headers = masked(attribution_headers(cfg))
    if cfg.llm_proxy_auth == "jwt":
        if cfg.llm_proxy_wallet_client_id:
            headers[_verify.LLM_PROXY_WALLET_CLIENT_ID_HEADER] = cfg.llm_proxy_wallet_client_id
        return headers
    if cfg.llm_proxy_auth == "bearer":
        return headers
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


#: An origin: scheme, lower-cased host, and port (the scheme's default when absent).
Origin = tuple[str, str, int]

_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Lower-cased request-header names that carry or identify a credential: the
#: masked names plus the proxy client-id and wallet-selector headers. A shared
#: client never sends them to an origin it was not checked for.
CREDENTIAL_HEADERS: frozenset[str] = SENSITIVE_NAMES | frozenset(
    {
        _verify.LLM_PROXY_CLIENT_ID_HEADER.lower(),
        _verify.LLM_PROXY_WALLET_CLIENT_ID_HEADER.lower(),
    }
)


def origin_of(url: str | httpx.URL) -> Origin | None:
    """The :data:`Origin` of ``url``, or ``None`` when it has no scheme or host."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        return None
    port = parsed.port or _DEFAULT_PORTS.get(parsed.scheme)
    if not parsed.host or port is None:
        return None
    return (parsed.scheme, parsed.host.lower(), port)


def strip_credential_headers(request: httpx.Request) -> None:
    """Remove every :data:`CREDENTIAL_HEADERS` header from ``request``."""
    for name in [n for n in request.headers if n.lower() in CREDENTIAL_HEADERS]:
        del request.headers[name]


class _CheckedEndpoints:
    """The origins a shared client sends credentials to.

    Seeded with the plane's configured endpoint — the LLM proxy URL on the data
    plane, the control-plane URL on the control plane — which config validation
    checks. A URL passed in code joins through :meth:`allow_endpoint` once it
    passes the same https check. A request to any other origin, a redirect hop
    included (httpx runs request hooks on every hop), is sent without the SDK's
    credentials and with the credential headers a framework set removed.
    """

    _origins: set[Origin]

    def _init_origins(self, endpoint: str | None, origins: set[Origin] | None) -> None:
        self._origins = origins if origins is not None else set()
        seeded = origin_of(endpoint) if endpoint else None
        if seeded is not None:
            self._origins.add(seeded)

    @property
    def checked_origins(self) -> set[Origin]:
        """The live set of origins this client sends credentials to. Pass it as
        ``origins=`` to another client of the same plane to share it."""
        return self._origins

    def allow_endpoint(self, url: str, *, name: str) -> None:
        """Let this client send credentials to ``url``'s origin, a URL passed in
        code. Raises :class:`~donkey_kit.core.errors.ConfigError` unless it passes
        the config's https check (``https://``, a loopback host, or any host with
        ``DONKEY_ALLOW_HTTP=1`` in the environment); ``name`` labels the error."""
        require_secure_url(url, name=name)
        origin = origin_of(url)
        if origin is not None:
            self._origins.add(origin)

    def _guard(self, request: httpx.Request) -> bool:
        """Whether ``request`` may carry credentials. When not, strip any it has."""
        if origin_of(request.url) in self._origins:
            return True
        strip_credential_headers(request)
        return False


def _resolve_header_names(cfg: DonkeyConfig) -> tuple[str, str]:
    """The ``(correlation, call_id)`` request-header NAMES for this config (BG §1.1,
    #195): each is the config override if set, else the default from
    ``core/_verify`` (docs/verified-apis.md §3). Called ONCE per client at
    construction, not on every request."""
    correlation = cfg.correlation_header or _verify.CORRELATION_ID_HEADER
    call_id = cfg.call_id_header or _verify.CALL_ID_HEADER
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
    passed in, resolved by :func:`_request_correlation_id`: the bound run's ID,
    or one pinned per logical request, so re-setting it on each retry is
    idempotent (#803). The header NAME is resolved
    once by the client. The per-call ID is deliberately NOT set here — being
    random, it must be pinned once before the retry loop
    (:func:`_apply_call_id_header`), never re-rolled per send.

    With ``cfg.send_cost_headers`` enabled, ``attribution_headers`` already
    carries the CONFIG-LEVEL cost tags and any ``donkey.run(...)`` per-run
    overrides are applied on top here (read from the contextvar per send), so a
    run-scope dimension wins for its block (#196). Disabled (the default), no
    cost header is sent; the span attributes still carry every tag."""
    request.headers[correlation_header] = correlation_id
    # Stamp the resolved name so read-back (errors._sent_ids) honours a
    # header-name override without core/errors importing DonkeyConfig (#363).
    # Idempotent across retries — same name each send.
    request.extensions["donkey_correlation_header"] = correlation_header
    for name, value in attribution_headers(cfg).items():
        request.headers[name] = value
    run = current_cost_tags()
    if run is not None and cfg.send_cost_headers:
        for name, value in cost_headers(cfg, run).items():
            request.headers[name] = value
    # Per-request semantic-cache steering bound by ``donkey.cache(...)`` (#587).
    # Contextvar-read per send, exactly like the run-scope cost tags above, so a
    # steered block reaches framework-spawned asyncio tasks with no threading. The
    # VERIFIED lowercase ``x-cache-*`` names ride the same single injection seam as
    # every other governance header (BG §1.1); absent controls inject nothing.
    controls = current_cache_controls()
    if controls is not None:
        for name, value in controls.headers():
            request.headers[name] = value


def _request_correlation_id(request: httpx.Request) -> str:
    """The correlation ID for one send of ``request`` — never bound (#803).

    Inside a ``donkey.run()`` block this is the run's ID. Outside one,
    :func:`request_correlation_id` mints a fresh, unbound ID; it is pinned on
    the request's extensions on the first send, so the retries and 401 refresh
    of the same logical request reuse it rather than each minting another."""
    pinned = request.extensions.get("donkey_correlation_id")
    if pinned is not None and current_correlation_id() is None:
        return str(pinned)
    rid = request_correlation_id()
    request.extensions["donkey_correlation_id"] = rid
    return rid


def _apply_call_id_header(request: httpx.Request, call_id_header: str) -> None:
    """Pin a FRESH per-call ID on the request, ONCE, before the retry loop
    (BG §1.1, #195).

    The run/correlation id is stable per request (the bound run's, or one pinned
    on first send — :func:`_request_correlation_id`), so the per-send event hook
    can safely re-set it on every retry. The call id is
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
    # Floored at 0 by the parser: a negative Retry-After (e.g. "-1") must retry
    # immediately, never become a negative sleep — asyncio.sleep()/time.sleep()
    # raise ValueError on a negative argument, which would turn the retryable
    # status the loop exists to absorb into an unhandled exception (#286). The
    # HTTP-date form parses to None and falls through to backoff.
    retry_after = parse_retry_after(response.headers.get("retry-after"))
    if retry_after is not None:
        return min(retry_after, _BACKOFF_CAP_S)
    exp = min(_BACKOFF_BASE_S * (2.0**attempt), _BACKOFF_CAP_S)
    return exp * (0.5 + random.random() / 2.0)  # full-ish jitter


def _no_attempts(max_retries: object) -> ConfigError:
    """The error for a retry loop that sent nothing: ``max_retries`` is below 0.
    ``DonkeyConfig`` refuses that value, so only a config altered after
    construction gets here (#809)."""
    return ConfigError(
        f"max_retries is {max_retries!r}, so no request was sent; expected a whole "
        "number, 0 or more."
    )


def _gateway_unavailable(
    request: httpx.Request,
    exc: httpx.TransportError,
    *,
    correlation_header: str,
    call_id_header: str,
) -> GatewayUnavailable:
    """Wrap a transport-level httpx failure (DNS, refused connection, TLS error,
    timeout — no HTTP response) as the typed :class:`GatewayUnavailable` (#379,
    BG §1.2). ``httpx.TransportError`` is the precise base: it covers exactly the
    "never got a response" family and excludes response-bearing failures
    (``HTTPStatusError``), so a 4xx/5xx still flows to :func:`classify` unchanged.

    The origin that failed is put on the exception (the request URL is the
    truthful target, honouring any proxy/base-url override). The run/call ids the
    client already stamped on the request are carried so an availability failure
    quotes the same ids a response error would — even though the upstream
    provider's ``request_id`` (read from a response) is necessarily absent."""
    url = request.url
    host = url.host if url.port is None else f"{url.host}:{url.port}"
    return gateway_unavailable(
        base_url=f"{url.scheme}://{host}" if host else None,
        cause=exc,
        correlation_id=request.headers.get(correlation_header),
        call_id=request.headers.get(call_id_header),
    )


def _lifecycle_error(
    request: httpx.Request,
    exc: RuntimeError,
    *,
    client_closed: bool,
    correlation_header: str,
    call_id_header: str,
) -> ConfigError | None:
    """Type the two lifecycle ``RuntimeError``s a send can hit (#813), or return
    ``None`` to let any other ``RuntimeError`` through unchanged.

    * The client is closed: httpx refuses the send. Detected from the client's
      own state, not httpx's message wording.
    * The event loop is closed: a pooled connection belongs to a loop that has
      since closed, typically a second ``asyncio.run()`` on one ``Donkey``."""
    if client_closed:
        message = (
            "Cannot send: this Donkey's HTTP client is closed. It was closed by "
            "Donkey.aclose()/close() or by a framework that closed the client it "
            "was given."
        )
        remediation = (
            "Make the call on an open client: create a new Donkey, or keep the "
            "Donkey open (do not close it, or the client it hands a framework, "
            "until its last call)."
        )
    elif str(exc) == "Event loop is closed":
        message = (
            "Cannot send: the async client's connections belong to an event loop "
            "that has closed (for example, a second asyncio.run() on the same "
            "Donkey)."
        )
        remediation = (
            "Use the Donkey within one event loop: create it inside the "
            "asyncio.run() that uses it, or make every call from the same loop."
        )
    else:
        return None
    return ConfigError(
        message,
        remediation=remediation,
        correlation_id=request.headers.get(correlation_header),
        call_id=request.headers.get(call_id_header),
    )


# --- GenAI span extraction (#192, BG §1.6) ----------------------------------
# Shared by both transports (sync + async). Each takes a plain response/request,
# so the span-recording logic lives in one place and cannot drift between the
# two clients. The response header carrying the resolved upstream provider
# (docs/verified-apis.md §2 "Model routing" and §3 "Gateway identity on
# response") is the SOLE source of gen_ai.system; absent → the
# attribute is omitted, never guessed (verification discipline), because the proxy routes to
# several providers and defaulting one would misattribute the call. The header
# NAME is defined once in ``lastcall`` (which also parses it onto ``last_call``)
# and imported here as ``LLM_PROVIDER_HEADER`` so there is a single source (#309).
# Streaming (SSE) responses carry no usage on the envelope; it lives in a
# terminal event, captured by the span-closing stream wrapper (#193).
_STREAM_CONTENT_TYPE = "text/event-stream"


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
    the run id is the cross-call join key (BG §1.1, #195)."""
    try:
        decision, policy_type = _span_decision(response)
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


# A refusal on a stream request arrives unread: classify() would hit
# ``ResponseNotRead`` and the span would lose its decision, policy type and ERROR
# status (#805). The SDKs read an error body anyway, so the transport reads it
# first — bounded by _ERROR_BODY_CAP, and replayed untouched when it overflows.


def _is_read(response: httpx.Response) -> bool:
    try:
        response.content  # noqa: B018 — raises ResponseNotRead on an unread body
    except httpx.ResponseNotRead:
        return False
    return True


class _ReplayAsyncStream(httpx.AsyncByteStream):
    """An over-cap error body: the chunks already read, then the rest, unchanged."""

    def __init__(
        self, head: list[bytes], rest: AsyncIterator[bytes], inner: httpx.AsyncByteStream
    ) -> None:
        self._head, self._rest, self._inner = head, rest, inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._head:
            yield chunk
        async for chunk in self._rest:
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class _ReplaySyncStream(httpx.SyncByteStream):
    """Blocking twin of :class:`_ReplayAsyncStream`."""

    def __init__(
        self, head: list[bytes], rest: Iterator[bytes], inner: httpx.SyncByteStream
    ) -> None:
        self._head, self._rest, self._inner = head, rest, inner

    def __iter__(self) -> Iterator[bytes]:
        yield from self._head
        yield from self._rest

    def close(self) -> None:
        self._inner.close()


async def _aread_error_body(response: httpx.Response) -> None:
    """Read an unread non-2xx stream body of at most :data:`_ERROR_BODY_CAP`
    bytes, leaving the response as if it had been buffered. A larger body is put
    back as a replay stream, still unread."""
    inner = response.stream
    if not isinstance(inner, httpx.AsyncByteStream) or _is_read(response):
        return
    rest = inner.__aiter__()
    head: list[bytes] = []
    size = 0
    async for chunk in rest:
        head.append(chunk)
        size += len(chunk)
        if size > _ERROR_BODY_CAP:
            response.stream = _ReplayAsyncStream(head, rest, inner)
            return
    await inner.aclose()
    response.stream = httpx.ByteStream(b"".join(head))
    await response.aread()


def _read_error_body(response: httpx.Response) -> None:
    """Blocking twin of :func:`_aread_error_body`."""
    inner = response.stream
    if not isinstance(inner, httpx.SyncByteStream) or _is_read(response):
        return
    rest = iter(inner)
    head: list[bytes] = []
    size = 0
    for chunk in rest:
        head.append(chunk)
        size += len(chunk)
        if size > _ERROR_BODY_CAP:
            response.stream = _ReplaySyncStream(head, rest, inner)
            return
    inner.close()
    response.stream = httpx.ByteStream(b"".join(head))
    response.read()


class _SseUsageScanner:
    """Incrementally scans an SSE byte stream for the terminal ``usage`` event,
    keeping the latest observed token counts (#193, #307).

    Line-buffered, so it reconstructs ``data:`` lines across arbitrary chunk
    boundaries, and it only parses JSON for lines that mention ``usage`` — a
    cheap substring test skips the vast majority of delta events, so memory and
    CPU stay bounded no matter how long the completion is (buffering the whole
    body would defeat the point of streaming). ``counts`` holds the six usage
    fields (:func:`parse_usage`); a scanned value fills its field, so a later
    partial event never nulls a count already seen."""

    __slots__ = ("_buf", "counts")

    def __init__(self) -> None:
        self._buf: bytes = b""
        self.counts: dict[str, int | None] = parse_usage(None)

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
        # ``"usage`` matches both OpenAI's ``"usage"`` and Gemini's ``"usageMetadata"``.
        if b'"usage' not in line:
            return
        stripped = line.strip()
        if not stripped.startswith(b"data:"):
            return
        payload = stripped[len(b"data:") :].strip()
        try:
            obj = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return
        usage = usage_mapping(obj)
        if usage is None:
            return
        for field, value in parse_usage(usage).items():
            if value is not None:
                self.counts[field] = value


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
            counts = self._scanner.counts
            self._gspan.record(
                input_tokens=counts["input_tokens"],
                output_tokens=counts["output_tokens"],
                cached_tokens=counts["cached_tokens"],
                cache_write_tokens=counts["cache_write_tokens"],
                reasoning_tokens=counts["reasoning_tokens"],
            )
            # The record set in _on_response had no usage (the body was unread on
            # a stream); merge the terminal event's counts into it now (#307). The
            # stream is consumed in the same context that set the record, so this
            # updates the caller's own donkey.last_call.
            observe_usage(counts)
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


class _UrlPattern(Protocol):
    """The one method of httpx's (private) mount-key ``URLPattern`` we use."""

    def matches(self, other: httpx.URL) -> bool: ...


class _AsyncMountRouter(httpx.AsyncBaseTransport):
    """The client's base transport and its proxy mounts, folded into one
    transport (#801).

    httpx turns ``HTTP(S)_PROXY`` / ``ALL_PROXY`` (``trust_env``), ``proxy=`` and
    ``mounts=`` into ``client._mounts``, which it checks *before*
    ``client._transport``. A swap that replaces only ``_transport`` is bypassed
    by them, so ``simulate()`` and the conformance probe went online behind a
    corporate proxy. Folding the mounts in here leaves ``_mounts`` empty, so
    ``_transport`` is the only route: a swap takes every request, and a
    fixture's pass-through to this router still honours the proxy settings.
    Routing mirrors httpx's ``_transport_for_url``: the first matching pattern
    wins, and a ``None`` mount (a ``NO_PROXY`` entry) means the default."""

    def __init__(
        self,
        default: httpx.AsyncBaseTransport,
        mounts: Iterable[tuple[_UrlPattern, httpx.AsyncBaseTransport | None]],
    ) -> None:
        self._default = default
        self._routes = list(mounts)

    def _children(self) -> list[httpx.AsyncBaseTransport]:
        return [self._default, *(t for _, t in self._routes if t is not None)]

    def _route(self, url: httpx.URL) -> httpx.AsyncBaseTransport:
        for pattern, transport in self._routes:
            if pattern.matches(url):
                return self._default if transport is None else transport
        return self._default

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._route(request.url).handle_async_request(request)

    async def __aenter__(self) -> _AsyncMountRouter:
        for transport in self._children():
            await transport.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        for transport in self._children():
            await transport.__aexit__(exc_type, exc_value, traceback)

    async def aclose(self) -> None:
        for transport in self._children():
            await transport.aclose()


class _SyncMountRouter(httpx.BaseTransport):
    """Blocking twin of :class:`_AsyncMountRouter`."""

    def __init__(
        self,
        default: httpx.BaseTransport,
        mounts: Iterable[tuple[_UrlPattern, httpx.BaseTransport | None]],
    ) -> None:
        self._default = default
        self._routes = list(mounts)

    def _children(self) -> list[httpx.BaseTransport]:
        return [self._default, *(t for _, t in self._routes if t is not None)]

    def _route(self, url: httpx.URL) -> httpx.BaseTransport:
        for pattern, transport in self._routes:
            if pattern.matches(url):
                return self._default if transport is None else transport
        return self._default

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._route(request.url).handle_request(request)

    def __enter__(self) -> _SyncMountRouter:
        for transport in self._children():
            transport.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        for transport in self._children():
            transport.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        for transport in self._children():
            transport.close()


def _refuse_swap_past_mounts(mounts: Sized) -> None:
    """Fail closed (#801): with a mount in place, httpx would route some
    requests past a swapped-in fixture to a real connection. The constructors
    fold every mount into the base transport, so a mount here was added later."""
    if mounts:
        raise RuntimeError(
            "cannot swap the transport: this client has httpx mounts that would "
            "bypass it and open real connections. Pass proxies via mounts=/proxy= "
            "at construction so the client folds them into its transport."
        )


class DonkeyAsyncClient(_CheckedEndpoints, httpx.AsyncClient):
    """An ``httpx.AsyncClient`` that injects attribution/correlation/auth headers
    and applies the SDK's retry policy. Every adapter that accepts a custom HTTP
    client MUST be given one of these.

    A client serves exactly one credential plane (BG §1.1). The default is the
    data plane (the LLM proxy): ``auth`` is the data-plane credential, which is
    the model-wallet JWT provider in ``jwt`` mode, the bearer-token provider in
    ``bearer`` mode, and ``None`` in client-id mode.
    ``control_plane=True`` marks a client for Anypoint platform calls: ``auth``
    is then the connected-app provider, and the ``jwt``-mode wallet headers are
    never stamped on its requests.

    Credentials go only to the plane's checked endpoints (see
    :class:`_CheckedEndpoints`); ``origins`` shares that set with another client.
    Redirects are not followed unless a caller asks for it per request."""

    def __init__(
        self,
        cfg: DonkeyConfig,
        auth: AuthProvider | None,
        *,
        budget: Budget | None = None,
        control_plane: bool = False,
        origins: set[Origin] | None = None,
        **kw: object,
    ) -> None:
        self._cfg = cfg
        self._control_plane = control_plane
        self._init_origins(cfg.control_plane_url if control_plane else cfg.llm_proxy_url, origins)
        # The two correlation request-header NAMES, resolved once (config override
        # → UNVERIFIED placeholder). The one-time verification-discipline warning
        # for an un-overridden
        # name fires here, at construction, not per request (BG §1.1, #195).
        self._correlation_header, self._call_id_header = _resolve_header_names(cfg)
        # NB: httpx.AsyncClient uses ``self._auth`` internally, so we must NOT
        # store our token provider there — super().__init__() would clobber it.
        self._token_provider = auth
        # Optional in-band budget collaborator (BG §1.3, #185). When attached, the
        # response hook feeds it; when None the hook stays a byte-identical no-op,
        # so the control-plane token-fetch client tracks no budget.
        self._budget = budget
        self._view: DonkeyAsyncClientView | None = None
        super().__init__(
            timeout=cfg.timeout_s,
            event_hooks={"request": [self._inject_headers]},
            **kw,  # type: ignore[arg-type]
        )
        # Fold env/``proxy=``/``mounts=`` mounts into the base transport so the
        # swap seam below covers every route (#801).
        if self._mounts:
            self._transport = _AsyncMountRouter(self._transport, self._mounts.items())
            self._mounts = {}

    def view(self) -> DonkeyAsyncClientView:
        """The non-owning view of this client, the one to hand a framework (#733).
        It sends through this client, and closing it leaves this client open."""
        if self._view is None:
            self._view = DonkeyAsyncClientView(self)
        return self._view

    @property
    def token_provider(self) -> AuthProvider | None:
        """The attached :class:`AuthProvider`, if any. Read by ``LLMClient`` to
        tell an attached JWT provider from a missing one in ``jwt`` auth mode
        (#509) — the provider lives on the transport, not on ``DonkeyConfig``."""
        return self._token_provider

    async def _inject_headers(self, request: httpx.Request) -> None:
        _apply_base_headers(
            self._cfg,
            request,
            _request_correlation_id(request),
            correlation_header=self._correlation_header,
        )
        if not self._guard(request):
            return
        # jwt auth mode (model-wallet ingress, #509/#372): stamp the durable
        # wallet-selector ``X-Client-Id`` on every data-plane send (not just the
        # default_headers snapshot), so adapters routed through this shared client
        # carry it too — and never on a control-plane send. The name is VERIFIED
        # (docs/verified-apis.md §2/§3).
        data_plane_token = (
            self._cfg.llm_proxy_auth in TOKEN_AUTH_MODES and not self._control_plane
        )
        wallet = self._cfg.llm_proxy_auth == "jwt" and not self._control_plane
        if wallet and self._cfg.llm_proxy_wallet_client_id:
            request.headers.setdefault(
                _verify.LLM_PROXY_WALLET_CLIENT_ID_HEADER,
                self._cfg.llm_proxy_wallet_client_id,
            )
        if self._token_provider is not None:
            token = await self._token_provider.token()
            # Three token-bearing ingresses ride here, each on its own client and
            # all as ``Authorization: Bearer`` (VERIFIED): the control-plane OAuth2
            # client_credentials token (docs/verified-apis.md §12.1), the
            # data-plane model-wallet JWT in jwt mode (docs/verified-apis.md §2/§3,
            # #372), and the data-plane token in bearer mode, which is the same
            # JWT Validation bearer header with no wallet selector (#836). The
            # header name/scheme come from the verified wallet constants so there
            # is one source; the other two use the identical shape.
            header = _verify.LLM_PROXY_WALLET_JWT_HEADER
            value = f"{_verify.LLM_PROXY_WALLET_JWT_SCHEME} {token}"
            if data_plane_token:
                # The OpenAI SDK pre-sets ``Authorization: Bearer <api_key>`` from
                # its mandatory key slot; the proxy READS this header as the
                # token, so we must OVERRIDE that sentinel with the fresh per-send
                # token. ``setdefault`` would yield to the sentinel and 401.
                request.headers[header] = value
            else:
                # Control plane: the OpenAI SDK is not in this path, so yield to
                # any call-site Authorization rather than clobber it.
                request.headers.setdefault(header, value)

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
        headers (#185) and records the gateway identity of a governed model call
        into ``donkey.last_call`` (#362); both are no-ops when not applicable. A
        subclass that overrides this hook must call ``super()._on_response(...)``
        to keep budget and last-call tracking."""
        if self._budget is not None:
            self._budget.observe(response)
        # Only a model call feeds ``last_call`` — a token fetch or a registry
        # GET shares this transport but is not "the last call" a developer means
        # (#362). ``_request_model`` is the same signal ``send()`` uses to decide
        # whether to open a GenAI span, so the two stay in step. The requested
        # model is threaded through so ``last_call`` can report a substitution
        # (requested≠served) against what the caller actually asked for (#309).
        model = _request_model(request)
        if model is not None:
            observe_last_call(response, requested_model=model)

    async def _on_refusal(self, violation: object) -> None:
        """Refusal seam for Phase 2 reaction handlers. Defined here so the
        attachment point exists; there is no caller until ``classify()`` (#181)
        produces a typed violation. ``violation`` is typed ``object`` until then."""

    def _swap_transport(self, transport: httpx.AsyncBaseTransport) -> None:
        """Replace the underlying transport on a live client. httpx resolves the
        transport per-send from ``self._mounts`` and then ``self._transport``;
        the constructor folds every mount (including ``HTTP(S)_PROXY`` /
        ``ALL_PROXY`` proxies from the environment) into ``self._transport``, so
        the next request uses ``transport`` with no reconstruction and no
        route around it (#801). This is the seam ``simulate()`` (#190) and the
        conformance harness (#191) swap a fixture into. Raises ``RuntimeError``
        rather than swap while a mount could bypass the fixture."""
        _refuse_swap_past_mounts(self._mounts)
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
        # retries and the 401 refresh (BG §1.1, #195). The run correlation id is set
        # per-send by the event hook (stable per request, so idempotent, #803).
        _apply_call_id_header(request, self._call_id_header)
        await self._on_request(request)
        attempts = self._cfg.max_retries + 1
        refreshed_once = False
        last_response: httpx.Response | None = None

        attempt = 0
        while attempt < attempts:
            # A transport-level failure (DNS, refused, TLS, timeout) never yields
            # a response, so it is not retried here (retries key off a status
            # code) and is not routed through ``_on_refusal`` — it is not a
            # governed refusal (#379). It is re-raised as the typed
            # ``GatewayUnavailable`` so the caller can distinguish a lost gateway
            # from any other fault; the span still closes via ``send()``'s
            # context manager / detached-span guard, exactly as for a raw
            # transport error today (#179/#192).
            try:
                response = await super().send(request, **kwargs)  # type: ignore[arg-type]
            except httpx.TransportError as exc:
                raise _gateway_unavailable(
                    request,
                    exc,
                    correlation_header=self._correlation_header,
                    call_id_header=self._call_id_header,
                ) from exc
            except RuntimeError as exc:
                typed = _lifecycle_error(
                    request,
                    exc,
                    client_closed=self.is_closed,
                    correlation_header=self._correlation_header,
                    call_id_header=self._call_id_header,
                )
                if typed is None:
                    raise
                raise typed from exc
            last_response = response

            provider = self._token_provider
            can_refresh = provider is not None and not refreshed_once
            if response.status_code == 401 and can_refresh:
                assert provider is not None  # narrowed by can_refresh
                refreshed_once = True
                await response.aclose()
                await provider.invalidate()
                # A 401 refresh is an auth re-send, not a rate-limit backoff, so it
                # does NOT consume the retry budget (BG §1.1: "retry exactly once on
                # 401"): re-send once with the fresh token regardless of `attempt`,
                # so the retry still happens on the final attempt / max_retries=0.
                # Event hooks re-run on send() → fresh token.
                continue

            # A response the gateway already failed over (routing fallback) is NOT
            # retried, even on a retryable status: the gateway's Enhanced
            # Resilience routing is the first recovery layer, and an SDK retry on
            # top multiplies latency against an outage the gateway is already
            # handling (docs/verified-apis.md §3, #309, #183). ``is_fallback`` is
            # definitive-True-only,
            # so a non-proxy 5xx with no routing header still retries as before.
            if (
                response.status_code in _RETRYABLE_STATUS
                and not is_fallback(response)
                and attempt < attempts - 1
            ):
                delay = _retry_delay(attempt, response)
                await response.aclose()
                await asyncio.sleep(delay)
                attempt += 1
                continue

            return await self._finish(request, response, gspan, streaming=streaming)

        if last_response is None:
            raise _no_attempts(self._cfg.max_retries)
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
        usage and — for a refusal — ERROR status). A non-2xx stream body is read
        first (bounded, #805) so the refusal is classified exactly as a buffered
        one; a transport failure mid-read surfaces as :class:`GatewayUnavailable`,
        as it would have on the buffered path's own body read."""
        if streaming and response.status_code // 100 != 2:
            try:
                await _aread_error_body(response)
            except httpx.TransportError as exc:
                await response.aclose()
                raise _gateway_unavailable(
                    request,
                    exc,
                    correlation_header=self._correlation_header,
                    call_id_header=self._call_id_header,
                ) from exc
        await self._on_response(request, response)
        _record_response(
            gspan,
            request,
            response,
            self._budget,
            correlation_header=self._correlation_header,
            cost_tags=effective_cost_tags(self._cfg),
        )
        # After telemetry (so the substituted call is still on the span), a
        # caller who opted into ``on_model_substitution="raise"`` gets a hard
        # error instead of the response (docs/verified-apis.md §3, #309). Raising
        # here propagates out
        # through the span context manager (buffered) or the detached-span guard
        # in ``send()`` (streaming), so the span still closes. The response is
        # closed first so an aborted stream leaks no connection.
        substitution = _substitution_error(self._cfg, request, response)
        if substitution is not None:
            await response.aclose()
            raise substitution
        if streaming:
            if _is_streaming_success(response):
                # An async client's response.stream is an AsyncByteStream; httpx
                # types the attribute as the sync|async union, hence the narrowing.
                response.stream = _SpanClosingAsyncStream(response.stream, gspan)  # type: ignore[arg-type]
            else:
                gspan.end()
        return response


class DonkeyClient(_CheckedEndpoints, httpx.Client):
    """The blocking twin of :class:`DonkeyAsyncClient`, for ``donkey.llm.client(
    sync=True)``.

    It injects the same correlation/attribution headers and applies the same
    retry policy, so a synchronous caller is governed identically to an async
    one. Without it, a sync caller would fall back to whatever bare client the
    framework builds for itself and quietly lose both.

    It takes **no** :class:`AuthProvider`: that protocol is async-only
    (``async def token()``), and there is no correct way to await it from here.
    That costs nothing on the LLM data plane, which authenticates with the
    ``client_id``/``client_secret`` header pair (docs/verified-apis.md §2/§3)
    rather than
    a fetched token. It does mean the control-plane surfaces — ``registry`` and
    ``tools`` — stay async-only; see BG §1.1 for why the two credentials are
    deliberately not conflated.

    In the token auth modes (``llm_proxy_auth='jwt'`` or ``'bearer'``) the
    credential is exactly such a fetched token, so every request raises
    :func:`sync_token_auth_error` instead of going out unauthenticated (#509,
    #736, #836). The check sits in
    :meth:`build_request`, which the OpenAI SDK calls outside the ``try`` that
    turns transport errors into ``APIConnectionError``, so a framework's sync
    call (``ChatOpenAI.invoke()``) raises the ``ConfigError`` itself.

    Like its async twin it sends credentials only to the checked endpoints; pass
    the async client's :attr:`checked_origins` as ``origins`` to share them.
    """

    def __init__(
        self,
        cfg: DonkeyConfig,
        *,
        budget: Budget | None = None,
        origins: set[Origin] | None = None,
        **kw: object,
    ) -> None:
        self._cfg = cfg
        self._init_origins(cfg.llm_proxy_url, origins)
        # Resolved once; see DonkeyAsyncClient.__init__ (BG §1.1, #195).
        self._correlation_header, self._call_id_header = _resolve_header_names(cfg)
        self._budget = budget  # see DonkeyAsyncClient.__init__ (BG §1.3, #185)
        self._view: DonkeyClientView | None = None
        super().__init__(
            timeout=cfg.timeout_s,
            event_hooks={"request": [self._inject_headers]},
            **kw,  # type: ignore[arg-type]
        )
        if self._mounts:  # see DonkeyAsyncClient.__init__ (#801)
            self._transport = _SyncMountRouter(self._transport, self._mounts.items())
            self._mounts = {}

    def build_request(self, *args: Any, **kwargs: Any) -> httpx.Request:
        if self._cfg.llm_proxy_auth in TOKEN_AUTH_MODES:
            raise sync_token_auth_error(self._cfg.llm_proxy_auth)
        return super().build_request(*args, **kwargs)

    def view(self) -> DonkeyClientView:
        """The non-owning view of this client (see :meth:`DonkeyAsyncClient.view`)."""
        if self._view is None:
            self._view = DonkeyClientView(self)
        return self._view

    def _inject_headers(self, request: httpx.Request) -> None:
        _apply_base_headers(
            self._cfg,
            request,
            _request_correlation_id(request),
            correlation_header=self._correlation_header,
        )
        self._guard(request)

    # --- lifecycle hooks (BG §1.1) ------------------------------------------
    # Synchronous twins of the async seams, kept in lockstep so a blocking caller
    # is governed identically. Defaults are no-ops; internal, not public API.

    def _on_request(self, request: httpx.Request) -> None:
        """Called once, before the retry loop (see :meth:`DonkeyAsyncClient._on_request`)."""

    def _on_response(self, request: httpx.Request, response: httpx.Response) -> None:
        """Called once with the final response returned to the caller. Feeds the
        attached :class:`Budget` (#185) and records a governed model call's
        gateway identity into ``donkey.last_call`` (#362); both no-ops when not
        applicable. A subclass that overrides this must call
        ``super()._on_response(...)``."""
        if self._budget is not None:
            self._budget.observe(response)
        # Model calls only — see the async twin (#362/#309).
        model = _request_model(request)
        if model is not None:
            observe_last_call(response, requested_model=model)

    def _on_refusal(self, violation: object) -> None:
        """Refusal seam for Phase 2; no caller until ``classify()`` (#181)."""

    def _swap_transport(self, transport: httpx.BaseTransport) -> None:
        """Replace the underlying transport on a live client; the next request
        uses it (see :meth:`DonkeyAsyncClient._swap_transport`)."""
        _refuse_swap_past_mounts(self._mounts)
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
            # Transport-level failure → typed ``GatewayUnavailable``, identical to
            # the async twin (#379): not retried, not a refusal, span closed by
            # ``send()``'s span lifecycle.
            try:
                response = super().send(request, **kwargs)  # type: ignore[arg-type]
            except httpx.TransportError as exc:
                raise _gateway_unavailable(
                    request,
                    exc,
                    correlation_header=self._correlation_header,
                    call_id_header=self._call_id_header,
                ) from exc
            except RuntimeError as exc:  # a closed client; see the async twin (#813)
                typed = _lifecycle_error(
                    request,
                    exc,
                    client_closed=self.is_closed,
                    correlation_header=self._correlation_header,
                    call_id_header=self._call_id_header,
                )
                if typed is None:
                    raise
                raise typed from exc
            last_response = response

            # No 401-refresh branch: with no token provider there is nothing to
            # refresh, so a 401 here is a real credential failure and terminal.
            # A routing-fallback response is not double-retried — see the async
            # twin (docs/verified-apis.md §3, #309, #183).
            if (
                response.status_code in _RETRYABLE_STATUS
                and not is_fallback(response)
                and attempt < attempts - 1
            ):
                delay = _retry_delay(attempt, response)
                response.close()
                time.sleep(delay)
                continue

            return self._finish(request, response, gspan, streaming=streaming)

        if last_response is None:
            raise _no_attempts(self._cfg.max_retries)
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
        (see :meth:`DonkeyAsyncClient._finish`, including the bounded read of a
        non-2xx stream body, #805)."""
        if streaming and response.status_code // 100 != 2:
            try:
                _read_error_body(response)
            except httpx.TransportError as exc:
                response.close()
                raise _gateway_unavailable(
                    request,
                    exc,
                    correlation_header=self._correlation_header,
                    call_id_header=self._call_id_header,
                ) from exc
        self._on_response(request, response)
        _record_response(
            gspan,
            request,
            response,
            self._budget,
            correlation_header=self._correlation_header,
            cost_tags=effective_cost_tags(self._cfg),
        )
        # After telemetry, raise for an opted-in substitution — see the async
        # twin (docs/verified-apis.md §3, #309).
        substitution = _substitution_error(self._cfg, request, response)
        if substitution is not None:
            response.close()
            raise substitution
        if streaming:
            if _is_streaming_success(response):
                # A sync client's response.stream is a SyncByteStream; httpx types
                # the attribute as the sync|async union, hence the narrowing.
                response.stream = _SpanClosingSyncStream(response.stream, gspan)  # type: ignore[arg-type]
            else:
                gspan.end()
        return response


# --- non-owning views (#733) --------------------------------------------------
# Every adapter and ``donkey.llm`` share one client per plane, but several
# frameworks own the lifecycle of the client they are given: Strands runs
# ``async with AsyncOpenAI(**client_args)`` per request, and ``async with
# donkey.openai()`` closes its ``http_client`` on exit. Handing them the shared
# client itself lets one framework close it for the whole ``Donkey``. A view sends
# through the shared client, so every hook, retry, span, ``simulate()`` swap and
# ``donkey.last_call`` still applies, but closing it never closes the pool; only
# ``Donkey.aclose()``/``close()`` does.


class _NoTransport(httpx.AsyncBaseTransport, httpx.BaseTransport):
    """A view's own transport. Never used, because a view's ``send()`` delegates;
    passing it keeps httpx from building a connection pool for the view."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise RuntimeError("a DonkeyClientView sends through its shared client")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise RuntimeError("a DonkeyAsyncClientView sends through its shared client")


class DonkeyAsyncClientView(httpx.AsyncClient):
    """A non-owning ``httpx.AsyncClient`` over a shared :class:`DonkeyAsyncClient`.

    ``send()`` hands each request to the shared client. ``aclose()`` and leaving
    ``async with`` do nothing, and :attr:`is_closed` reports the shared client's
    state. Event hooks added to the view run for requests sent through it, around
    the shared client's own. Get one from :meth:`DonkeyAsyncClient.view`."""

    def __init__(self, shared: DonkeyAsyncClient) -> None:
        self._shared = shared
        super().__init__(
            timeout=shared.timeout,
            follow_redirects=shared.follow_redirects,
            trust_env=False,
            transport=_NoTransport(),
        )

    @property
    def is_closed(self) -> bool:
        return self._shared.is_closed

    async def send(self, request: httpx.Request, **kwargs: object) -> httpx.Response:
        for hook in self.event_hooks["request"]:
            await hook(request)
        response = await self._shared.send(request, **kwargs)
        for hook in self.event_hooks["response"]:
            await hook(response)
        return response

    async def aclose(self) -> None:
        """Leave the shared client open; ``Donkey.aclose()`` owns it."""

    async def __aenter__(self) -> DonkeyAsyncClientView:
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Leave the shared client open (see :meth:`aclose`)."""


class DonkeyClientView(httpx.Client):
    """The blocking twin of :class:`DonkeyAsyncClientView`, over a shared
    :class:`DonkeyClient`. Get one from :meth:`DonkeyClient.view`."""

    def __init__(self, shared: DonkeyClient) -> None:
        self._shared = shared
        super().__init__(
            timeout=shared.timeout,
            follow_redirects=shared.follow_redirects,
            trust_env=False,
            transport=_NoTransport(),
        )

    @property
    def is_closed(self) -> bool:
        return self._shared.is_closed

    def build_request(self, *args: Any, **kwargs: Any) -> httpx.Request:
        # The shared client refuses here in a token mode; a view must refuse too.
        mode = self._shared._cfg.llm_proxy_auth
        if mode in TOKEN_AUTH_MODES:
            raise sync_token_auth_error(mode)
        return super().build_request(*args, **kwargs)

    def send(self, request: httpx.Request, **kwargs: object) -> httpx.Response:
        for hook in self.event_hooks["request"]:
            hook(request)
        response = self._shared.send(request, **kwargs)
        for hook in self.event_hooks["response"]:
            hook(response)
        return response

    def close(self) -> None:
        """Leave the shared client open; ``Donkey.close()`` owns it."""

    def __enter__(self) -> DonkeyClientView:
        return self

    def __exit__(self, *exc: object) -> None:
        """Leave the shared client open (see :meth:`close`)."""


def build_http_client(
    cfg: DonkeyConfig,
    auth: AuthProvider | None,
    *,
    budget: Budget | None = None,
    control_plane: bool = False,
) -> DonkeyAsyncClient:
    """Factory for a shared client, one per credential plane (BG §1.1). Pass
    ``budget`` to track the in-band token window on every response (BG §1.3,
    #185); omit it on the control plane, which observes no budget. Pass
    ``control_plane=True`` for a client that calls the Anypoint platform (see
    :class:`DonkeyAsyncClient`)."""
    return DonkeyAsyncClient(cfg, auth, budget=budget, control_plane=control_plane)


def sync_token_auth_error(mode: LlmProxyAuth) -> ConfigError:
    """The error for a blocking call in a token auth mode (jwt / model-wallet or
    bearer), shared by ``donkey.llm.client(sync=True)`` and every sync call
    through :class:`DonkeyClient` (#509, #736, #836), so both surfaces fail the
    same way."""
    label = "JWT / model-wallet" if mode == "jwt" else "Bearer-token"
    token = "JWT" if mode == "jwt" else "bearer token"
    return ConfigError(
        f"{label} auth mode (llm_proxy_auth={mode!r}) is async-only: "
        f"the credential is a rotating {token} fetched from an async AuthProvider, "
        "and the blocking client cannot await it. Use the async surface — "
        "`donkey.llm.client()` / `donkey.openai()` without sync=True, or a "
        "framework's async call (`ainvoke()` / `astream()`, not `invoke()`) — or "
        "switch to client-id auth for a synchronous caller."
    )



def build_sync_http_client(
    cfg: DonkeyConfig,
    *,
    budget: Budget | None = None,
    origins: set[Origin] | None = None,
) -> DonkeyClient:
    """Factory for the shared blocking client (BG §1.1). See :class:`DonkeyClient`
    for why it takes no :class:`AuthProvider`. Pass ``budget`` to share one budget
    object with the async client (BG §1.3, #185), and ``origins`` to share its
    checked endpoints."""
    return DonkeyClient(cfg, budget=budget, origins=origins)
