"""Publication — registering code-first assets into Exchange (BG §2.5).

Symmetric with governed-only discovery: one declarative object, three verbs, "declare in code, apply
in CI, verify at runtime". The symmetry breaks at runtime (BG §2.5): there is NO
runtime ``publish()`` — it would be actively harmful (immutable versions, catalog
reflecting process starts, privilege escalation, no review). The runtime verb is
:meth:`verify` (read-only drift check), the genuine mirror of ``resolve()``.

Verbs (BG §2.5):
  * ``preview()`` — laptop. Renders the entry as it would appear. Writes nothing.
  * ``export()``  — laptop. Compiles into donkey.yaml (provisioning-as-code). CI publishes on merge.
  * ``verify()``  — runtime, READ-ONLY. Fetches the published descriptor,
    introspects the live server, compares. Raises PublicationDrift on mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..core import _verify


class PublicationAssetType(Enum):
    """SDK publication categories; exact Exchange token strings are UNVERIFIED."""

    MCP_SERVER = "mcp"
    A2A_AGENT = "a2a-agent"
    AGENT = "agent"
    API = "api"


class VersionStrategy(Enum):
    PINNED = "pinned"            # default — no implicit bumps in a shared catalog
    FROM_PACKAGE = "from-package"
    SEMANTIC_AUTO = "semantic-auto"


@dataclass(frozen=True)
class Contact:
    team: str
    email: str


@dataclass(frozen=True)
class DescriptionIssue:
    tool: str
    kind: str  # "missing" | "tautological" | "too-short"
    detail: str


@dataclass(frozen=True)
class Publication:
    asset_type: PublicationAssetType
    group_id: str
    asset_id: str
    version: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    categories: dict[str, str] = field(default_factory=dict)
    contact: Contact | None = None
    descriptor: str = "auto"  # "auto" | "auto:live" | "auto:static" | "auto:check"
    docs: list[tuple[str, str]] = field(default_factory=list)
    endpoint: str | None = None
    governance: Any | None = None
    version_strategy: VersionStrategy = VersionStrategy.PINNED

    # ---- verb: preview (laptop) -------------------------------------------
    async def preview(self, donkey: Any) -> str:
        """Render the Exchange entry as it would appear (BG §2.5). Blocked until
        descriptor derivation + Exchange render shape are verified (BG §2.5)."""
        raise _verify.blocked(
            "descriptor derivation (BG §2.5) + Exchange entry render (BG §2.5). The "
            "description-quality report (check_description_quality) is implemented "
            "and should run here (BG §2.5)."
        )

    # ---- verb: export (laptop) --------------------------------------------
    def export(self, path: str | Path | None = None) -> str:
        """Compile to a ``donkey.yaml`` fragment (provisioning-as-code)."""
        raise _verify.blocked(
            "Publication.export() emits the donkey.yaml spec format. Schema is shared with "
            "provisioning-as-code — do not diverge it (provisioning-as-code)."
        )

    # ---- verb: verify (runtime, READ-ONLY) --------------------------------
    async def verify(self, donkey: Any, *, raise_on_drift: bool = False) -> None:
        """Compare the live server against the published descriptor; raise
        :class:`~donkey_kit.core.errors.PublicationDrift` on mismatch (BG §2.5).
        Defaults to warn-and-continue — a drifted catalog must be loud but must
        not take down production traffic. Blocked until Exchange read +
        introspection are verified (BG §2.5)."""
        raise _verify.blocked(
            "Exchange descriptor read + live introspection for verify() (BG §2.5)."
        )


_NORMALISE = re.compile(r"[_\W]+")


def check_description_quality(
    tools: list[tuple[str, str | None]], *, min_len: int = 12
) -> list[DescriptionIssue]:
    """Fail-worthy description problems (BG §2.5). Pure, implemented now.

    A description missing, or equal to the identifier (after normalising
    underscores/case), or shorter than ``min_len`` is a FAILURE, not a warning —
    a tautological description makes a useless tool look documented.
    """

    issues: list[DescriptionIssue] = []
    for name, desc in tools:
        if not desc or not desc.strip():
            issues.append(DescriptionIssue(name, "missing", "no description"))
            continue
        norm_name = _NORMALISE.sub("", name).lower()
        norm_desc = _NORMALISE.sub("", desc).lower()
        if norm_desc == norm_name:
            issues.append(
                DescriptionIssue(name, "tautological", f"description equals identifier {name!r}")
            )
        elif len(desc.strip()) < min_len:
            issues.append(
                DescriptionIssue(name, "too-short", f"description under {min_len} chars")
            )
    return issues
