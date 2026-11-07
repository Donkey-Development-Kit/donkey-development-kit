"""A ToolSet carrying a donkey.lock refuses drift and drops unpinned tools (BG §2.7, #1646)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from donkey_kit.registry.criteria import GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.filter import ToolExclusion
from donkey_kit.tools.lockfile import LockDrift, Lockfile, ToolLockDrift, build_lock
from donkey_kit.tools.session import ToolSet

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_policies"
_KEY = "com.example/inventory"


def _catalog(name: str) -> tuple[dict[str, Any], ...]:
    capture = json.loads((_FIXTURES / f"{name}-list.json").read_text("utf-8"))
    payload = capture["body"].removeprefix("event: message\ndata: ").strip()
    return tuple(json.loads(payload)["result"]["tools"])


def _handle(catalog: tuple[dict[str, Any], ...] | None) -> McpServerHandle:
    return McpServerHandle(
        ref=AssetRef("com.example", "inventory", "1.0.0"),
        endpoint_url="https://gateway.example.test/mcp/",
        governance=GovernanceReport(governed=True, checks=[]),
        tool_descriptors=catalog,
    )


def _locked(lock: Lockfile) -> ToolSet:
    return ToolSet([_handle(None)], _auth=ClientIdAuth("id", "secret"), _lock=lock)


def _replay(monkeypatch: pytest.MonkeyPatch, listed: tuple[dict[str, Any], ...]) -> None:
    async def replay(url: str, headers: object, *, server: str) -> tuple[dict[str, Any], ...]:
        return listed

    monkeypatch.setattr(session_module, "list_tools", replay)


async def test_locked_describe_passes_on_match(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _catalog("mapping")
    _replay(monkeypatch, catalog)
    described = await _locked(build_lock([_handle(catalog)])).describe()
    assert described.servers[0].tool_descriptors == catalog
    assert described.excluded == ()


async def test_locked_describe_raises_on_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_handle(catalog)])
    _replay(monkeypatch, catalog[1:])
    with pytest.raises(ToolLockDrift) as exc:
        await _locked(lock).describe()
    assert exc.value.drift == (LockDrift(_KEY, str(catalog[0]["name"]), "tool_missing"),)


async def test_unpinned_tool_excluded_and_warned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_handle(catalog)])
    _replay(monkeypatch, (*catalog, {"name": "new_tool", "inputSchema": {}}))
    with caplog.at_level(logging.WARNING, logger=session_module.__name__):
        described = await _locked(lock).describe()
    assert (
        ToolExclusion(name="new_tool", server="inventory", reason="unpinned") in described.excluded
    )
    assert "new_tool" not in {d.name for d in described._descriptors()}
    assert "new_tool" in caplog.text
    again = await described.describe()
    assert again is described


async def test_filter_after_lock_is_not_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _catalog("mapping")
    lock = build_lock([_handle(catalog)])
    _replay(monkeypatch, (*catalog, {"name": "new_tool", "inputSchema": {}}))
    name = str(catalog[0]["name"])
    narrowed = _locked(lock).filter(deny=[name])
    described = await narrowed.describe()
    reasons = {(e.name, e.reason) for e in described.excluded}
    assert (name, "filter") in reasons
    assert ("new_tool", "unpinned") in reasons


async def test_caller_missing_pinned_tool_is_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    # A per-caller catalog (as_user, ABAC) that omits a pinned tool is drift (deliberate).
    catalog = _catalog("progressive")
    lock = build_lock([_handle(catalog)])
    _replay(monkeypatch, catalog[:1])
    with pytest.raises(ToolLockDrift):
        await _locked(lock).describe()


async def test_unlocked_toolset_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = _catalog("mapping")
    _replay(monkeypatch, (*catalog, {"name": "new_tool", "inputSchema": {}}))
    plain = ToolSet([_handle(None)], _auth=ClientIdAuth("id", "secret"))
    described = await plain.describe()
    assert described.excluded == ()
    assert "new_tool" in {d.name for d in described._descriptors()}
