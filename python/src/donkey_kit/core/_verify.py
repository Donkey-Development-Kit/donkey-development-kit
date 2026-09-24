"""Centralized home for every value that the verification discipline says must
be verified against a real Anypoint sandbox before it can be trusted.

Working instruction #2: *never invent an endpoint, header name, or class name.*

Nothing in this module is a verified fact. Each value is either:

  * a ``PLACEHOLDER`` — a documented best-guess that is emitted with a loud,
    one-time :class:`UnverifiedValueWarning` whenever it is read, and is fully
    overridable via config / env so a customer can point it at the real value
    without waiting for us; or
  * absent, in which case the calling code raises
    ``NotImplementedError("blocked on verification: …")``.

When a value is confirmed against a sandbox, flip its row in
``docs/verified-apis.md`` to ``VERIFIED`` and set ``verified=True`` here so the
warning stops firing.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass


class UnverifiedValueWarning(UserWarning):
    """Emitted the first time an unverified placeholder constant is read."""


_warned: set[str] = set()


@dataclass(frozen=True)
class Unverified:
    """A placeholder value that is not yet confirmed against a real sandbox.

    Read it with :meth:`get`, which warns once. Treat the returned value as a
    default that the user can and should override.
    """

    key: str
    placeholder: str
    doc_ref: str
    verified: bool = False

    def get(self) -> str:
        if not self.verified and self.key not in _warned:
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
    MCP Bridge provisioning endpoint, verification discipline / provisioning-as-code).
    """

    return NotImplementedError(f"blocked on verification: {what}")


# --- Attribution headers (docs/verified-apis.md §3, the single most important unknown) ---
# These names are GUESSES. The gateway may read entirely different header names.
ATTRIBUTION_APP_HEADER = Unverified(
    key="attribution.application_header",
    placeholder="X-Anypoint-Client-Application",
    doc_ref="docs/verified-apis.md §3",
)
ATTRIBUTION_BUSINESS_GROUP_HEADER = Unverified(
    key="attribution.business_group_header",
    placeholder="X-Anypoint-Business-Group",
    doc_ref="docs/verified-apis.md §3",
)

# --- Cost-attribution tag headers (docs/verified-apis.md §3, #196) ---
# The fixed cost dimensions (team / project / env / enduser.id) are emitted as
# request headers AND as ``donkey.cost.*`` span attributes (the SDK controls the
# span end to end, #196 AC #4).
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
# attribute. The SDK still emits these ``X-Anypoint-Cost-*`` names as a
# forward-looking, overridable (``cost_*_header``) convention — harmless because
# nothing reads them — so each is ``verified=True``: there is nothing left to
# discover, and leaving the warning on would lie by silence.
COST_TEAM_HEADER = Unverified(
    key="cost.team_header",
    placeholder="X-Anypoint-Cost-Team",
    doc_ref="docs/verified-apis.md §3",
    verified=True,
)
COST_PROJECT_HEADER = Unverified(
    key="cost.project_header",
    placeholder="X-Anypoint-Cost-Project",
    doc_ref="docs/verified-apis.md §3",
    verified=True,
)
COST_ENV_HEADER = Unverified(
    key="cost.env_header",
    placeholder="X-Anypoint-Cost-Env",
    doc_ref="docs/verified-apis.md §3",
    verified=True,
)
COST_ENDUSER_HEADER = Unverified(
    key="cost.enduser_header",
    placeholder="X-Anypoint-Cost-Enduser-Id",
    doc_ref="docs/verified-apis.md §3",
    verified=True,
)

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
# / ``.call_id_header``) and both are ``verified=True``: the correlation name is
# confirmed read by the gateway, and the call-id name is confirmed to be a
# client-side construct the gateway ignores — neither is an open worklist item,
# and leaving the warning on would lie by silence. See docs/verified-apis.md §3.
CORRELATION_ID_HEADER = Unverified(
    key="correlation.request_header",
    placeholder="X-Correlation-Id",
    doc_ref="docs/verified-apis.md §3",
    verified=True,
)
CALL_ID_HEADER = Unverified(
    key="correlation.call_id_header",
    placeholder="X-Donkey-Request-Id",
    doc_ref="docs/verified-apis.md §3",
    verified=True,
)

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
# transport injects them lowercase as written. Overridable via config/env like
# the attribution headers above; ``verified=True`` because the names are confirmed
# live and there is nothing left to discover — leaving the warning on would lie by
# silence.
CACHE_SKIP_HEADER = Unverified(
    key="cache.skip_header",
    placeholder="x-cache-skip",
    doc_ref="docs/verified-apis.md §2",
    verified=True,
)
CACHE_NO_STORE_HEADER = Unverified(
    key="cache.no_store_header",
    placeholder="x-cache-no-store",
    doc_ref="docs/verified-apis.md §2",
    verified=True,
)
CACHE_TTL_HEADER = Unverified(
    key="cache.ttl_header",
    placeholder="x-cache-ttl",
    doc_ref="docs/verified-apis.md §2",
    verified=True,
)
CACHE_THRESHOLD_HEADER = Unverified(
    key="cache.threshold_header",
    placeholder="x-cache-threshold",
    doc_ref="docs/verified-apis.md §2",
    verified=True,
)
CACHE_PRINCIPAL_ID_HEADER = Unverified(
    key="cache.principal_id_header",
    placeholder="x-cache-principal-id",
    doc_ref="docs/verified-apis.md §2",
    verified=True,
)
# The two RESPONSE signal headers the gateway states its outcome on: the status
# (``hit`` / ``miss`` / ``bypass`` / ``no-store``, present on every cached route)
# and, on a ``hit`` only, the similarity score (four-dp string, e.g. ``0.9518``).
# Parsed onto ``donkey.last_call`` — plain confirmed names, the lastcall
# response-header style (not sent, so not overridable), living here beside the
# request names so the whole caching header contract has one home (#587).
SEMANTIC_CACHE_STATUS_HEADER = "x-semantic-cache-status"
SEMANTIC_CACHE_SCORE_HEADER = "x-semantic-cache-score"

# --- Control-plane token endpoint (docs/verified-apis.md §1) ----------------
# Path is appended to the region base URL. VERIFIED (docs/verified-apis.md §12.1) from
# static analysis of the shipping `mulesoft-anypoint-cli-agent-fabric-plugin` (+ `anypoint-cli-
# command`): OAuth2 client_credentials → `POST /accounts/api/v2/oauth2/token`.
OAUTH_TOKEN_PATH = Unverified(
    key="anypoint.oauth_token_path",
    placeholder="/accounts/api/v2/oauth2/token",
    doc_ref="docs/verified-apis.md §12.1",
    verified=True,
)

# --- LLM proxy consumer auth (docs/verified-apis.md §2/§3) — VERIFIED (LIVE
# 2026-08-28) ------------
# The directly-called ingress LLM proxy authenticates the caller with a
# `client_id` + `client_secret` REQUEST-header pair (client-id-enforcement
# 1.3.3), NOT a bearer token. This pair IS the per-agent attribution unit. These
# are confirmed header names, not placeholders — see docs/verified-apis.md §2/§3.
LLM_PROXY_CLIENT_ID_HEADER = "client_id"
LLM_PROXY_CLIENT_SECRET_HEADER = "client_secret"

# --- LLM proxy model-wallet ingress (docs/verified-apis.md §2/§3, #372) —
# VERIFIED (LIVE 2026-09-21) ------------
# The PARALLEL ingress model on a wallet-backed proxy: the caller is identified
# by an IdP-issued JWT + a client ID, with NO `client_secret`. The default
# DataWeave Headers Transformation + Client ID Enforcement policies are disabled;
# JWT Validation identifies the caller instead. These are confirmed names, not
# placeholders — captured live against `ddk-model-wallet` (instance 21186246,
# Sandbox), fixtures in tests/fixtures/anypoint/model_wallet/. The SDK does not
# yet emit this path (transport still sends only the CIE pair); wiring is #509.
#   * the wallet-selector REQUEST header (value = the wallet's generated clientId);
LLM_PROXY_WALLET_CLIENT_ID_HEADER = "X-Client-Id"
#   * the JWT rides as `Authorization: Bearer <JWT>` (jwtOrigin
#     httpBearerAuthenticationHeader); Bearer was an assumption in the doc read,
#     confirmed live;
LLM_PROXY_WALLET_JWT_HEADER = "Authorization"
LLM_PROXY_WALLET_JWT_SCHEME = "Bearer"
#   * the JWT claim the LLM Proxy Core Policy reads as the caller id, published by
#     JWT Validation and read via `#[authentication.properties.claims.client_id]`;
LLM_PROXY_WALLET_JWT_CLIENT_ID_CLAIM = "client_id"
#   * the response header the gateway echoes naming the matched wallet.
LLM_PROXY_WALLET_SELECTED_HEADER = "x-model-wallet-selected"

# --- Region host map (docs/verified-apis.md §1) ------------------------------
# UNVERIFIED — Hyperforce region hosts in particular need confirmation.
REGION_HOSTS: dict[str, str] = {
    "us": "https://anypoint.mulesoft.com",
    "eu": "https://eu1.anypoint.mulesoft.com",
    "ca": "https://ca1.anypoint.mulesoft.com",
    "jp": "https://jp1.anypoint.mulesoft.com",
}
REGION_HOSTS_VERIFIED = False
