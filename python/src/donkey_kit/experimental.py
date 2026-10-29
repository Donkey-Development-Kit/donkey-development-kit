"""Types outside the stable namespace, most of them for surfaces still blocked
on verification (§0.3).

The lockfile and the governed-state search join (BG §2.7) and ``Publication``'s
descriptor derivation (BG §2.5) raise ``NotImplementedError("blocked on
verification: …")``; the registry and tool-call types those surfaces share have
no settled tier yet. The types are importable so code can be written against
them, but they are not part of the stable ``donkey_kit`` namespace, and they may
change in any release (ADR 0006).

Moved out of the ``donkey_kit`` namespace in #730 (ADR 0008 in ``docs/adr/``).
A name moves up to ``donkey_kit`` when its tier is settled.
``GovernanceCriteria``, ``ToolNotInProfile`` and ``UngovernedMcpServer`` moved
up in 0.2.0 (#1425), because the surfaces that take and raise them, tool
discovery and binding, are confirmed against a live gateway
(``docs/verified-apis.md`` §9). Their
``donkey_kit.experimental.<name>`` spelling still resolves, with a
``DeprecationWarning``, until a later release removes it.

    from donkey_kit.experimental import STRICT, AssetRef

    await donkey.registry.explain(AssetRef.parse("com.acme/tools/1.0.0"), criteria=STRICT)
"""

from __future__ import annotations

import warnings as _warnings
from importlib import import_module as _import_module
from typing import TYPE_CHECKING

from .core.errors import PublicationDrift, RegistryError, ToolInvocationError
from .core.tool_access import tool_access_denial
from .registry import (
    STRICT,
    AssetRef,
    AssetType,
    Contact,
    Publication,
    PublicationAssetType,
)

if TYPE_CHECKING:
    # The redundant ``as`` marks an explicit re-export, so a type checker still
    # accepts the deprecated spelling.
    from .core.tool_access import ToolNotInProfile as ToolNotInProfile
    from .registry import GovernanceCriteria as GovernanceCriteria
    from .tools.session import UngovernedMcpServer as UngovernedMcpServer

# Names promoted to donkey_kit in #1425, with the module each one resolves from.
_PROMOTED = {
    "GovernanceCriteria": ".registry",
    "ToolNotInProfile": ".core.tool_access",
    "UngovernedMcpServer": ".tools.session",
}

if not TYPE_CHECKING:

    def __getattr__(name: str) -> object:
        module = _PROMOTED.get(name)
        if module is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        _warnings.warn(
            f"donkey_kit.experimental.{name} is deprecated; import it from donkey_kit "
            "(its surface is verified, #1425).",
            DeprecationWarning,
            stacklevel=2,
        )
        return getattr(_import_module(module, "donkey_kit"), name)


__all__ = [
    "STRICT",
    "AssetRef",
    "AssetType",
    "Contact",
    "Publication",
    "PublicationAssetType",
    "PublicationDrift",
    "RegistryError",
    "ToolInvocationError",
    "tool_access_denial",
]
