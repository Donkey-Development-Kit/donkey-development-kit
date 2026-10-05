"""Shared pytest configuration for the SDK's own test suite.

Enables the ``pytester`` fixture so ``tests/unit/test_conformance_plugin.py`` can
spawn throwaway pytest sessions that load our own ``pytest11`` plugin
(``donkey-kit[test]``, #191) and assert its end-to-end behaviour — inert
without the flag, a clean UsageError on misuse, a scenario table on success.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from donkey_kit.core import runtime
from donkey_kit.integrations import ADAPTERS

pytest_plugins = ["pytester"]


@pytest.fixture(autouse=True)
def _fail_unexpected_skip_for_contract_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """CI's ``adapter-contract-<extra>`` leg (``.github/workflows/ci.yml``) installs
    exactly one real framework and sets ``DONKEY_CONTRACT_EXTRA=<extra>`` (#742).
    ``tests/conformance/test_adapter_contract.py`` already turns its own skip into a
    failure for that framework; everywhere else in the suite still calls
    ``pytest.importorskip`` on that framework's modules and would silently SKIP
    instead of FAIL if the real install were ever broken — exactly the "never a
    silent skip" gap #748 closes. Patch ``pytest.importorskip`` for the duration of
    the leg's own framework modules only: every other framework's calls keep
    skipping normally (they are not installed in this leg and are not supposed to
    be)."""
    # A job that installs more than one framework (agents-strands-last-call) sets
    # a comma-separated list; the adapter-contract matrix (and every other
    # single-framework job) sets exactly one — ``test_adapter_contract.py``'s own
    # exact-equality check against a single extra is unaffected either way, since
    # it never runs in a job that sets more than one.
    raw = os.environ.get("DONKEY_CONTRACT_EXTRA", "")
    extras = {e.strip() for e in raw.split(",") if e.strip()}
    if not extras:
        return
    owned: set[str] = set()
    for spec in ADAPTERS.values():
        if spec.extra in extras:
            owned.update(spec.probe)
            owned.add(spec.attr)
    if not owned:
        return
    real_importorskip = pytest.importorskip

    def _strict_importorskip(modname: str, *args: Any, **kwargs: Any) -> Any:
        if modname in owned or any(modname.startswith(f"{root}.") for root in owned):
            try:
                return importlib.import_module(modname)
            except ImportError as exc:
                pytest.fail(
                    f"DONKEY_CONTRACT_EXTRA={extras!r} but importing {modname!r} "
                    f"failed ({exc}) — the real framework install is broken in "
                    "this CI leg (#748)."
                )
        return real_importorskip(modname, *args, **kwargs)

    monkeypatch.setattr(pytest, "importorskip", _strict_importorskip)


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
