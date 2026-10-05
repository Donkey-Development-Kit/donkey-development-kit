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
from typing import Any

import pytest

from donkey_kit.core import runtime
from donkey_kit.integrations import ADAPTERS

pytest_plugins = ["pytester"]


def pytest_configure(config: pytest.Config) -> None:
    """Fail, never skip, an ``importorskip`` of a framework this CI job installs.

    The CI jobs that install a real framework (``.github/workflows/ci.yml``: the
    ``adapter-contract`` matrix and the per-framework stacks jobs) set
    ``DONKEY_CONTRACT_EXTRA`` to the extra(s) they install (#742). If that install
    were broken, every ``pytest.importorskip`` of the framework would SKIP and the
    job would still pass, which is the "never a silent skip" gap #748 closes. So
    for those modules only, a skip becomes a failure. Every other framework's
    calls keep skipping, because they are not installed in that job.

    The patch is installed here rather than in a fixture so that it also covers
    module-level ``importorskip`` calls, which run at collection time before any
    fixture (a module-level skip would otherwise drop the whole file silently).
    """
    # A job that installs two frameworks (agents-strands-last-call) sets a
    # comma-separated list. test_adapter_contract.py compares the variable to a
    # single extra itself, and only the adapter-contract matrix (one extra per
    # leg) relies on that.
    raw = os.environ.get("DONKEY_CONTRACT_EXTRA", "")
    extras = {e.strip() for e in raw.split(",") if e.strip()}
    owned: set[str] = set()
    for spec in ADAPTERS.values():
        if spec.extra in extras:
            owned.update(spec.probe)
            # The attribute name doubles as the top-level package for LangGraph,
            # whose graph-runtime modules (langgraph.graph, langgraph.types) are
            # not in its probe; for the other adapters it names no module and
            # never matches.
            owned.add(spec.attr)
    if not owned:
        return
    real_importorskip = pytest.importorskip

    def _strict_importorskip(modname: str, *args: Any, **kwargs: Any) -> Any:
        if modname in owned or any(modname.startswith(f"{root}.") for root in owned):
            try:
                return real_importorskip(modname, *args, **kwargs)
            except pytest.skip.Exception as exc:
                pytest.fail(
                    f"DONKEY_CONTRACT_EXTRA={raw!r} but importorskip({modname!r}) "
                    f"skipped ({exc}). The real framework install is broken in "
                    "this CI job (#748)."
                )
        return real_importorskip(modname, *args, **kwargs)

    patch = pytest.MonkeyPatch()
    patch.setattr(pytest, "importorskip", _strict_importorskip)
    config.add_cleanup(patch.undo)


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
