"""donkey-kit — an SDK for consuming Agent Fabric
capabilities from your own agent framework.

See the README for the maintainer + support statement and the trademark note.
"Agent Fabric" is a MuleSoft product name; this package is descriptive.

Public surface (BG §1.8):

    from donkey_kit import Donkey
    donkey = Donkey.from_env()
    donkey.langgraph.chat_model("gpt-4o")   # native ChatOpenAI

Working-instruction reminder (verification discipline, #2): many platform endpoints/headers/class
names are UNVERIFIED. Those code paths raise
``NotImplementedError("blocked on verification: …")`` rather than guessing. See
docs/verified-apis.md.
"""

from __future__ import annotations

import logging as _logging

from .core.budget import Budget
from .core.cachecontrol import CacheControls, CacheScope
from .core.config import DonkeyConfig, Region
from .core.cost import CostTags
from .core.errors import (
    AgentKilled,
    AuthError,
    BudgetReserveReached,
    ConfigError,
    ContentSafetyBlocked,
    DonkeyError,
    GatewayUnavailable,
    GovernanceDrift,
    ModelNotRoutable,
    ModelSubstituted,
    PIIDetected,
    PlatformTeamOnly,
    PolicyViolation,
    PromptInjectionBlocked,
    ProvisioningError,
    PublicationDrift,
    RegistryError,
    TokenBudgetExceeded,
    ToolInvocationError,
    UpstreamModelError,
    UpstreamRequestError,
    classify,
)
from .core.lastcall import LastCall, LastCallStatus
from .core.refusals import TypedRefusals
from .core.telemetry import RunScope
from .core.toolspec import ToolSpec, registered_tools
from .core.transport import DonkeyAsyncClientView, DonkeyClientView
from .donkey import Donkey, ToolsFacade
from .integrations import typed_refusals
from .llm.client import LLMClient
from .registry import (
    STRICT,
    AssetRef,
    AssetType,
    Contact,
    ExchangeRegistry,
    GovernanceCriteria,
    Publication,
    PublicationAssetType,
)

# A library attaches only a NullHandler to its root logger, so the SDK's DEBUG
# records stay silent until the application configures logging (#717).
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

__version__ = "0.1.2.dev0"

__all__ = [
    "STRICT",
    "AgentKilled",
    "AssetRef",
    "AssetType",
    "AuthError",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "ConfigError",
    "Contact",
    "ContentSafetyBlocked",
    "CostTags",
    "Donkey",
    "DonkeyAsyncClientView",
    "DonkeyClientView",
    "DonkeyConfig",
    "DonkeyError",
    "ExchangeRegistry",
    "GatewayUnavailable",
    "GovernanceCriteria",
    "GovernanceDrift",
    "LLMClient",
    "LastCall",
    "LastCallStatus",
    "ModelNotRoutable",
    "ModelSubstituted",
    "PIIDetected",
    "PlatformTeamOnly",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "ProvisioningError",
    "Publication",
    "PublicationAssetType",
    "PublicationDrift",
    "Region",
    "RegistryError",
    "RunScope",
    "TokenBudgetExceeded",
    "ToolInvocationError",
    "ToolSpec",
    "ToolsFacade",
    "TypedRefusals",
    "UpstreamModelError",
    "UpstreamRequestError",
    "__version__",
    "classify",
    "registered_tools",
    "typed_refusals",
]
