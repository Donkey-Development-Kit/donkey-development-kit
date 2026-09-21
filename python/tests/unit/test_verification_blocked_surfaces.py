"""Regression coverage for verification-blocked SDK surfaces (§0.3).

CLI ``_blocked(...)`` paths are intentionally not duplicated here: all eight
are covered by ``test_cli_provisioning.py`` from PR #476.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.governance import GatewayTarget, Governance
from donkey_kit.provisioning.applier import apply as apply_plan
from donkey_kit.provisioning.planner import Plan, build_plan
from donkey_kit.provisioning.publish import publish_if_changed
from donkey_kit.provisioning.spec import DonkeySpec, SpecMetadata

_TARGET = GatewayTarget(mode="local", base_url="http://localhost", connected=False)
_GOVERNANCE = Governance(name="blocked-governance", gateway=_TARGET)
_SPEC = DonkeySpec(
    metadata=SpecMetadata(name="blocked-spec", environment="Sandbox"),
)
_PLAN = Plan()
_CONFIG = DonkeyConfig(
    telemetry=False,
    correlation_header="X-Test-Correlation-Id",
    call_id_header="X-Test-Call-Id",
)


async def _enter_simulation(_donkey: Donkey) -> None:
    async with _GOVERNANCE.simulate():
        pass


_SYNC_BLOCKED_SURFACES: tuple[
    tuple[str, Callable[[Donkey], object]], ...
] = (
    ("Donkey.tools.lock", lambda donkey: donkey.tools.lock()),
    ("Governance.export", lambda _donkey: _GOVERNANCE.export()),
)

_ASYNC_BLOCKED_SURFACES: tuple[
    tuple[str, Callable[[Donkey], Awaitable[object]]], ...
] = (
    ("Donkey.tools.discover", lambda donkey: donkey.tools.discover()),
    ("Governance.resolve", lambda donkey: _GOVERNANCE.resolve(donkey)),
    (
        "Governance.apply",
        lambda _donkey: _GOVERNANCE.apply(_TARGET, i_am_the_platform_team=True),
    ),
    ("SimulationContext.__aenter__", _enter_simulation),
    (
        "publish_if_changed",
        lambda donkey: publish_if_changed(object(), donkey),
    ),
    ("build_plan", lambda donkey: build_plan(_SPEC, donkey)),
    ("provisioning.apply", lambda donkey: apply_plan(_PLAN, donkey)),
)


@pytest.mark.parametrize(
    ("_surface", "invoke"),
    _SYNC_BLOCKED_SURFACES,
    ids=[surface for surface, _ in _SYNC_BLOCKED_SURFACES],
)
async def test_sync_verification_blocked_surfaces_remain_blocked(
    _surface: str,
    invoke: Callable[[Donkey], object],
) -> None:
    async with Donkey(_CONFIG, auth=StaticToken("unused")) as donkey:
        with pytest.raises(NotImplementedError, match=r"^blocked on verification:"):
            invoke(donkey)


@pytest.mark.parametrize(
    ("_surface", "invoke"),
    _ASYNC_BLOCKED_SURFACES,
    ids=[surface for surface, _ in _ASYNC_BLOCKED_SURFACES],
)
async def test_async_verification_blocked_surfaces_remain_blocked(
    _surface: str,
    invoke: Callable[[Donkey], Awaitable[object]],
) -> None:
    async with Donkey(_CONFIG, auth=StaticToken("unused")) as donkey:
        with pytest.raises(NotImplementedError, match=r"^blocked on verification:"):
            await invoke(donkey)


async def test_governance_apply_requires_platform_team_authorization() -> None:
    with pytest.raises(PermissionError):
        await _GOVERNANCE.apply(_TARGET)
