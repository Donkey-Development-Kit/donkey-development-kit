"""Apply (provisioning-as-code).

Partial-failure handling (provisioning-as-code): apply resources in dependency order, stop on
first failure, report exactly what was applied and what was not. Do NOT attempt
automatic rollback — report state and let the operator re-plan. Silent partial
rollback in a control plane is worse than a clear stop.

Policy ownership (provisioning-as-code): apply runs in CI under a platform-controlled connected
app, and may only reference policy assets enumerated in a platform-owned
allow-list (``policy-catalog.yaml``). The allow-list mechanism ships in v1 even
if nobody asks — its absence gets the SDK banned in security review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core import _verify
from .planner import Plan

__all__ = ["ApplyResult", "PolicyAllowList", "apply"]


@dataclass(frozen=True)
class PolicyAllowList:
    """Platform-owned allow-list of referenceable policy assets (provisioning-as-code)."""

    allowed_asset_ids: frozenset[str]

    @classmethod
    def load(cls, path: str | Path) -> PolicyAllowList:
        """Read the allow-list from a ``policy-catalog.yaml`` (its ``allowedPolicies`` list)."""
        import yaml

        data = yaml.safe_load(Path(path).read_text()) or {}
        ids = data.get("allowedPolicies", [])
        return cls(allowed_asset_ids=frozenset(ids))

    def check(self, asset_ids: list[str]) -> list[str]:
        """Return the asset ids that are NOT in the allow-list."""
        return [a for a in asset_ids if a not in self.allowed_asset_ids]


@dataclass
class ApplyResult:
    """What an :func:`apply` run applied, what it did not, and the error that stopped it."""

    applied: list[str] = field(default_factory=list)
    not_applied: list[str] = field(default_factory=list)
    error: str | None = None


async def apply(
    plan: Plan, donkey: object, *, allow_list: PolicyAllowList | None = None
) -> ApplyResult:
    """Apply ``plan`` in dependency order, stopping on the first failure.

    Raises:
        NotImplementedError: Always, for now; see docs/verified-apis.md §5.
    """
    raise _verify.blocked(
        "MCP Bridge provisioning write API for apply (provisioning-as-code). "
        "Read-before-write + stop-on-first-failure semantics are specified; wire "
        "them once the API is confirmed, or emit Terraform (provisioning-as-code)."
    )
