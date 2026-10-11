"""Header policy for the governed clients (BG §1.1, docs/verified-apis.md §2/§3).

Everything both clients stamp on a request, and the rule for where credentials
may go: the run correlation id and per-call id, attribution, cost tags and
cache steering, the consumer-auth snapshot for ``default_headers`` frameworks,
the per-send token, and the checked endpoints that keep credentials off any
origin the plane was not configured for. Pure functions over a request and a
config; no I/O (#728).
"""

from __future__ import annotations

import httpx

from .. import _verify
from ..cachecontrol import current_cache_controls
from ..config import TOKEN_AUTH_MODES, DonkeyConfig
from ..correlation import (
    current_correlation_id,
    current_cost_tags,
    new_call_id,
    request_correlation_id,
)
from ..cost import CostTags
from ..endpoints import require_secure_url
from ..masking import SENSITIVE_NAMES, masked

__all__ = [
    "CALL_ID_HEADER",
    "CORRELATION_HEADER",
    "CREDENTIAL_HEADERS",
    "PROXY_API_KEY_SENTINEL",
    "Origin",
    "attribution_headers",
    "cost_headers",
    "effective_cost_tags",
    "origin_of",
    "proxy_api_key",
    "proxy_auth_headers",
    "strip_credential_headers",
]

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
    override = {field: getattr(cfg, attr) for field, attr, _default in _COST_HEADER_SOURCES}
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
    control_plane: bool,
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
    cost header is sent; the span attributes still carry every tag.

    On a ``control_plane`` client only the correlation ID is set: the
    attribution, cost-tag and ``x-cache-*`` headers are meant for the LLM proxy
    (docs/verified-apis.md §3) and no Anypoint platform API reads them
    (docs/verified-apis.md §12.2), so they are not sent there (#833)."""
    request.headers[correlation_header] = correlation_id
    # Stamp the resolved name so read-back (errors._sent_ids) honours a
    # header-name override without core/errors importing DonkeyConfig (#363).
    # Idempotent across retries — same name each send.
    request.extensions["donkey_correlation_header"] = correlation_header
    if control_plane:
        return
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


def _apply_auth(
    cfg: DonkeyConfig, request: httpx.Request, token: str | None, *, control_plane: bool
) -> None:
    """Stamp the per-send credential on a request already cleared by the
    checked-endpoint guard. Shared by both clients so they cannot drift (#728).

    In ``jwt`` mode (model-wallet ingress, #509/#372) a data-plane send carries
    the durable wallet-selector ``X-Client-Id``, on every send rather than only
    in the ``default_headers`` snapshot, so adapters routed through the shared
    client carry it too; a control-plane send never does. The name is VERIFIED
    (docs/verified-apis.md §2/§3).

    ``token`` is the attached provider's token, or ``None`` when there is none
    (client-id mode, and always on the blocking client, whose token modes are
    refused before a request is built). Three token-bearing ingresses ride here,
    each on its own client and all as ``Authorization: Bearer`` (VERIFIED): the
    control-plane OAuth2 client_credentials token (docs/verified-apis.md §12.1),
    the data-plane model-wallet JWT in jwt mode (docs/verified-apis.md §2/§3,
    #372), and the data-plane token in bearer mode, the same JWT Validation
    bearer header with no wallet selector (#836)."""
    if cfg.llm_proxy_auth == "jwt" and not control_plane and cfg.llm_proxy_wallet_client_id:
        request.headers.setdefault(
            _verify.LLM_PROXY_WALLET_CLIENT_ID_HEADER, cfg.llm_proxy_wallet_client_id
        )
    if token is None:
        return
    header = _verify.LLM_PROXY_WALLET_JWT_HEADER
    value = f"{_verify.LLM_PROXY_WALLET_JWT_SCHEME} {token}"
    if cfg.llm_proxy_auth in TOKEN_AUTH_MODES and not control_plane:
        # The OpenAI SDK pre-sets ``Authorization: Bearer <api_key>`` from its
        # mandatory key slot; the proxy READS this header as the token, so the
        # sentinel is OVERRIDDEN with the fresh per-send token. ``setdefault``
        # would yield to the sentinel and 401.
        request.headers[header] = value
    else:
        # Control plane: the OpenAI SDK is not in this path, so yield to any
        # call-site Authorization rather than clobber it.
        request.headers.setdefault(header, value)
