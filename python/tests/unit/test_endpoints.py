"""``core.endpoints``: which hosts count as loopback (#1443)."""

from __future__ import annotations

import pytest

from donkey_kit.core.endpoints import is_loopback, is_loopback_host


@pytest.mark.parametrize(
    "host", ["localhost", "LOCALHOST", "127.0.0.1", "127.8.9.10", "::1", "[::1]"]
)
def test_loopback_hosts(host: str) -> None:
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "10.0.0.5", "agent.internal", "", "localhost."])
def test_hosts_other_machines_can_reach(host: str) -> None:
    assert not is_loopback_host(host)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8080/", True),
        ("http://[::1]:8080/", True),
        ("http://0.0.0.0:8080/", False),
        ("https://gw.example.com/", False),
        ("not a url", False),
    ],
)
def test_is_loopback_reads_the_url_host(url: str, expected: bool) -> None:
    assert is_loopback(url) is expected
