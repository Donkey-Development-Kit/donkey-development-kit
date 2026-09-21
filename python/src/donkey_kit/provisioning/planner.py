"""Plan / diff (provisioning-as-code).

Requirements the implementer must honour when this is wired to a real API:
  * Read-before-write, always — fetch current state, diff, render, then apply.
    Never blind-PUT.
  * Idempotent — re-running apply with no change makes zero mutating calls.
  * ``--dry-run`` / ``--out plan.json`` for CI gating.

GATE (provisioning-as-code, verification discipline): whether a usable MCP Bridge provisioning API
exists is an open Verification-milestone question. If UI-only, this whole module is cut and we emit
Terraform instead (provisioning-as-code). Until confirmed, planning against live state is blocked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core import _verify
from .spec import DonkeySpec


@dataclass(frozen=True)
class Change:
    action: str  # "create" | "update" | "remove"
    kind: str    # "mcpBridge" | "tool" | "policy"
    name: str
    detail: str = ""


@dataclass(frozen=True)
class Plan:
    changes: list[Change] = field(default_factory=list)

    def render(self) -> str:
        if not self.changes:
            return "No changes. Infrastructure matches the spec."
        sign = {"create": "+", "update": "~", "remove": "-"}
        lines = [
            f"  {sign.get(c.action, '?')} {c.kind:<10} {c.name:<28} "
            f"({c.action}) {c.detail}".rstrip()
            for c in self.changes
        ]
        lines.append(f"\n{len(self.changes)} changes. Run `apply` to proceed.")
        return "\n".join(lines)


async def build_plan(spec: DonkeySpec, donkey: object) -> Plan:
    raise _verify.blocked(
        "MCP Bridge provisioning read API for read-before-write planning "
        "(provisioning-as-code). If verification finds it UI-only, pivot to Terraform generation "
        "(provisioning-as-code) — do NOT reverse-engineer internal endpoints."
    )
