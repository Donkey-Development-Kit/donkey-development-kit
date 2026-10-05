"""Shared pytest configuration for the SDK's own test suite.

Enables the ``pytester`` fixture so ``tests/unit/test_conformance_plugin.py`` can
spawn throwaway pytest sessions that load our own ``pytest11`` plugin
(``donkey-kit[test]``, #191) and assert its end-to-end behaviour — inert
without the flag, a clean UsageError on misuse, a scenario table on success.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from donkey_kit.core import runtime

pytest_plugins = ["pytester"]

# Proxy variables are standard on corporate laptops and in agent sandboxes
# (#747): httpx mounts a proxy transport from the environment, and those
# mounts win over any swapped-in fixture transport (see
# tests/unit/test_transport_proxy_mounts.py), so an ambient proxy makes an
# offline test's result depend on the calling shell. ``NO_PROXY`` is cleared
# alongside the rest so a test that asserts its *absence* starts from a known
# state too.
_PROXY_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
# Prefixes cleared wholesale rather than var-by-var, so a new OTEL_*/DONKEY_*
# var never needs a matching addition here: OTEL_* (an ambient collector would
# otherwise receive real span exports, #732), DONKEY_* (this SDK's own config
# env vars, §2.1) and MULESOFT_* (any ambient Anypoint/Mule tooling env).
_ENV_PREFIXES = ("OTEL_", "DONKEY_", "MULESOFT_")


@pytest.fixture(autouse=True)
def _empty_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the home directory at an empty temp dir, so a test that unsets
    ``XDG_CONFIG_HOME`` never reads the developer's own
    ``~/.config/.donkey-kit.toml`` (#837)."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.fixture(autouse=True)
def _hermetic_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear proxy, ``OTEL_*``, ``DONKEY_*`` and ``MULESOFT_*`` env vars before
    every test (#747), so a developer's or CI's ambient shell never changes
    which transport a test exercises, which collector a span export reaches, or
    which credentials a config resolve picks up. A test that needs one of these
    sets it itself (``monkeypatch.setenv``), which runs after this fixture.

    ``@pytest.mark.sandbox`` is the opt-in marker: those tests are off by
    default (``addopts``) and, when a maintainer explicitly runs
    ``pytest -m sandbox``, read real consumer credentials and proxy targets
    from the ambient environment (``tests/sandbox/conftest.py``) — clearing
    them here would defeat the whole suite."""
    if request.node.get_closest_marker("sandbox") is not None:
        return
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fresh_default_runtime() -> Iterator[None]:
    """Drop the process-default runtime after each test (#725), so a test that
    sets env vars and calls a module-level factory never sees a runtime built
    from an earlier test's environment."""
    yield
    runtime.close_default()
