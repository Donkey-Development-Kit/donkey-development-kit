"""core/ — the framework-free foundation (§2).

HARD RULE (§1.1): nothing in this package may import from
``donkey_kit.integrations``. Enforced by import-linter in CI.
"""

from .auth import AnypointConnectedApp, AuthProvider, ChainedAuth, StaticToken
from .budget import Budget
from .cache import TTLCache
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
    "classify",
    "cost_headers",
    "current_correlation_id",
    "current_cost_tags",
    "new_correlation_id",
    "run_context",
]
