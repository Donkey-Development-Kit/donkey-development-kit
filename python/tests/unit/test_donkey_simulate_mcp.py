"""``Donkey.simulate_mcp()`` argument checks and discovery scoping (#1391, ADR 0023)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError

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
