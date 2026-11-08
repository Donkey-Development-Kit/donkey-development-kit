"""``Donkey.simulate_mcp()`` argument checks and discovery scoping (#1391, ADR 0023),
and ``discover(locked=True)`` inside the block (#1671, ADR 0024)."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.tools.lockfile import LockDrift, LockedServer, Lockfile, ToolLockDrift, write_lock

_SERVERS = {"acme/crm-tools/1.0.0": [{"name": "lookup_customer"}]}


def _donkey() -> Donkey:
    return Donkey(DonkeyConfig(telemetry=False))


@pytest.mark.parametrize(
    ("capture", "servers", "times", "message"),
    [
        ("nope", _SERVERS, 1, "Unknown MCP capture"),
        ("pii-request", {}, 1, "at least one"),
        ("pii-request", {"acme/crm-tools": [{"name": "x"}]}, 1, "group_id/asset_id/version"),
        ("pii-request", {"acme/crm-tools/1.0.0": []}, 1, "non-empty list"),
        ("pii-request", {"acme/crm-tools/1.0.0": ["x"]}, 1, "descriptor mappings"),
        ("pii-request", {"acme/crm-tools/1.0.0": [{"name": "a"}, {"name": "a"}]}, 1, "unique"),
        ("pii-request", _SERVERS, 0, "positive integer"),
        ("pii-request", _SERVERS, True, "positive integer"),
    ],
)
def test_invalid_arguments_raise_on_enter(capture, servers, times, message) -> None:
    with pytest.raises(ValueError, match=message):
        with _donkey().simulate_mcp(capture, servers=servers, times=times):
            pass


async def test_discover_returns_declared_governed_servers_without_registry() -> None:
    donkey = _donkey()  # no control-plane config: a registry read would raise ConfigError
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        tools = await donkey.tools.discover(tags=["ignored"])
        (server,) = tools.servers
        assert server.governance is not None and server.governance.governed
        assert server.endpoint_url.startswith("https://simulated.donkey-kit.invalid/")
        assert await donkey.tools.warm() == 1
    with pytest.raises(ConfigError):
        await donkey.tools.discover()


def _lock(path: Path, *assets: str) -> Path:
    servers = {
        f"acme/{a}": LockedServer("acme", a, "1.0.0", {"lookup_customer": "0" * 64}) for a in assets
    }
    write_lock(Lockfile(servers=servers), path)
    return path


async def test_discover_locked_without_a_lockfile_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no ./donkey.lock: same refusal as a live discover()
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        with pytest.raises(ConfigError):
            await donkey.tools.discover(locked=True)


async def test_discover_locked_raises_drift_for_a_pinned_server_not_declared(
    tmp_path: Path,
) -> None:
    lock = _lock(tmp_path / "donkey.lock", "crm-tools", "billing")
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        with pytest.raises(ToolLockDrift) as caught:
            await donkey.tools.discover(locked=True, lock_path=lock)
    assert caught.value.drift == (LockDrift("acme/billing", None, "server_missing"),)


async def test_discover_locked_drops_a_declared_server_the_lock_does_not_pin(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    lock = _lock(tmp_path / "donkey.lock", "crm-tools")
    servers = {**_SERVERS, "acme/billing/2.0.0": [{"name": "refund"}]}
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=servers):
        with caplog.at_level(logging.WARNING):
            tools = await donkey.tools.discover(locked=True, lock_path=lock)
        unlocked = await donkey.tools.discover()
    assert [s.ref.coordinates for s in tools.servers] == ["acme/crm-tools/1.0.0"]
    assert "acme/billing/2.0.0 is not pinned in donkey.lock" in caplog.text
    assert len(unlocked.servers) == 2


async def test_discover_locked_checks_declared_tools_against_the_lock(tmp_path: Path) -> None:
    pytest.importorskip("mcp")  # describe() lists tools through the offline MCP session
    lock = tmp_path / "donkey.lock"
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        path = await donkey.tools.lock(path=lock)
        described = await (await donkey.tools.discover(locked=True, lock_path=path)).describe()
        assert [t["name"] for t in described.servers[0].tool_descriptors or ()] == [
            "lookup_customer"
        ]
    changed = {"acme/crm-tools/1.0.0": [{"name": "lookup_customer", "description": "Find anyone"}]}
    with donkey.simulate_mcp("pii-request", servers=changed):
        tools = await donkey.tools.discover(locked=True, lock_path=lock)
        with pytest.raises(ToolLockDrift) as caught:
            await tools.describe()
    assert caught.value.drift == (LockDrift("acme/crm-tools", "lookup_customer", "changed"),)


async def test_discover_locked_drops_declared_tools_the_lock_does_not_pin(tmp_path: Path) -> None:
    pytest.importorskip("mcp")
    lock = tmp_path / "donkey.lock"
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        await donkey.tools.lock(path=lock)
    grown = {
        "acme/crm-tools/1.0.0": [*_SERVERS["acme/crm-tools/1.0.0"], {"name": "delete_customer"}]
    }
    with donkey.simulate_mcp("pii-request", servers=grown):
        described = await (await donkey.tools.discover(locked=True, lock_path=lock)).describe()
    (server,) = described.servers
    assert [t["name"] for t in server.tool_descriptors or ()] == ["lookup_customer"]
    assert [(e.name, e.reason) for e in described.excluded] == [("delete_customer", "unpinned")]


def test_nesting_raises_and_leaves_the_outer_block_intact() -> None:
    donkey = _donkey()
    with donkey.simulate_mcp("pii-request", servers=_SERVERS):
        with pytest.raises(RuntimeError, match="do not nest"):
            with donkey.simulate_mcp("abac-denied", servers=_SERVERS):
                pass
        assert donkey.tools._discovery._simulated is not None
    assert donkey.tools._discovery._simulated is None


def test_scope_is_per_donkey() -> None:
    first, second = _donkey(), _donkey()
    with first.simulate_mcp("pii-request", servers=_SERVERS):
        assert second.tools._discovery._simulated is None


def test_import_donkey_kit_loads_no_simulator_module() -> None:
    code = (
        "import sys, donkey_kit\n"
        "donkey_kit.Donkey.simulate_mcp\n"
        "assert not [m for m in sys.modules if m.startswith('donkey_kit.simulator')]\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
