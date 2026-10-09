"""Centralized home for every gateway or control-plane value the verification
discipline governs: endpoint paths, header names, and hosts.

Header-name strings are defined once, in ``core/_wire``; this module binds the
governed ones to their verification status (and wraps the unconfirmed ones in
:class:`Unverified`), so callers keep reading them from here.

Working instruction #2: *never invent an endpoint, header name, or class name.*

Each value here is one of:

  * a plain constant, confirmed against a real sandbox or the official
    shipping client; its ``docs/verified-apis.md`` row records how;
  * an :class:`Unverified` placeholder, a documented best guess that emits a
    loud, one-time :class:`UnverifiedValueWarning` when it is read and is fully
    overridable via config / env so a customer can point it at the real value
    without waiting for us; or
  * absent, in which case the calling code raises
    ``NotImplementedError("blocked on verification: …")`` via :func:`blocked`.

When a placeholder is confirmed against a sandbox, flip its row in
``docs/verified-apis.md`` to ``VERIFIED`` and replace it here with a plain
constant so the warning stops firing.

This is the one module under ``src/`` that may record verification status and
dates. Everywhere else, code describes behaviour and cites
``docs/verified-apis.md §N`` (enforced by ``scripts/check_verification_claims.py``).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from . import _wire


class UnverifiedValueWarning(UserWarning):
    """Emitted the first time an unverified placeholder constant is read."""


_warned: set[str] = set()


def _reset_for_tests() -> None:
    """Clear the one-time-warning de-dupe set (#750).

    Each :class:`Unverified` placeholder warns at most once per process, keyed
    by ``key`` in :data:`_warned`. Left alone, whichever test reads a given
    placeholder first "spends" that warning for the rest of the run, so a test
    asserting ``pytest.warns(UnverifiedValueWarning)`` passes or fails
    depending on execution order. Called from the autouse fixture in
    ``tests/conftest.py`` so every test starts from a clean slate; test code
    should never clear this by hand."""
    _warned.clear()


@dataclass(frozen=True)
class Unverified:
    """A placeholder value that is not yet confirmed against a real sandbox.

    Read it with :meth:`get`, which warns once. Treat the returned value as a
    default that the user can and should override.
    """

    key: str
    placeholder: str
    doc_ref: str

    def get(self) -> str:
        if self.key not in _warned:
            _warned.add(self.key)
            warnings.warn(
                f"Using UNVERIFIED placeholder for {self.key!r} "
                f"({self.placeholder!r}). This has NOT been confirmed against a "
                f"real Anypoint sandbox — see {self.doc_ref}. Override it via "
                f"config/env, or verify it and flip the row in "
                f"docs/verified-apis.md.",
                UnverifiedValueWarning,
                stacklevel=2,
            )
        return self.placeholder


def blocked(what: str) -> NotImplementedError:
    """Construct the standard verification-blocked error.

    Use for surfaces where we have no defensible placeholder at all (e.g. the
    Exchange publication API behind ``donkey publish``, BG §2.5).
    """

    return NotImplementedError(f"blocked on verification: {what}")


# --- Attribution headers (docs/verified-apis.md §3, the single most important unknown) ---
# These names are GUESSES. The gateway may read entirely different header names.
ATTRIBUTION_APP_HEADER = Unverified(
    key="attribution.application_header",
    placeholder=_wire.ATTRIBUTION_APP_HEADER_PLACEHOLDER,
    doc_ref="docs/verified-apis.md §3",
)
ATTRIBUTION_BUSINESS_GROUP_HEADER = Unverified(
    key="attribution.business_group_header",
    placeholder=_wire.ATTRIBUTION_BUSINESS_GROUP_HEADER_PLACEHOLDER,
    doc_ref="docs/verified-apis.md §3",
)

# --- Cost-attribution tag headers (docs/verified-apis.md §3, #196) ---
# The fixed cost dimensions (team / project / env / enduser.id) are emitted as
# ``donkey.cost.*`` span attributes (the SDK controls the span end to end, #196
# AC #4), and as request headers only when ``send_cost_headers`` is enabled.
#
# NEGATIVE result — VERIFIED (LIVE 2026-09-22, #522): the deployed Omni Gateway
# LLM proxy has NO inbound cost-tag ingestion. A live probe sending
# ``X-Anypoint-Cost-Team/Project/Env/Enduser-Id`` saw none of them echoed, and
# none of the names appears in ANY of the 10 platform-managed system policies
# applied to the proxy (checked via ``view_api_instance_policies`` on instance
# 21179672, DDK / Sandbox, and against the ``ddk-create-llm-proxy-*`` provisioning
# skills' system-policy set). Cost/usage is collected by the ``llm-proxy-core``
# system policy from token usage (the ``x-llm-proxy-*-tokens-cost-per-1m``
# response headers) and attributed by API instance + consuming client application
# — never by a client-sent header. So the gateway-side names are confirmed to be
# a NON-CONTRACT; the authoritative carrier is the ``donkey.cost.*`` span
# attribute. The SDK keeps these ``X-Anypoint-Cost-*`` names as a
# forward-looking, overridable (``cost_*_header``) convention, sent only when a
# caller opts in with ``send_cost_headers`` — by default nothing is sent to a
# gateway that reads nothing — so each is a plain constant: there is nothing
# left to discover, and a warning would lie by silence.
COST_TEAM_HEADER = _wire.COST_TEAM_HEADER
COST_PROJECT_HEADER = _wire.COST_PROJECT_HEADER
COST_ENV_HEADER = _wire.COST_ENV_HEADER
COST_ENDUSER_HEADER = _wire.COST_ENDUSER_HEADER

# --- Correlation / call-id request headers (BG §1.1, #195) ------------------
# VERIFIED (LIVE 2026-09-22, #522) against the deployed Omni Gateway proxy
# ``ddk-multi-route-fallback`` (API instance 21179672, DDK / Sandbox):
#   * X-Correlation-Id (request) — POSITIVE: the gateway READS the inbound value
#     and echoes it verbatim on the response ``x-correlation-id`` (probe sent
#     ``X-Correlation-Id: ddk522-corr``; the response echoed ``ddk522-corr`` on
#     both the 200 and the 400 paths). This is the client→gateway run/trace join
#     key the SDK sends, so the name is confirmed — not a guess. It matches the
#     already-verified response echo (docs/verified-apis.md §3, 2026-08-28).
#   * X-Donkey-Request-Id (request) — NEGATIVE: no gateway policy reads a per-call
#     id header (not echoed; absent from all 10 applied system policies). It is a
#     CLIENT-OWNED per-call id surfaced on ``DonkeyError.call_id``, stable across a
#     request's retries; the gateway does not need to consume it. The name is the
#     SDK's own convention.
# Both are overridable per-Donkey via config (``DonkeyConfig.correlation_header``
# / ``.call_id_header``) and both are plain constants: the correlation name is
# confirmed read by the gateway, and the call-id name is confirmed to be a
# client-side construct the gateway ignores — neither is an open worklist item,
# and a warning would lie by silence. See docs/verified-apis.md §3.
CORRELATION_ID_HEADER = _wire.CORRELATION_ID_HEADER
CALL_ID_HEADER = _wire.CALL_ID_HEADER

# --- Semantic caching (docs/verified-apis.md §2 "Semantic caching", #587/#588) —
# VERIFIED (LIVE 2026-09-24) ------------
# Captured against ``ddk-semantic-cache`` (API instance 21195392, DDK / Sandbox),
# fixtures in tests/fixtures/anypoint/semantic_cache/. The gateway's
# semantic-caching policy takes five REQUEST steering headers and reports its
# outcome on the RESPONSE. The SDK caches nothing and computes no embeddings — it
# STEERS and SURFACES the gateway's own cache (not the client-side semantic cache
# on the "Do not build" list).
#
# The five ``x-cache-*`` steering headers were sent LOWERCASE and honored, so the
# transport injects them lowercase as written. Plain constants because the names
# are confirmed live and there is nothing left to discover — a warning would lie
# by silence.
CACHE_SKIP_HEADER = _wire.CACHE_SKIP_HEADER
CACHE_NO_STORE_HEADER = _wire.CACHE_NO_STORE_HEADER
CACHE_TTL_HEADER = _wire.CACHE_TTL_HEADER
CACHE_THRESHOLD_HEADER = _wire.CACHE_THRESHOLD_HEADER
CACHE_PRINCIPAL_ID_HEADER = _wire.CACHE_PRINCIPAL_ID_HEADER
# The two RESPONSE signal headers the gateway states its outcome on: the status
# (``hit`` / ``miss`` / ``bypass`` / ``no-store``, present on every cached route)
# and, on a ``hit`` only, the similarity score (four-dp string, e.g. ``0.9518``).
# Parsed onto ``donkey.last_call`` — plain confirmed names, the lastcall
# response-header style (not sent, so not overridable), living here beside the
# request names so the whole caching header contract has one home (#587).
SEMANTIC_CACHE_STATUS_HEADER = _wire.SEMANTIC_CACHE_STATUS_HEADER
SEMANTIC_CACHE_SCORE_HEADER = _wire.SEMANTIC_CACHE_SCORE_HEADER

# --- Control-plane token endpoint (docs/verified-apis.md §1) ----------------
# Path is appended to the region base URL. VERIFIED (docs/verified-apis.md §12.1) from
# static analysis of the shipping `mulesoft-anypoint-cli-agent-fabric-plugin` (+ `anypoint-cli-
# command`): OAuth2 client_credentials → `POST /accounts/api/v2/oauth2/token`.
OAUTH_TOKEN_PATH = "/accounts/api/v2/oauth2/token"

# --- LLM proxy consumer auth (docs/verified-apis.md §2/§3) — VERIFIED (LIVE
# 2026-08-28) ------------
# The directly-called ingress LLM proxy authenticates the caller with a
# `client_id` + `client_secret` REQUEST-header pair (client-id-enforcement
# 1.3.3), NOT a bearer token. This pair IS the per-agent attribution unit. These
# are confirmed header names, not placeholders — see docs/verified-apis.md §2/§3.
LLM_PROXY_CLIENT_ID_HEADER = _wire.LLM_PROXY_CLIENT_ID_HEADER
LLM_PROXY_CLIENT_SECRET_HEADER = _wire.LLM_PROXY_CLIENT_SECRET_HEADER

# --- LLM proxy model-wallet ingress (docs/verified-apis.md §2/§3, #372) —
# VERIFIED (LIVE 2026-09-21) ------------
# The PARALLEL ingress model on a wallet-backed proxy: the caller is identified
# by an IdP-issued JWT + a client ID, with NO `client_secret`. The default
# DataWeave Headers Transformation + Client ID Enforcement policies are disabled;
# JWT Validation identifies the caller instead. These are confirmed names, not
# placeholders — captured live against `ddk-model-wallet` (instance 21186246,
# Sandbox), fixtures in tests/fixtures/anypoint/model_wallet/. The transport
# emits this path when ``llm_proxy_auth = "jwt"`` (#509).
#   * the wallet-selector REQUEST header (value = the wallet's generated clientId);
LLM_PROXY_WALLET_CLIENT_ID_HEADER = _wire.LLM_PROXY_WALLET_CLIENT_ID_HEADER
#   * the JWT rides as `Authorization: Bearer <JWT>` (jwtOrigin
#     httpBearerAuthenticationHeader); Bearer was an assumption in the doc read,
#     confirmed live;
LLM_PROXY_WALLET_JWT_HEADER = _wire.LLM_PROXY_WALLET_JWT_HEADER
LLM_PROXY_WALLET_JWT_SCHEME = _wire.LLM_PROXY_WALLET_JWT_SCHEME

# --- Region host map (docs/verified-apis.md §1) ------------------------------
# The US host is VERIFIED (CLI) 2026-08-28. The eu/ca/jp hosts are UNVERIFIED:
# each is an ``Unverified`` placeholder, so resolving that region with no
# ``base_url`` override warns once (``DonkeyConfig.control_plane_url``).
# ``REGION_HOSTS`` holds every host as a string, for host checks that must not warn.
UNVERIFIED_REGION_HOSTS: dict[str, Unverified] = {
    region: Unverified(
        key=f"anypoint.region_host.{region}",
        placeholder=f"https://{region}1.anypoint.mulesoft.com",
        doc_ref="docs/verified-apis.md §1",
    )
    for region in ("eu", "ca", "jp")
}
REGION_HOSTS: dict[str, str] = {
    "us": "https://anypoint.mulesoft.com",
    **{region: host.placeholder for region, host in UNVERIFIED_REGION_HOSTS.items()},
}
