"""Gateway wire names: every HTTP header name the SDK sends to, or reads from,
the gateway, defined once.

Code under ``src/`` imports a gateway header name from here (or through
``core/_verify``, which wraps some of them) and never spells it out as a string
literal; ``tests/unit/test_wire_names.py`` enforces that. Whether a name is
confirmed, and how, is recorded only in ``core/_verify.py`` and
``docs/verified-apis.md``; this module holds the strings and nothing else.

A ``*_PLACEHOLDER`` name is UNVERIFIED: read it through its
``_verify.Unverified`` wrapper, which warns once, never from here directly.

Response header names are lowercase, as the gateway sends them; httpx matches
header names case-insensitively either way.
"""

from __future__ import annotations

# --- Request headers ---------------------------------------------------------

# Attribution (docs/verified-apis.md §3). Read through
# ``_verify.ATTRIBUTION_APP_HEADER`` / ``_verify.ATTRIBUTION_BUSINESS_GROUP_HEADER``.
ATTRIBUTION_APP_HEADER_PLACEHOLDER = "X-Anypoint-Client-Application"
ATTRIBUTION_BUSINESS_GROUP_HEADER_PLACEHOLDER = "X-Anypoint-Business-Group"

# Cost-attribution tags, sent only with ``send_cost_headers`` (docs/verified-apis.md §3).
COST_TEAM_HEADER = "X-Anypoint-Cost-Team"
COST_PROJECT_HEADER = "X-Anypoint-Cost-Project"
COST_ENV_HEADER = "X-Anypoint-Cost-Env"
COST_ENDUSER_HEADER = "X-Anypoint-Cost-Enduser-Id"

# The run correlation id (the gateway echoes it back on the response under the
# same name) and the SDK's own per-call id (docs/verified-apis.md §3).
CORRELATION_ID_HEADER = "X-Correlation-Id"
CALL_ID_HEADER = "X-Donkey-Request-Id"

# Semantic-cache steering, sent lowercase as written (docs/verified-apis.md §2).
CACHE_SKIP_HEADER = "x-cache-skip"
CACHE_NO_STORE_HEADER = "x-cache-no-store"
CACHE_TTL_HEADER = "x-cache-ttl"
CACHE_THRESHOLD_HEADER = "x-cache-threshold"
CACHE_PRINCIPAL_ID_HEADER = "x-cache-principal-id"

# LLM proxy consumer auth: the client-id-enforcement pair (docs/verified-apis.md §2/§3).
LLM_PROXY_CLIENT_ID_HEADER = "client_id"
LLM_PROXY_CLIENT_SECRET_HEADER = "client_secret"

# Model-wallet and bearer ingress: the wallet selector, and the token header and
# scheme (docs/verified-apis.md §2/§3).
LLM_PROXY_WALLET_CLIENT_ID_HEADER = "X-Client-Id"
LLM_PROXY_WALLET_JWT_HEADER = "Authorization"
LLM_PROXY_WALLET_JWT_SCHEME = "Bearer"

# --- Response headers --------------------------------------------------------

# Standard HTTP headers the refusal classifier reads (docs/verified-apis.md §4).
RETRY_AFTER_HEADER = "retry-after"
WWW_AUTHENTICATE_HEADER = "www-authenticate"

# Injection-protection verdict (docs/verified-apis.md §4).
INJECTION_PROTECTION_HEADER = "x-injection-protection"

# Token-rate-limit budget (docs/verified-apis.md §4): the numeric trio, and the
# prose header that carries the same three values on responses without it.
TOKEN_HEADER_PREFIX = "x-token-"
TOKEN_LIMIT_HEADER = "x-token-limit"
TOKEN_REMAINING_HEADER = "x-token-remaining"
TOKEN_RESET_HEADER = "x-token-reset"
RATELIMIT_HEADER = "x-llm-proxy-ratelimit"

# The prefix every LLM proxy policy header shares.
LLM_PROXY_HEADER_PREFIX = "x-llm-proxy-"

# Gateway identity (docs/verified-apis.md §3). The request id is the upstream
# provider's own, so its name depends on the provider.
REQUEST_ID_HEADER = "x-request-id"  # OpenAI, Azure OpenAI
AMZN_REQUEST_ID_HEADER = "x-amzn-requestid"  # Amazon Bedrock
APIM_REQUEST_ID_HEADER = "apim-request-id"  # Azure OpenAI's APIM
ANTHROPIC_REQUEST_ID_HEADER = "request-id"  # Anthropic, native ingress
DECORATOR_OPERATION_HEADER = "x-envoy-decorator-operation"

# Routing (docs/verified-apis.md §3).
ROUTING_TYPE_HEADER = "x-llm-proxy-routing-type"
ROUTING_FALLBACK_HEADER = "x-llm-proxy-routing-fallback"
LLM_PROVIDER_HEADER = "x-llm-proxy-llm-provider"
LLM_MODEL_HEADER = "x-llm-proxy-llm-model"
SEMANTIC_ROUTING_SUCCESS_HEADER = "x-llm-proxy-semantic-routing-success"

# Semantic-cache outcome (docs/verified-apis.md §2).
SEMANTIC_CACHE_STATUS_HEADER = "x-semantic-cache-status"
SEMANTIC_CACHE_SCORE_HEADER = "x-semantic-cache-score"

# Content-safety verdicts, an ``-action`` and a ``-reason`` per vendor
# (docs/verified-apis.md §4).
AZURE_CONTENT_SAFETY_ACTION_HEADER = "x-llm-proxy-azure-content-safety-action"
AZURE_CONTENT_SAFETY_REASON_HEADER = "x-llm-proxy-azure-content-safety-reason"
BEDROCK_GUARDRAIL_ACTION_HEADER = "x-llm-proxy-bedrock-guardrail-action"
BEDROCK_GUARDRAIL_REASON_HEADER = "x-llm-proxy-bedrock-guardrail-reason"
