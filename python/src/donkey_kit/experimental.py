"""Types for surfaces that are still blocked on verification (§0.3).

Every name here belongs to a surface whose platform API is not yet confirmed
against a real sandbox: Exchange discovery and governed-state checks (BG §2.7),
code-first publication (BG §2.5), and MCP tool calls. Each surface currently
raises ``NotImplementedError("blocked on verification: …")``. The types are
importable so code can be written against them, but they are not part of the
stable ``donkey_kit`` namespace. A name moves up to ``donkey_kit`` when its
surface is verified, and until then it may change in any release.

Moved out of the ``donkey_kit`` namespace in #730 (ADR 0008 in ``docs/adr/``),
so that ``donkey_kit.__all__`` exports nothing that only a blocked or refused
surface uses.

    from donkey_kit.experimental import STRICT, AssetRef

    await donkey.registry.explain(AssetRef.parse("com.acme/tools/1.0.0"), criteria=STRICT)
"""

from __future__ import annotations

from .core.errors import PublicationDrift, RegistryError, ToolInvocationError
from .registry import (
    STRICT,
    AssetRef,
    AssetType,
    Contact,
    GovernanceCriteria,
    Publication,
    PublicationAssetType,
)

__all__ = [
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
]
