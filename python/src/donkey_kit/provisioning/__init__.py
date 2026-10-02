"""provisioning/ — CI-oriented declarative provisioning-as-code.

Separate entry point from the runtime SDK. Nothing in the runtime path mutates
shared state (working instruction #11); every mutation lives here and runs in CI
from a reviewed spec under platform-controlled credentials.
"""
from __future__ import annotations

import warnings

from .applier import ApplyResult, PolicyAllowList
from .lint import Finding, LintResult, Severity
from .planner import Change, Plan
from .publish import content_digest
from .spec import (
    ApiSpec,
    ApiToolSpec,
    DonkeySpec,
    HttpMapping,
    McpBridgeSpec,
    PolicySpec,
    SpecMetadata,
)

__all__ = [
    "ApiSpec",
    "ApiToolSpec",
    "ApplyResult",
    "Change",
    "DonkeySpec",
    "Finding",
    "HttpMapping",
    "LintResult",
    "McpBridgeSpec",
    "Plan",
    "PolicyAllowList",
    "PolicySpec",
    "Severity",
    "SpecMetadata",
    "content_digest",
]


def __getattr__(name: str) -> type[ApiToolSpec]:
    # Deprecated alias (#719): ``ToolSpec`` collided with ``donkey_kit.ToolSpec``.
    if name == "ToolSpec":
        warnings.warn(
            "donkey_kit.provisioning.ToolSpec is deprecated; use ApiToolSpec.",
            DeprecationWarning,
            stacklevel=2,
        )
        return ApiToolSpec
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
