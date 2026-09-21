"""The Governance object and its three-verb lifecycle.

ONE object, THREE verbs, THREE trust levels:

  * ``simulate()`` — local, developer laptop. Ephemeral local gateway; touches
    nothing shared.
  * ``export()``   — compiles to a ``donkey.yaml`` fragment (provisioning-as-code). Writes a
    file; touches nothing.
  * ``resolve()``  — runtime, READ-ONLY. Looks up the already-provisioned route,
    verifies the declared policies are actually applied, returns the base_url.
    Raises :class:`GovernanceDrift` on mismatch.

There is deliberately NO ``apply()`` on the runtime object: applying to
sandbox/prod goes through ``donkey apply`` in CI, against a reviewed spec,
under platform-controlled credentials. An escape hatch exists for platform teams
(:meth:`apply`) with an explicit, hard-to-miss kwarg.

Local is NOT sandbox-with-a-different-URL: feature sets differ, policies
are not portable across modes, identity/secrets differ. ``simulate()`` must
loudly report which declared policies are skipped locally and why.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

from .core import _verify
from .core.errors import ConfigError

GatewayMode = Literal["local", "managed", "self-managed"]


class PolicyPortability(Enum):
    """Whether a policy works in Local Mode, Connected Mode, or both.

    The concrete classification per policy MUST come from real data captured by
    the Verification milestone, not inference — until then policies default to ``UNKNOWN``.
    """

    BOTH = "both"
    CONNECTED_ONLY = "connected_only"
    LOCAL_ONLY = "local_only"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PolicyBinding:
    asset_id: str
    version: str
    config: dict[str, Any] = field(default_factory=dict)
    #: Populated from the portability table (the Verification milestone); UNKNOWN until then.
    portability: PolicyPortability = PolicyPortability.UNKNOWN


@dataclass(frozen=True)
class GatewayTarget:
    mode: GatewayMode
    base_url: str
    environment: str | None = None
    gateway_name: str | None = None
    connected: bool = True  # local-mode gateways are unconnected

    @classmethod
    def from_env(cls) -> GatewayTarget:
        """Select a profile from ``.donkey-kit.toml`` via ``DONKEY_TARGET``
        (``local|sandbox|production``) (environment targeting)."""

        target = os.environ.get("DONKEY_TARGET", "local")
        profiles = _load_targets()
        if target not in profiles:
            raise ConfigError(
                f"DONKEY_TARGET={target!r} has no matching [targets.{target}] "
                f"profile in .donkey-kit.toml. Defined: {sorted(profiles) or 'none'}."
            )
        p = profiles[target]
        mode: GatewayMode = cast(GatewayMode, p.get("mode", "local"))
        return cls(
            mode=mode,
            base_url=p["base_url"],
            environment=p.get("environment"),
            gateway_name=p.get("gateway_name"),
            connected=(mode != "local"),
        )


@dataclass(frozen=True)
class Governance:
    name: str
    gateway: GatewayTarget
    policies: list[PolicyBinding] = field(default_factory=list)

    # ---- verb 1: simulate (local) -----------------------------------------
    def simulate(self) -> SimulationContext:
        """Start an ephemeral local Omni Gateway harness (BG §1.4).

        Requires the ``[local]`` extra (docker). Whether Local Mode can run the
        LLM Proxy / MCP Bridge at all is a gate in the Verification milestone; if not, LLM traffic
        is served by a clearly-labelled local mock proxy. Either way, skipped
        connected-only policies are reported loudly and non-suppressibly.
        """

        return SimulationContext(self)

    # ---- verb 2: export (sandbox/prod, laptop) ----------------------------
    def export(self, path: str | Path | None = None) -> str:
        """Compile to a ``donkey.yaml`` fragment (provisioning-as-code)."""
        raise _verify.blocked(
            "Governance.export() emits the shared donkey.yaml spec format; keep it identical "
            "to donkey_kit.provisioning.spec and do not diverge it. The export mapping remains "
            "unresolved: docs/verified-apis.md §5 confirms an Agent Network Maven-project + CLI "
            "flow but not whether export() wraps that toolchain or emits its project layout "
            "(the Verification milestone). This surface does not add a provisioning control "
            "plane competing with API Manager or Terraform."
        )

    # ---- verb 3: resolve (runtime, READ-ONLY) -----------------------------
    async def resolve(self, donkey: Any) -> GatewayTarget:
        """READ-ONLY: verify the declared policies are actually applied on the
        provisioned gateway and return its route. Raises
        :class:`~donkey_kit.core.errors.GovernanceDrift` on mismatch.

        Blocked until the API Manager read APIs are verified (the Verification milestone)."""
        raise _verify.blocked(
            "runtime governed-route lookup + applied-policy read for resolve() "
            "(the Verification milestone)."
        )

    # ---- escape hatch (platform teams only) -------------------------------
    async def apply(self, target: GatewayTarget, *, i_am_the_platform_team: bool = False) -> None:
        """Platform-team-only direct apply. Requires write scopes the
        default connected app will not hold; every use is logged at WARNING."""
        if not i_am_the_platform_team:
            raise PermissionError(
                "Governance.apply() inverts the platform-team ownership model "
                "(provisioning-as-code). Runtime code should use resolve() (read-only). If you are "
                "the platform team automating your own gateway, pass "
                "i_am_the_platform_team=True and ensure the connected app holds "
                "write scopes."
            )
        raise _verify.blocked(
            "direct policy apply endpoint (provisioning-as-code, the Verification milestone)."
        )


class SimulationContext:
    """Async context manager for ``gov.simulate()`` (BG §1.4)."""

    def __init__(self, gov: Governance) -> None:
        self._gov = gov

    def skipped_policies(self) -> list[tuple[PolicyBinding, str]]:
        """Declared policies that will NOT be exercised locally, with reasons —
        printed loudly before start and repeated at teardown."""
        skipped: list[tuple[PolicyBinding, str]] = []
        for p in self._gov.policies:
            if p.portability in (PolicyPortability.CONNECTED_ONLY, PolicyPortability.UNKNOWN):
                reason = (
                    "connected-mode only (needs API Manager client apps)"
                    if p.portability is PolicyPortability.CONNECTED_ONLY
                    else "portability UNKNOWN pending Verification-milestone verification"
                )
                skipped.append((p, reason))
        return skipped

    async def __aenter__(self) -> Any:
        raise _verify.blocked(
            "local Omni Gateway docker harness + Local-Mode LLM-Proxy/MCP-Bridge "
            "availability (BG §1.4, the Verification milestone). The [local] extra and the loud "
            "skipped-policy report (skipped_policies()) are scaffolded; the docker "
            "orchestration is gated on the Verification milestone's local-mode findings."
        )

    async def __aexit__(self, *exc: object) -> None:
        return None


def _load_targets() -> dict[str, dict[str, Any]]:
    path = Path.cwd() / ".donkey-kit.toml"
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed {path}: {exc}") from exc
    targets = data.get("targets", {})
    return targets if isinstance(targets, dict) else {}
