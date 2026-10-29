"""core/ — the framework-free foundation.

HARD RULE (the layered architecture): nothing in this package may import from
``donkey_kit.integrations``. Enforced by import-linter in CI.
"""

from __future__ import annotations

from .auth import AnypointConnectedApp, AuthProvider, ChainedAuth, StaticToken
from .budget import Budget, RequestWindow, UpstreamQuota, UpstreamWindow
from .cache import TTLCache
from .cachecontrol import CacheControls, CacheScope, cache_scope, current_cache_controls
from .classifier import Classifier, PolicyRegistration, classify
from .config import ConfigOverrides, DonkeyConfig, Region
from .correlation import AttributionScope, current_business_group
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
)
from .identity import UserScope
from .lastcall import LastCall, LastCallStatus
from .step_up import GatewayApprovalDenied, GatewayApprovalPending
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
    "AttributionScope",
    "AuthError",
    "AuthProvider",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "ChainedAuth",
    "Classifier",
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
    "GatewayApprovalDenied",
    "GatewayApprovalPending",
    "GatewayUnavailable",
    "LastCall",
    "LastCallStatus",
    "ModelNotRoutable",
    "ModelSubstituted",
    "PIIDetected",
    "PolicyRegistration",
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
    "UpstreamQuota",
    "UpstreamRequestError",
    "UpstreamWindow",
    "UserScope",
    "attribution_headers",
    "build_http_client",
    "build_sync_http_client",
    "cache_scope",
    "classify",
    "cost_headers",
    "current_business_group",
    "current_cache_controls",
    "current_correlation_id",
    "current_cost_tags",
    "new_correlation_id",
    "run_context",
]
