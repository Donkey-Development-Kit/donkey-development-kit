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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, cast

from .core import _verify
from .core.config import load_config_table
from .core.errors import ConfigError, PlatformTeamOnly

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
        """Start an ephemeral local harness for this governance spec (BG §1.4). Roadmap.

        Entering the returned context is still ``_verify.blocked``. When it lands it is
        meant to run on the pure-Python local simulator (the ``[local]`` extra), with no
        Docker and no Omni/Flex Gateway Local Mode, which the SDK does not support (#661).
        Skipped connected-only policies are reported loudly and non-suppressibly.
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
            raise PlatformTeamOnly(
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
            "running a Governance spec's policies locally (BG §1.4, the Verification "
            "milestone). The loud skipped-policy report (skipped_policies()) is scaffolded. "
            "For local refusals today use donkey.simulate() or the [local] simulator "
            "(donkey mock); Omni/Flex Gateway Local Mode is not supported (#661)."
        )

    async def __aexit__(self, *exc: object) -> None:
        return None


def _load_targets() -> dict[str, dict[str, Any]]:
    return cast("dict[str, dict[str, Any]]", load_config_table("targets"))
