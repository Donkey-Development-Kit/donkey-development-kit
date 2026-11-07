"""``donkey.tools.lock()`` and ``discover(locked=True)`` (BG §2.7, #1646)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from donkey_kit import ConfigError, Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.lockfile import ToolLockDrift, read_lock

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_policies"
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])


def _catalog(name: str) -> tuple[dict[str, Any], ...]:
    capture = json.loads((_FIXTURES / f"{name}-list.json").read_text("utf-8"))
    payload = capture["body"].removeprefix("event: message\ndata: ").strip()
    return tuple(json.loads(payload)["result"]["tools"])


class _Registry:
    def __init__(self, *assets: str) -> None:
        self.assets = assets

    def refresh(self) -> None:
        pass

    async def search(self, **_: Any) -> list[AssetRef]:
        return [AssetRef("com.example", a, "1.0.0") for a in self.assets]

    async def explain(self, ref: AssetRef, **_: Any) -> GovernanceReport:
        return _GOVERNED

    async def resolve_mcp(self, ref: AssetRef, **_: Any) -> McpServerHandle:
        return McpServerHandle(ref=ref, endpoint_url=f"https://gw.example.test/{ref.asset_id}")


def _donkey(registry: _Registry) -> Donkey:
    donkey = Donkey(DonkeyConfig(telemetry=False), auth=StaticToken("unused"))
    donkey.tools._discovery._registry = registry  # type: ignore[assignment]  # test seam
    donkey.tools._discovery._auth = ClientIdAuth("id", "secret")
    return donkey


def _replay(monkeypatch: pytest.MonkeyPatch, listed: tuple[dict[str, Any], ...]) -> list[str]:
    calls: list[str] = []

    async def replay(url: str, headers: object, *, server: str) -> tuple[dict[str, Any], ...]:
        calls.append(url)
        return listed

    monkeypatch.setattr(session_module, "list_tools", replay)
    return calls


async def test_lock_writes_file_and_returns_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _replay(monkeypatch, _catalog("mapping"))
    async with _donkey(_Registry("inventory")) as donkey:
        path = await donkey.tools.lock(path=tmp_path / "donkey.lock")
    assert path == (tmp_path / "donkey.lock").resolve()
    assert "arithmetic" in read_lock(path).servers["com.example/inventory"].tools


async def test_discover_locked_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _replay(monkeypatch, _catalog("progressive"))
    lock = tmp_path / "donkey.lock"
    async with _donkey(_Registry("inventory")) as donkey:
        await donkey.tools.lock(path=lock)
        tools = await donkey.tools.discover(locked=True, lock_path=lock)
        described = await tools.describe()
    assert described.excluded == ()


async def test_discover_locked_missing_file(tmp_path: Path) -> None:
    async with _donkey(_Registry("inventory")) as donkey:
        with pytest.raises(ConfigError) as exc:
            await donkey.tools.discover(locked=True, lock_path=tmp_path / "nope.lock")
    assert "donkey.tools.lock()" in exc.value.remediation


async def test_discover_locked_server_missing_raises_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _replay(monkeypatch, _catalog("mapping"))
    lock = tmp_path / "donkey.lock"
    async with _donkey(_Registry("inventory")) as donkey:
        await donkey.tools.lock(path=lock)
    calls.clear()
    async with _donkey(_Registry("other")) as donkey:
        with pytest.raises(ToolLockDrift) as exc:
            await donkey.tools.discover(locked=True, lock_path=lock)
    assert exc.value.drift[0].kind == "server_missing"
    assert calls == []


async def test_discover_locked_drops_unpinned_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _replay(monkeypatch, _catalog("mapping"))
    lock = tmp_path / "donkey.lock"
    async with _donkey(_Registry("inventory")) as donkey:
        await donkey.tools.lock(path=lock)
    async with _donkey(_Registry("inventory", "extra")) as donkey:
        tools = await donkey.tools.discover(locked=True, lock_path=lock)
    assert [s.ref.asset_id for s in tools.servers] == ["inventory"]


def test_tool_lock_drift_exported() -> None:
    from donkey_kit import ToolLockDrift as Exported

    assert Exported is ToolLockDrift
