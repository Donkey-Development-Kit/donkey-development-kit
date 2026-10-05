"""Shared pytest configuration for the SDK's own test suite.

Enables the ``pytester`` fixture so ``tests/unit/test_conformance_plugin.py`` can
spawn throwaway pytest sessions that load our own ``pytest11`` plugin
(``donkey-kit[test]``, #191) and assert its end-to-end behaviour — inert
without the flag, a clean UsageError on misuse, a scenario table on success.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from donkey_kit.core import _verify, runtime, telemetry, toolspec
from donkey_kit.integrations import _base

pytest_plugins = ["pytester"]


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
def _no_ambient_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any OTLP endpoint the developer's or CI's shell exports, so a test
    that builds a ``Donkey`` never ships spans to that collector (#732). A test
    that needs an endpoint sets one itself."""
    for name in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
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
