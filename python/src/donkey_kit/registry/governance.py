"""Governed-only discovery.

"Governed" is NOT a flag — it is a computed predicate joining state across three
systems, environment-scoped. The criteria and report types here are
pure and fully implemented. The JOIN that populates a report from live API
Manager / ruleset state lives in :mod:`donkey_kit.registry.exchange` and is
gated on M0 verification (the Verification milestone).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GovernanceCriteria:
    """Definition of 'governed'. Every field is a separate, independently
    checkable condition. Defaults are deliberately moderate; orgs override."""

    require_api_instance: bool = True
    require_deployed: bool = True
    require_any_policy: bool = True
    required_policies: list[str] = field(default_factory=list)
    forbidden_policies: list[str] = field(default_factory=list)
    require_governance_pass: bool = False
    require_gateways: list[str] = field(default_factory=list)
    require_tags: list[str] = field(default_factory=list)
    require_lifecycle: list[str] = field(default_factory=list)
    #: If a check cannot be evaluated, does the asset pass? Matters a lot: with
    #: allow_unknown=False an unavailable API silently filters the catalog to
    #: zero — which is why every excluded asset MUST carry a reason (governed-only discovery).
    allow_unknown: bool = False


STRICT = GovernanceCriteria(
    require_governance_pass=True,
    required_policies=["client-id-enforcement"],
    allow_unknown=False,
)


@dataclass(frozen=True)
class Check:
    """One governance condition's outcome. ``passed=None`` means UNKNOWN /
    could-not-evaluate — surfaced, never silently dropped (governed-only discovery)."""

    name: str
    passed: bool | None
    detail: str


@dataclass(frozen=True)
class GovernanceReport:
    """Result of :meth:`explain` — why an asset is (not) governed."""

    governed: bool
    checks: list[Check]

    def reasons_failed(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.passed is not True]


def evaluate(checks: list[Check], criteria: GovernanceCriteria) -> GovernanceReport:
    """Combine individual checks into a verdict, honouring ``allow_unknown``.

    A ``passed=None`` (UNKNOWN) check passes only when ``allow_unknown`` is True;
    otherwise it fails the asset — but the reason is always retained so a
    developer can tell "filtered out" from "broken credential" (governed-only discovery).
    """

    governed = True
    for check in checks:
        if check.passed is True:
            continue
        if check.passed is None and criteria.allow_unknown:
            continue
        governed = False
    return GovernanceReport(governed=governed, checks=checks)
