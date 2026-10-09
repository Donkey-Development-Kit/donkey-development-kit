"""The default suite is hermetic (#747): the guarantees ``tests/conftest.py`` and
``[tool.pytest.ini_options]`` in ``pyproject.toml`` make, pinned so that
dropping a flag, the autouse fixture or one of its exemptions fails here rather
than resurfacing as a shell-dependent flake.

The env and sandbox checks run ``tests/conftest.py`` itself inside a
``pytester`` session with the variables set, so they do not depend on what the
calling shell exports. The socket and timeout checks run against this
session's own configuration.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from pytest_socket import SocketConnectBlockedError

_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

# TEST-NET-1 (RFC 5737): never routed, so even if the guard were missing the
# connect could not reach a real host — it would time out, not succeed.
_NON_LOOPBACK = ("192.0.2.1", 80)

_AMBIENT = {
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "http_proxy": "http://127.0.0.1:9",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
    "DONKEY_LLM_PROXY_URL": "http://127.0.0.1:9/",
    "MULESOFT_ANYTHING": "1",
    "DONKEY_CONTRACT_EXTRA": "langgraph",
    "DONKEY_BENCHMARK_JSON": "/tmp/benchmark-result.json",
}


def _run_with_ambient_env(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, test_body: str
) -> pytest.RunResult:
    for name, value in _AMBIENT.items():
        monkeypatch.setenv(name, value)
    pytester.makeconftest(_CONFTEST.read_text(encoding="utf-8"))
    pytester.makeini(
        "[pytest]\nasyncio_default_fixture_loop_scope = function\nmarkers =\n    sandbox: live\n"
    )
    pytester.makepyfile(test_body)
    return pytester.runpytest_inprocess("-p", "no:cacheprovider", "--allow-hosts=127.0.0.1")


def test_ambient_proxy_and_governance_env_is_cleared(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default-suite test sees no proxy, ``OTEL_*``, ``DONKEY_*`` or
    ``MULESOFT_*`` variable from the calling shell, except the CI harness
    switches: ``DONKEY_CONTRACT_EXTRA`` (#742), which the adapter-contract leg
    relies on to turn a missing framework into a failure instead of a skip, and
    ``DONKEY_BENCHMARK_JSON`` (#753, #1060), the path the benchmark job uploads
    its result from."""
    result = _run_with_ambient_env(
        pytester,
        monkeypatch,
        """
        import os

        def test_env():
            assert os.environ.get("DONKEY_CONTRACT_EXTRA") == "langgraph"
            assert os.environ.get("DONKEY_BENCHMARK_JSON") == "/tmp/benchmark-result.json"
            for name in ("HTTPS_PROXY", "http_proxy", "OTEL_EXPORTER_OTLP_ENDPOINT",
                         "DONKEY_LLM_PROXY_URL", "MULESOFT_ANYTHING"):
                assert name not in os.environ, name
        """,
    )
    result.assert_outcomes(passed=1)


# Lift this outer test's own --allow-hosts guard: pytest-socket's enable_socket
# does not undo a guard already installed, so the inner session could not
# otherwise observe the unguarded state the sandbox exemption produces.
@pytest.mark.enable_socket
def test_sandbox_tests_keep_the_ambient_env_and_real_sockets(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``@pytest.mark.sandbox`` tests call the real proxies with the ambient
    credentials, so they keep the environment and are exempt from the
    loopback-only ``--allow-hosts`` restriction."""
    result = _run_with_ambient_env(
        pytester,
        monkeypatch,
        """
        import os
        import socket

        import pytest

        @pytest.mark.sandbox
        def test_live():
            assert os.environ["DONKEY_LLM_PROXY_URL"] == "http://127.0.0.1:9/"
            assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:9"
            assert socket.socket.connect.__name__ == "connect"

        def test_default():
            assert socket.socket.connect.__name__ == "guarded_connect"
        """,
    )
    result.assert_outcomes(passed=2)


# pytest-socket's error also emits a UserWarning carrying the same message.
@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_non_loopback_connect_fails_fast() -> None:
    """``--allow-hosts`` blocks a real outbound socket with a clear error
    before any packet is sent."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(SocketConnectBlockedError, match="192.0.2.1"):
            sock.connect(_NON_LOOPBACK)
    finally:
        sock.close()


def test_loopback_connect_is_allowed() -> None:
    """The local gateway simulator and in-process collectors bind 127.0.0.1,
    so loopback must stay reachable under the same restriction."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass


def test_every_test_is_capped_by_a_timeout(pytestconfig: pytest.Config) -> None:
    """pytest-timeout's ``timeout`` ini caps every test at 60s, so a call that
    stalls on a dead proxy or collector fails instead of hanging CI."""
    assert float(pytestconfig.getini("timeout")) == 60
