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
from typing import TYPE_CHECKING

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
from .core.telemetry import RunScope
from .core.toolspec import ToolSpec, registered_tools
from .core.transport import DonkeyAsyncClientView, DonkeyClientView
from .donkey import Donkey, ToolsFacade
from .llm.client import LLMClient
from .registry import ExchangeRegistry

# A library attaches only a NullHandler to its root logger, so the SDK's DEBUG
# records stay silent until the application configures logging (#717).
_logging.getLogger(__name__).addHandler(_logging.NullHandler())

__version__ = "0.1.2.dev0"

__all__ = [
    "AgentKilled",
    "AuthError",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "ConfigError",
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
    "UpstreamModelError",
    "UpstreamRequestError",
    "__version__",
    "classify",
    "registered_tools",
]

# Names that moved to donkey_kit.experimental in #730. They are out of __all__,
# but a public symbol is deprecated before it is removed (CONTRIBUTING, "One
# source of truth, no dead code"), so the old spelling still resolves with a
# DeprecationWarning. Defined for the runtime only: type checkers see no
# module __getattr__, so they flag the old import and every other typo.
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

if not TYPE_CHECKING:

    def __getattr__(name: str) -> object:
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
