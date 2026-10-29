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
``STRICT``, ``RegistryError``, …) live in :mod:`donkey_kit.experimental`, not here
(#730, ADR 0008 in docs/adr/). Their old ``donkey_kit.<name>`` spelling still
resolves, with a ``DeprecationWarning``, until a later release removes it.

Every name in ``__all__`` is in the stable tier (ADR 0006), and
``tests/unit/test_public_api_snapshot.py`` holds the list.
"""

from __future__ import annotations

import logging as _logging
import warnings as _warnings
from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .a2a.types import A2AResult, A2ATask
    from .core.approvals import (
        Approval,
        ApprovalRejected,
        ApprovalRequired,
        Approvals,
        ApprovalTimedOut,
    )
    from .core.budget import Budget, RequestWindow, UpstreamQuota, UpstreamWindow
    from .core.cachecontrol import CacheControls, CacheScope
    from .core.classifier import Classifier, classify
    from .core.config import ConfigOverrides, DonkeyConfig, Region
    from .core.correlation import AttributionScope
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
        RequestRateLimitExceeded,
        TokenBudgetExceeded,
        UpstreamModelError,
        UpstreamRequestError,
    )
    from .core.identity import UserScope
    from .core.lastcall import LastCall, LastCallStatus
    from .core.reactions import OnRefusal
    from .core.refusals import TypedRefusals
    from .core.runledger import RunLedger, RunRefusal
    from .core.step_up import GatewayApprovalDenied, GatewayApprovalPending
    from .core.telemetry import RunScope
    from .core.tool_access import ToolNotInProfile
    from .core.toolspec import AgentSpec, ToolSpec, registered_agents, registered_tools
    from .core.transport import DonkeyAsyncClientView, DonkeyClientView
    from .donkey import Donkey, ToolsFacade
    from .integrations import typed_refusals
    from .llm.client import LLMClient
    from .registry import ExchangeRegistry, GovernanceCriteria
    from .tools.auth import McpAuth
    from .tools.session import ToolSet, UngovernedMcpServer

# The public names resolve lazily (PEP 562): ``import donkey_kit`` loads no
# submodule until a name is first used. The conformance plugin auto-loads on
# every pytest run via its pytest11 entry point, so an eager import here would
# pull the whole SDK into every test session in the environment (#746).
_LAZY = {
    "A2AResult": ".a2a.types",
    "A2ATask": ".a2a.types",
    "AgentKilled": ".core.errors",
    "Approval": ".core.approvals",
    "ApprovalRequired": ".core.approvals",
    "ApprovalRejected": ".core.approvals",
    "ApprovalTimedOut": ".core.approvals",
    "Approvals": ".core.approvals",
    "AttributionScope": ".core.correlation",
    "AuthError": ".core.errors",
    "Budget": ".core.budget",
    "BudgetReserveReached": ".core.errors",
    "RequestWindow": ".core.budget",
    "UpstreamQuota": ".core.budget",
    "UpstreamWindow": ".core.budget",
    "UserScope": ".core.identity",
    "CacheControls": ".core.cachecontrol",
    "CacheScope": ".core.cachecontrol",
    "ConfigError": ".core.errors",
    "ConfigOverrides": ".core.config",
    "Classifier": ".core.classifier",
    "ContentSafetyBlocked": ".core.errors",
    "CostTags": ".core.cost",
    "Donkey": ".donkey",
    "DonkeyAsyncClientView": ".core.transport",
    "DonkeyClientView": ".core.transport",
    "DonkeyConfig": ".core.config",
    "DonkeyError": ".core.errors",
    "ExchangeRegistry": ".registry",
    "GatewayApprovalDenied": ".core.step_up",
    "GatewayApprovalPending": ".core.step_up",
    "GatewayUnavailable": ".core.errors",
    "GovernanceCriteria": ".registry",
    "LLMClient": ".llm.client",
    "LastCall": ".core.lastcall",
    "LastCallStatus": ".core.lastcall",
    "McpAuth": ".tools.auth",
    "ModelNotRoutable": ".core.errors",
    "ModelSubstituted": ".core.errors",
    "OnRefusal": ".core.reactions",
    "PIIDetected": ".core.errors",
    "PolicyViolation": ".core.errors",
    "PromptInjectionBlocked": ".core.errors",
    "RunLedger": ".core.runledger",
    "RunRefusal": ".core.runledger",
    "Region": ".core.config",
    "RequestRateLimitExceeded": ".core.errors",
    "RunScope": ".core.telemetry",
    "TokenBudgetExceeded": ".core.errors",
    "ToolNotInProfile": ".core.tool_access",
    "ToolSet": ".tools.session",
    "ToolSpec": ".core.toolspec",
    "AgentSpec": ".core.toolspec",
    "ToolsFacade": ".donkey",
    "TypedRefusals": ".core.refusals",
    "UngovernedMcpServer": ".tools.session",
    "UpstreamModelError": ".core.errors",
    "UpstreamRequestError": ".core.errors",
    "classify": ".core.classifier",
    "registered_agents": ".core.toolspec",
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

# The package's one version source (#767): pyproject.toml declares the version
# dynamic and hatch reads this line, so bump it here and nowhere else. The
# release workflows fail when a tag disagrees with it (docs/releasing.md).
__version__ = "0.2.0.dev8"

__all__ = [
    "A2AResult",
    "A2ATask",
    "AgentKilled",
    "AgentSpec",
    "Approval",
    "ApprovalRejected",
    "ApprovalRequired",
    "ApprovalTimedOut",
    "Approvals",
    "AttributionScope",
    "AuthError",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "Classifier",
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
    "GatewayApprovalDenied",
    "GatewayApprovalPending",
    "GatewayUnavailable",
    "GovernanceCriteria",
    "LLMClient",
    "LastCall",
    "LastCallStatus",
    "McpAuth",
    "ModelNotRoutable",
    "ModelSubstituted",
    "OnRefusal",
    "PIIDetected",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "Region",
    "RequestRateLimitExceeded",
    "RequestWindow",
    "RunLedger",
    "RunRefusal",
    "RunScope",
    "TokenBudgetExceeded",
    "ToolNotInProfile",
    "ToolSet",
    "ToolSpec",
    "ToolsFacade",
    "TypedRefusals",
    "UngovernedMcpServer",
    "UpstreamModelError",
    "UpstreamQuota",
    "UpstreamRequestError",
    "UpstreamWindow",
    "UserScope",
    "__version__",
    "classify",
    "registered_agents",
    "registered_tools",
    "typed_refusals",
]
