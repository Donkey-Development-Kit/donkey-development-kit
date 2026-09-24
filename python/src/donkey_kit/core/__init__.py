"""core/ — the framework-free foundation.

HARD RULE (the layered architecture): nothing in this package may import from
``donkey_kit.integrations``. Enforced by import-linter in CI.
"""

from .auth import AnypointConnectedApp, AuthProvider, ChainedAuth, StaticToken
from .budget import Budget
from .cache import TTLCache
from .cachecontrol import CacheControls, CacheScope, cache_scope, current_cache_controls
from .config import DonkeyConfig, Region
from .cost import CostTags
from .errors import (
    AuthError,
    BudgetReserveReached,
    ConfigError,
    ContentSafetyBlocked,
    DonkeyError,
    GovernanceDrift,
    ModelSubstituted,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    ProvisioningError,
    PublicationDrift,
    RegistryError,
    TokenBudgetExceeded,
    ToolInvocationError,
    UpstreamModelError,
    classify,
)
from .telemetry import (
    current_correlation_id,
    current_cost_tags,
    new_correlation_id,
    run_context,
)
from .transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    attribution_headers,
    build_http_client,
    build_sync_http_client,
    cost_headers,
)

__all__ = [
    "AnypointConnectedApp",
    "AuthError",
    "AuthProvider",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "ChainedAuth",
    "ConfigError",
    "ContentSafetyBlocked",
    "CostTags",
    "DonkeyAsyncClient",
    "DonkeyClient",
    "DonkeyConfig",
    "DonkeyError",
    "GovernanceDrift",
    "ModelSubstituted",
    "PIIDetected",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "ProvisioningError",
    "PublicationDrift",
    "Region",
    "RegistryError",
    "StaticToken",
    "TTLCache",
    "TokenBudgetExceeded",
    "ToolInvocationError",
    "UpstreamModelError",
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
