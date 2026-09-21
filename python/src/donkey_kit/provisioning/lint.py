"""Governance lint (provisioning-as-code).

Small, cheap, uncontroversial, and independent of the provisioning control plane
excluded by the build plan's "Do not build, at any phase" boundary.

Validates API specs against project and centralized rulesets BEFORE anything is
published, and fails the PR on ``error`` severity. Ruleset RESOLUTION against the
platform is gated (verification discipline — is a rulesets API exposed?), but local spec-shape
validation and per-rule severity handling are implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    message: str
    location: str | None = None


@dataclass(frozen=True)
class LintResult:
    findings: list[Finding]

    @property
    def failed(self) -> bool:
        """True if any ``error``-severity finding exists — fail the PR (provisioning-as-code)."""
        return any(f.severity is Severity.ERROR for f in self.findings)

    def render(self) -> str:
        if not self.findings:
            return "lint: no findings."
        return "\n".join(
            f"  [{f.severity.value}] {f.rule}: {f.message}"
            + (f" ({f.location})" if f.location else "")
            for f in self.findings
        )
