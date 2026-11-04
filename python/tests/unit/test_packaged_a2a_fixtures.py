"""Packaged ingress evidence remains byte-identical to #1393 (BG section 2.9.3)."""

from __future__ import annotations

from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pytest

from donkey_kit.simulator import fixtures

_ORIGINALS = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"
_NAMES = [
    f"v1-{policy}-{method}"
    for policy in ("azure-block", "pii", "rate")
    for method in ("send", "stream")
]


@pytest.mark.parametrize("name", _NAMES)
def test_served_a2a_bytes_match_original_capture_and_lock(name: str) -> None:
    """Only method-matched v1 refusals are served; no auth-code or new policy capture."""
    fixture = fixtures.load(f"a2a-{name}")
    original_lock = fixtures.read_test_lock()
    served_lock = fixtures.read_lock()
    for suffix in ("headers.txt", "body.json"):
        original = _ORIGINALS / f"{name}.{suffix}"
        if not original.exists():
            assert fixture.body == b""
            continue
        path = f"anypoint/a2a_policies/{original.name}"
        assert (
            fixtures.fixture_bytes("anypoint/a2a_policies", original.name) == original.read_bytes()
        )
        assert served_lock[path] == original_lock[path]


def test_packaged_a2a_tree_contains_only_served_pairs_and_provenance() -> None:
    root = files("donkey_kit.simulator") / "_fixtures" / "anypoint" / "a2a_policies"
    expected = {f"{name}.headers.txt" for name in _NAMES}
    expected |= {f"{name}.body.json" for name in _NAMES if "-rate-" not in name}
    expected.add("README.md")
    assert {resource.name for resource in root.iterdir()} == expected
    assert (
        fixtures.fixture_bytes("anypoint/a2a_policies", "README.md")
        == (_ORIGINALS / "README.md").read_bytes()
    )
    assert fixtures.fixture_bytes("anypoint", "README.a2a.md")


@pytest.mark.parametrize("name", _NAMES)
def test_replay_filters_credentials_and_server_framing_independently(name: str) -> None:
    """Plant headers in memory, never capture bytes; pin negatives outside the allow-list."""
    fixture = fixtures.load(f"a2a-{name}")
    forbidden = {
        "authorization",
        "proxy-authorization",
        "client_id",
        "client_secret",
        "x-client-id",
        "api-key",
        "x-api-key",
        "server",
        "date",
        "connection",
        "transfer-encoding",
        "content-length",
        "strict-transport-security",
        "set-cookie",
    }
    planted = replace(
        fixture, headers={**fixture.headers, **dict.fromkeys(forbidden, "must-not-replay")}
    )
    assert forbidden.isdisjoint(fixtures.replay_headers(planted))
