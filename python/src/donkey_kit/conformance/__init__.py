"""The customer-facing conformance kit (#191, `BG §1.5`).

The public, run-it-against-*your*-agent sibling of the SDK's internal adapter
matrix (``tests/conformance/``). Install ``donkey-kit[test]`` and run::

    pytest --donkey-conformance --agent=my_app.agent:build

- :mod:`~donkey_kit.conformance.suite` — the four scenarios and their types.
- :mod:`~donkey_kit.conformance.harness` — the observable ``Donkey`` the
  scenarios run against, plus :func:`run_conformance` for programmatic use.
- :mod:`~donkey_kit.conformance.report` — the dependency-free status table.
- :mod:`~donkey_kit.conformance.plugin` — the pytest11 plugin (auto-loaded).

**Dev-only sibling of the five production layers** (the layered architecture): an import-linter
contract forbids ``core``/``llm``/``registry``/``tools``/``integrations`` from
importing it. Because the pytest11 entry point auto-loads
:mod:`~donkey_kit.conformance.plugin` on *every* pytest run where this package
is installed — including the base-only job — only the framework-free
``suite``/``report`` submodules are imported at package top. The harness (which
imports :class:`~donkey_kit.donkey.Donkey`) is exposed lazily via
:func:`__getattr__`, so ``import donkey_kit.conformance`` never eagerly drags
the full SDK — or a framework — onto the base import path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .report import StatusCounts, count_statuses, render_report
from .suite import (
    PROBE_CORRELATION_ID,
    SCENARIO_NAMES,
    SCENARIOS,
    Observation,
    Outcome,
    Result,
    Scenario,
    ScenarioContext,
    Status,
    validate_known_limitations,
)

if TYPE_CHECKING:  # for type-checkers only; not imported at runtime module top
    from .harness import (
        ConformanceHarness,
        ConformanceUsageError,
        run_conformance,
    )

__all__ = [
    "PROBE_CORRELATION_ID",
    "SCENARIOS",
    "SCENARIO_NAMES",
    "ConformanceHarness",
    "ConformanceUsageError",
    "Observation",
    "Outcome",
    "Result",
    "Scenario",
    "ScenarioContext",
    "Status",
    "StatusCounts",
    "count_statuses",
    "render_report",
    "run_conformance",
    "validate_known_limitations",
]

# The harness pulls in Donkey; import it only when actually asked for, so the
# auto-loaded plugin path stays light (mirrors the lazy-adapter discipline).
_LAZY = {"ConformanceHarness", "ConformanceUsageError", "run_conformance"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from . import harness

        return getattr(harness, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
