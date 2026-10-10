"""core/ — the framework-free foundation.

HARD RULE (the layered architecture): nothing in this package may import from
``donkey_kit.integrations``. Enforced by import-linter in CI.
"""

from __future__ import annotations

from .auth import AnypointConnectedApp, AuthProvider, ChainedAuth, StaticToken
from .budget import Budget, RequestWindow
from .cache import TTLCache
from .cachecontrol import CacheControls, CacheScope, cache_scope, current_cache_controls
from .config import ConfigOverrides, DonkeyConfig, Region
from .cost import CostTags
from .errors import (
    AgentKilled,
    AuthError,
    BudgetReserveReached,
    ConfigError,
    ContentSafetyBlocked,
    DonkeyError,
    GatewayUnavailable,
    ModelNotRoutable,
    ModelSubstituted,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    PublicationDrift,
    RegistryError,
    RequestRateLimitExceeded,
    TokenBudgetExceeded,
    ToolInvocationError,
    UpstreamModelError,
    UpstreamRequestError,
    classify,
)
from .lastcall import LastCall, LastCallStatus
from .telemetry import (
    RunScope,
    current_correlation_id,
    current_cost_tags,
    new_correlation_id,
    run_context,
)
from .transport import (
    DonkeyAsyncClient,
    DonkeyAsyncClientView,
    DonkeyClient,
    DonkeyClientView,
    attribution_headers,
    build_http_client,
    build_sync_http_client,
    cost_headers,
)

__all__ = [
    "AgentKilled",
    "AnypointConnectedApp",
    "AuthError",
    "AuthProvider",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "ChainedAuth",
    "ConfigError",
    "ConfigOverrides",
    "ContentSafetyBlocked",
    "CostTags",
    "DonkeyAsyncClient",
    "DonkeyAsyncClientView",
    "DonkeyClient",
    "DonkeyClientView",
    "DonkeyConfig",
    "DonkeyError",
    "GatewayUnavailable",
    "LastCall",
    "LastCallStatus",
    "ModelNotRoutable",
    "ModelSubstituted",
    "PIIDetected",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "PublicationDrift",
    "Region",
    "RegistryError",
    "RequestRateLimitExceeded",
    "RequestWindow",
    "RunScope",
    "StaticToken",
    "TTLCache",
    "TokenBudgetExceeded",
    "ToolInvocationError",
    "UpstreamModelError",
    "UpstreamRequestError",
    "attribution_headers",
    "build_http_client",
    "build_sync_http_client",
    "cache_scope",
    "classify",
    "cost_headers",
    "current_cache_controls",
    "current_correlation_id",
    "current_cost_tags",
    "new_correlation_id",
    "run_context",
]
