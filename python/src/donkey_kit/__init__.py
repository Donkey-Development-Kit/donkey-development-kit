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
from .core.cachecontrol import CacheControls
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
from .core.toolspec import ToolSpec, registered_tools
from .donkey import Donkey
from .registry import (
    STRICT,
    AssetRef,
    AssetType,
    Contact,
    GovernanceCriteria,
    Publication,
    PublicationAssetType,
)

# A library attaches only a NullHandler to its root logger, so the SDK's DEBUG
# records stay silent until the application configures logging (#717).
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

__version__ = "0.1.1"

__all__ = [
    "STRICT",
    "AgentKilled",
    "AssetRef",
    "AssetType",
    "AuthError",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "ConfigError",
    "Contact",
    "ContentSafetyBlocked",
    "CostTags",
    "Donkey",
    "DonkeyConfig",
    "DonkeyError",
    "GatewayUnavailable",
    "GovernanceCriteria",
    "GovernanceDrift",
    "ModelNotRoutable",
    "ModelSubstituted",
    "PIIDetected",
    "PlatformTeamOnly",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "Publication",
    "PublicationAssetType",
    "ProvisioningError",
    "PublicationDrift",
    "Region",
    "RegistryError",
    "TokenBudgetExceeded",
    "ToolInvocationError",
    "ToolSpec",
    "UpstreamModelError",
    "UpstreamRequestError",
    "__version__",
    "classify",
    "registered_tools",
]
