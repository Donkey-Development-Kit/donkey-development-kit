"""Deprecated alias of :mod:`donkey_kit.registry.criteria` (#719).

This module was renamed so it no longer shares a name with the top-level
:mod:`donkey_kit.governance`. Import the criteria from :mod:`donkey_kit.registry`
instead; this alias will be removed in a later release.
"""

from __future__ import annotations

import warnings

from .criteria import STRICT, Check, GovernanceCriteria, GovernanceReport, evaluate

__all__ = ["STRICT", "Check", "GovernanceCriteria", "GovernanceReport", "evaluate"]

warnings.warn(
    "donkey_kit.registry.governance is deprecated; import from donkey_kit.registry "
    "(the module is now donkey_kit.registry.criteria).",
    DeprecationWarning,
    stacklevel=2,
)
