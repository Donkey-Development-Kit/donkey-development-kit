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

from .core.budget import Budget
from .core.config import DonkeyConfig, Region
from .core.cost import CostTags
from .core.errors import (
    AuthError,
    BudgetReserveReached,
    ConfigError,
    ContentSafetyBlocked,
    DonkeyError,
    GatewayUnavailable,
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
)

__version__ = "0.1.0.dev2"

__all__ = [
    "STRICT",
    "AssetRef",
    "AssetType",
    "AuthError",
    "Budget",
    "BudgetReserveReached",
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
    "ModelSubstituted",
    "PIIDetected",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "Publication",
    "ProvisioningError",
    "PublicationDrift",
    "Region",
    "RegistryError",
    "TokenBudgetExceeded",
    "ToolInvocationError",
    "ToolSpec",
    "UpstreamModelError",
    "__version__",
    "registered_tools",
]
