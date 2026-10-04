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
from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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

# The public names resolve lazily (PEP 562): ``import donkey_kit`` loads no
# submodule until a name is first used. The conformance plugin auto-loads on
# every pytest run via its pytest11 entry point, so an eager import here would
# pull the whole SDK into every test session in the environment (#746).
_LAZY = {
    "AgentKilled": ".core.errors",
    "AssetRef": ".registry",
    "AssetType": ".registry",
    "AuthError": ".core.errors",
    "Budget": ".core.budget",
    "BudgetReserveReached": ".core.errors",
    "CacheControls": ".core.cachecontrol",
    "CacheScope": ".core.cachecontrol",
    "ConfigError": ".core.errors",
    "Contact": ".registry",
    "ContentSafetyBlocked": ".core.errors",
    "CostTags": ".core.cost",
    "Donkey": ".donkey",
    "DonkeyAsyncClientView": ".core.transport",
    "DonkeyClientView": ".core.transport",
    "DonkeyConfig": ".core.config",
    "DonkeyError": ".core.errors",
    "ExchangeRegistry": ".registry",
    "GatewayUnavailable": ".core.errors",
    "GovernanceCriteria": ".registry",
    "GovernanceDrift": ".core.errors",
    "LLMClient": ".llm.client",
    "LastCall": ".core.lastcall",
    "LastCallStatus": ".core.lastcall",
    "ModelNotRoutable": ".core.errors",
    "ModelSubstituted": ".core.errors",
    "PIIDetected": ".core.errors",
    "PlatformTeamOnly": ".core.errors",
    "PolicyViolation": ".core.errors",
    "PromptInjectionBlocked": ".core.errors",
    "ProvisioningError": ".core.errors",
    "Publication": ".registry",
    "PublicationAssetType": ".registry",
    "PublicationDrift": ".core.errors",
    "Region": ".core.config",
    "RegistryError": ".core.errors",
    "RunScope": ".core.telemetry",
    "STRICT": ".registry",
    "TokenBudgetExceeded": ".core.errors",
    "ToolInvocationError": ".core.errors",
    "ToolSpec": ".core.toolspec",
    "ToolsFacade": ".donkey",
    "TypedRefusals": ".core.refusals",
    "UpstreamModelError": ".core.errors",
    "UpstreamRequestError": ".core.errors",
    "classify": ".core.errors",
    "registered_tools": ".core.toolspec",
    "typed_refusals": ".integrations",
}


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


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
