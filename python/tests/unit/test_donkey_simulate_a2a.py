"""``Donkey.simulate_a2a()`` argument checks and resolution scoping (#1652, ADR 0025)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.simulator.a2a import CAPTURES
from donkey_kit.simulator.fixtures import SHAPES

_AGENTS = ["acme/triage-agent/1.0.0"]


def _donkey() -> Donkey:
    return Donkey(DonkeyConfig(telemetry=False))


@pytest.mark.parametrize(
    ("capture", "agents", "version", "times", "message"),
    [
        ("nope", _AGENTS, "1.0", 1, "No packaged A2A v1.0 capture"),
        ("pii-stream", _AGENTS, "0.3", 1, "No packaged A2A v0.3 capture"),
        ("azure-block-send", _AGENTS, "0.3", 1, "No packaged A2A v0.3 capture"),
        ("pii-send", _AGENTS, "2.0", 1, "version must be"),
        ("pii-send", [], "1.0", 1, "non-empty list"),
        ("pii-send", "acme/triage-agent/1.0.0", "1.0", 1, "non-empty list"),
        ("pii-send", ["acme/triage-agent"], "1.0", 1, "group_id/asset_id/version"),
        ("pii-send", _AGENTS, "1.0", 0, "int >= 1"),
        ("pii-send", _AGENTS, "1.0", True, "int >= 1"),
    ],
)
def test_invalid_arguments_raise_on_enter(capture, agents, version, times, message) -> None:
    with pytest.raises(ValueError, match=message):
        with _donkey().simulate_a2a(capture, agents=agents, version=version, times=times):
            pass


def test_every_name_maps_to_a_packaged_capture() -> None:
    assert {shape for shape in CAPTURES.values()} <= set(SHAPES)
    assert {shape for shape in SHAPES if shape.startswith("a2a-")} == set(CAPTURES.values())


def test_nesting_raises_and_leaves_the_outer_block_intact() -> None:
    donkey = _donkey()
    with donkey.simulate_a2a("pii-send", agents=_AGENTS):
        with pytest.raises(RuntimeError, match="do not nest"):
            with donkey.simulate_a2a("rate-send", agents=_AGENTS):
                pass
        assert donkey.a2a._simulated is not None
    assert donkey.a2a._simulated is None


def test_scope_is_per_donkey() -> None:
    first, second = _donkey(), _donkey()
    with first.simulate_a2a("pii-send", agents=_AGENTS):
        assert second.a2a._simulated is None


def test_import_donkey_kit_loads_no_simulator_module() -> None:
    code = (
        "import sys, donkey_kit\n"
        "donkey_kit.Donkey.simulate_a2a\n"
        "assert not [m for m in sys.modules if m.startswith('donkey_kit.simulator')]\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
