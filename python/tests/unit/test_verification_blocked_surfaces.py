"""Regression coverage for verification-blocked SDK surfaces (§0.3).

CLI blocked commands are intentionally not duplicated here: ``status``,
``publish`` and ``verify`` are covered by ``test_cli_blocked_commands.py``.
The refused governance/provisioning stubs were deleted, not kept blocked (#730). When a surface is
verified, remove its row here in the same change that records the VERIFIED date
and source in ``docs/verified-apis.md``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.experimental import Publication, PublicationAssetType
from donkey_kit.registry.publication import publish_if_changed

_PUBLICATION = Publication(
    asset_type=PublicationAssetType.MCP_SERVER,
    group_id="com.example",
    asset_id="blocked-asset",
    version="1.0.0",
    name="Blocked asset",
    description="An asset whose publication surface remains verification-blocked.",
)
_CONFIG = DonkeyConfig(
    telemetry=False,
    correlation_header="X-Test-Correlation-Id",
    call_id_header="X-Test-Call-Id",
)


_SYNC_BLOCKED_SURFACES: tuple[
    tuple[str, Callable[[Donkey], object]], ...
] = (
    ("Donkey.tools.lock", lambda donkey: donkey.tools.lock()),
)

_ASYNC_BLOCKED_SURFACES: tuple[
    tuple[str, Callable[[Donkey], Awaitable[object]]], ...
] = (
    ("Donkey.tools.discover", lambda donkey: donkey.tools.discover()),
    (
        "publish_if_changed",
        lambda donkey: publish_if_changed(_PUBLICATION, donkey),
    ),
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
