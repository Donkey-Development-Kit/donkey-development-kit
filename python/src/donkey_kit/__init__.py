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

Types that exist only for those blocked surfaces (``AssetRef``, ``Publication``,
``GovernanceCriteria``, ``STRICT``, ``RegistryError``, …) live in
:mod:`donkey_kit.experimental`, not here (#730, ADR 0008 in docs/adr/). Their old
``donkey_kit.<name>`` spelling still resolves, with a ``DeprecationWarning``,
until a later release removes it.
"""

from __future__ import annotations

import logging as _logging
import warnings as _warnings
from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core.budget import Budget
    from .core.cachecontrol import CacheControls, CacheScope
    from .core.config import ConfigOverrides, DonkeyConfig, Region
    from .core.cost import CostTags
    from .core.errors import (
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
        TokenBudgetExceeded,
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
    from .registry import ExchangeRegistry

# The public names resolve lazily (PEP 562): ``import donkey_kit`` loads no
# submodule until a name is first used. The conformance plugin auto-loads on
# every pytest run via its pytest11 entry point, so an eager import here would
# pull the whole SDK into every test session in the environment (#746).
_LAZY = {
    "AgentKilled": ".core.errors",
    "AuthError": ".core.errors",
    "Budget": ".core.budget",
    "BudgetReserveReached": ".core.errors",
    "CacheControls": ".core.cachecontrol",
    "CacheScope": ".core.cachecontrol",
    "ConfigError": ".core.errors",
    "ConfigOverrides": ".core.config",
    "ContentSafetyBlocked": ".core.errors",
    "CostTags": ".core.cost",
    "Donkey": ".donkey",
    "DonkeyAsyncClientView": ".core.transport",
    "DonkeyClientView": ".core.transport",
    "DonkeyConfig": ".core.config",
    "DonkeyError": ".core.errors",
    "ExchangeRegistry": ".registry",
    "GatewayUnavailable": ".core.errors",
    "LLMClient": ".llm.client",
    "LastCall": ".core.lastcall",
    "LastCallStatus": ".core.lastcall",
    "ModelNotRoutable": ".core.errors",
    "ModelSubstituted": ".core.errors",
    "PIIDetected": ".core.errors",
    "PolicyViolation": ".core.errors",
    "PromptInjectionBlocked": ".core.errors",
    "Region": ".core.config",
    "RunScope": ".core.telemetry",
    "TokenBudgetExceeded": ".core.errors",
    "ToolSpec": ".core.toolspec",
    "ToolsFacade": ".donkey",
    "TypedRefusals": ".core.refusals",
    "UpstreamModelError": ".core.errors",
    "UpstreamRequestError": ".core.errors",
    "classify": ".core.errors",
    "registered_tools": ".core.toolspec",
    "typed_refusals": ".integrations",
}


# Names that moved to donkey_kit.experimental in #730. They are out of __all__
# and _LAZY, but a public symbol is deprecated before it is removed
# (CONTRIBUTING, "One source of truth, no dead code"), so the old spelling still
# resolves with a DeprecationWarning.
_MOVED_TO_EXPERIMENTAL = frozenset(
    {
        "STRICT",
        "AssetRef",
        "AssetType",
        "Contact",
        "GovernanceCriteria",
        "Publication",
        "PublicationAssetType",
        "PublicationDrift",
        "RegistryError",
        "ToolInvocationError",
    }
)

# Defined for the runtime only: type checkers see the TYPE_CHECKING imports
# above and no module __getattr__, so they flag the old experimental spellings
# and every other typo.
if not TYPE_CHECKING:

    def __getattr__(name: str) -> object:
        module = _LAZY.get(name)
        if module is not None:
            value = getattr(_import_module(module, __name__), name)
            globals()[name] = value
            return value
        if name in _MOVED_TO_EXPERIMENTAL:
            from . import experimental

            _warnings.warn(
                f"donkey_kit.{name} is deprecated; import it from donkey_kit.experimental "
                "(its surface is still blocked on verification, #730).",
                DeprecationWarning,
                stacklevel=2,
            )
            return getattr(experimental, name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})


# A library attaches only a NullHandler to its root logger, so the SDK's DEBUG
# records stay silent until the application configures logging (#717).
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

__version__ = "0.1.2.dev1"

__all__ = [
    "AgentKilled",
    "AuthError",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "ConfigError",
    "ConfigOverrides",
    "ContentSafetyBlocked",
    "CostTags",
    "Donkey",
    "DonkeyAsyncClientView",
    "DonkeyClientView",
    "DonkeyConfig",
    "DonkeyError",
    "ExchangeRegistry",
    "GatewayUnavailable",
    "LLMClient",
    "LastCall",
    "LastCallStatus",
    "ModelNotRoutable",
    "ModelSubstituted",
    "PIIDetected",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "Region",
    "RunScope",
    "TokenBudgetExceeded",
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
