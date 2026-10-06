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

from donkey_kit.core import _verify, runtime, telemetry, toolspec
from donkey_kit.integrations import _base

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
# CI harness switches that share the DONKEY_ prefix but are not SDK config:
# the SDK never reads them, and the test that does reads them on purpose.
# ``DONKEY_CONTRACT_EXTRA`` is the ``adapter-contract`` CI leg's "this
# framework must be installed, never skip" switch (#742,
# tests/conformance/test_adapter_contract.py); clearing it would turn a broken
# framework install back into a silent skip.
_HARNESS_VARS = frozenset({"DONKEY_CONTRACT_EXTRA"})

# LiteLLM (pulled in by ADK's ``model()`` and CrewAI) fetches its model cost
# map from raw.githubusercontent.com at import time, which pytest-socket's
# loopback-only ``--allow-hosts`` blocks (#747). This switch makes it load the
# copy bundled in the wheel instead. It is read once at import, so it is set
# here at conftest load, before any test can import litellm.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Exempt ``@pytest.mark.sandbox`` tests from pytest-socket's loopback-only
    ``--allow-hosts`` (``addopts``, #747). Those tests call the real provisioned
    proxies by design, so under the global restriction every one of them would
    fail with ``SocketConnectBlockedError`` the moment a maintainer runs
    ``pytest -m sandbox`` with ``DONKEY_SANDBOX_TESTS=1``. ``enable_socket`` is
    pytest-socket's own per-test override and takes precedence over
    ``--allow-hosts``."""
    for item in items:
        if item.get_closest_marker("sandbox") is not None:
            item.add_marker(pytest.mark.enable_socket)


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """Repeat pytest-randomly's seed at the end of the run (#750).

    pytest-randomly reports the seed only in the session header, which ``-q``
    suppresses, and most CI jobs run ``pytest -q``. Printing it here too lets
    any job's order-dependent failure be replayed with ``--randomly-seed``."""
    if config.pluginmanager.hasplugin("randomly"):
        seed = config.getoption("randomly_seed")
        msg = f"pytest-randomly seed: {seed} (replay: --randomly-seed={seed})"
        terminalreporter.write_line(msg)


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
    CI harness switches in ``_HARNESS_VARS`` are kept.

    ``@pytest.mark.sandbox`` is the opt-in marker: those tests are off by
    default (``addopts``) and, when a maintainer explicitly runs
    ``pytest -m sandbox``, read real consumer credentials and proxy targets
    from the ambient environment (``tests/sandbox/conftest.py``) — clearing
    them here would defeat the whole suite. The same marker also lifts the
    loopback-only socket restriction (``pytest_collection_modifyitems``)."""
    if request.node.get_closest_marker("sandbox") is not None:
        return
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES) and name not in _HARNESS_VARS:
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fresh_default_runtime() -> Iterator[None]:
    """Drop the process-default runtime after each test (#725), so a test that
    sets env vars and calls a module-level factory never sees a runtime built
    from an earlier test's environment."""
    yield
    runtime.close_default()


@pytest.fixture(autouse=True)
def _reset_module_state() -> Iterator[None]:
    """Reset every module-level cache/flag the SDK keeps for process lifetime,
    before AND after each test (#750).

    Without this, whichever test reads an :class:`Unverified` placeholder,
    builds a module-level adapter default, or configures OTLP export first
    "spends" that module-level state for every test after it — so results
    depend on collection order (reverse order, a new test, or a random seed can
    all flip a previously-passing assertion). Reset before the test too, so a
    test's own ``pytest.warns``/cache assertions never depend on what an
    earlier test happened to leave behind. Individual tests must not clear
    these by hand; add a new module's ``_reset_for_tests()`` here instead."""
    _verify._reset_for_tests()
    _base._reset_for_tests()
    telemetry._reset_for_tests()
    toolspec._reset_for_tests()
    yield
    _verify._reset_for_tests()
    _base._reset_for_tests()
    telemetry._reset_for_tests()
    toolspec._reset_for_tests()
