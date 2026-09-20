"""The public pytest plugin — ``pytest --donkey-conformance`` (#191, `BG §1.5`).

This is the deliverable the milestone's centre of gravity moved to: not our
internal adapter matrix, but a suite a **customer runs against their own agent**::

    pip install "donkey-kit[test]"
    pytest --donkey-conformance --agent=my_app.agent:build

It registers via a ``pytest11`` entry point, so it auto-loads on *every* pytest
run in an environment where ``donkey-kit[test]`` is installed — including this
repo's own base-only job. That constraint drives the two rules this module
follows without exception:

1. **It is inert unless asked.** With no ``--donkey-conformance`` flag,
   :func:`pytest_collection` returns ``None`` and default collection runs
   untouched; the only footprint is three CLI options and one unused fixture.
2. **Its module top is import-light.** It imports only stdlib, ``pytest``, and
   the framework-free :mod:`~.suite`/:mod:`~.report` siblings. The heavy
   harness (which imports :class:`~donkey_kit.donkey.Donkey`) and ``asyncio``
   are imported lazily, inside the functions that need them, so merely loading
   the plugin never drags the full SDK — or a framework — onto the import path.

When the flag is set, ``--donkey-conformance`` runs the conformance suite
*exclusively* (it replaces normal collection), turning each scenario into one
pytest item and printing the scenario→status table in the terminal summary.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest

from .report import render_report
from .suite import SCENARIOS, Result, Scenario, validate_known_limitations

if TYPE_CHECKING:  # keep the heavy import out of the auto-loaded module body
    from .harness import AgentFactory

# Stash keys carry collection-time state to the run/summary phases without
# module globals (pytest recommends config.stash over ad-hoc attributes).
_FACTORY_KEY = pytest.StashKey[Callable[..., Any]]()
_KNOWN_KEY = pytest.StashKey[dict[str, str]]()
_RESULTS_KEY = pytest.StashKey[list[Result]]()


class _ConformanceFailure(AssertionError):
    """A scenario's FAIL verdict, rendered as the item's failure message with no
    Python traceback — the finding about the agent *is* the message."""


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("donkey", "Donkey conformance")
    group.addoption(
        "--donkey-conformance",
        action="store_true",
        default=False,
        dest="donkey_conformance",
        help="Run the Donkey conformance suite against your agent "
        "(requires --agent). Replaces normal test collection.",
    )
    group.addoption(
        "--agent",
        action="store",
        default=None,
        dest="donkey_agent",
        metavar="MODULE:FACTORY",
        help="Import path to your agent factory, e.g. my_app.agent:build. "
        "Called once per scenario; receives the donkey fixture if it declares one.",
    )
    group.addoption(
        "--donkey-known-limitations",
        action="store",
        default=None,
        dest="donkey_known_limitations",
        metavar="MODULE:NAME",
        help="Import path to a KNOWN_LIMITATIONS dict of {scenario: reason}. "
        "Defaults to a KNOWN_LIMITATIONS attribute in the --agent module if present.",
    )


def _import_target(spec: str, *, what: str) -> tuple[Any, Any]:
    """Resolve a ``module:attribute`` spec, raising :class:`pytest.UsageError`
    (a clean CLI error, no traceback) for a malformed spec, an unimportable
    module, or a missing attribute."""
    if ":" not in spec:
        raise pytest.UsageError(
            f"{what} must be given as 'module:attribute', got {spec!r}"
        )
    mod_name, _, attr = spec.partition(":")
    if not mod_name or not attr:
        raise pytest.UsageError(
            f"{what} must be given as 'module:attribute', got {spec!r}"
        )
    try:
        module = importlib.import_module(mod_name)
    except ImportError as exc:
        raise pytest.UsageError(f"could not import {mod_name!r} for {what}: {exc}") from exc
    try:
        return module, getattr(module, attr)
    except AttributeError as exc:
        raise pytest.UsageError(
            f"module {mod_name!r} has no attribute {attr!r} for {what}"
        ) from exc


def _resolve_agent(config: pytest.Config) -> tuple[AgentFactory, dict[str, str]]:
    """Resolve the ``--agent`` factory and its known-limitations mapping, and
    validate the exemptions **now** (collection time) so a bad key or an empty
    reason fails loudly before any scenario runs (the conformance kit: asserted, never silent)."""
    agent_spec = config.getoption("donkey_agent")
    if not agent_spec:
        raise pytest.UsageError(
            "--donkey-conformance requires --agent=module:factory "
            "(the import path to your agent factory)"
        )
    module, factory = _import_target(agent_spec, what="the agent factory (--agent)")
    if not callable(factory):
        raise pytest.UsageError(f"--agent target {agent_spec!r} is not callable")

    known_spec = config.getoption("donkey_known_limitations")
    if known_spec:
        _, known_obj = _import_target(
            known_spec, what="known limitations (--donkey-known-limitations)"
        )
    else:
        # Auto-discover: a KNOWN_LIMITATIONS attribute next to the factory.
        known_obj = getattr(module, "KNOWN_LIMITATIONS", None)

    try:
        known = validate_known_limitations(known_obj)
    except (TypeError, ValueError) as exc:
        raise pytest.UsageError(str(exc)) from exc
    return factory, known


class _ScenarioItem(pytest.Item):
    """One conformance scenario as a pytest item. All scenarios share a single
    :func:`~donkey_kit.conformance.harness.run_conformance` pass (memoised on
    the config); each item reports its own row's verdict."""

    def __init__(self, *, name: str, parent: Any, scenario: Scenario) -> None:
        super().__init__(name, parent)
        self._scenario = scenario

    def runtest(self) -> None:
        result = _result_for(self.config, self._scenario.name)
        if result.status == "fail":
            raise _ConformanceFailure(f"{self._scenario.title}: {result.detail}")
        # pass and exempt both leave the item green; the asserted exemption is
        # validated at collection and shown in the summary table, so it is
        # recorded, not silently skipped (the conformance kit).
        if result.status == "exempt":
            self.add_report_section("call", "exempt", result.detail)

    def repr_failure(self, excinfo: Any, style: Any = None) -> Any:
        if isinstance(excinfo.value, _ConformanceFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self) -> tuple[Any, int, str]:
        return self.path, 0, f"conformance: {self._scenario.title}"


class _ConformanceCollector(pytest.Collector):
    """A single synthetic collector so item node ids read
    ``donkey-conformance::<scenario>`` rather than dangling off the session."""

    def collect(self) -> Any:
        for scenario in SCENARIOS:
            yield _ScenarioItem.from_parent(self, name=scenario.name, scenario=scenario)


@pytest.hookimpl(tryfirst=True)
def pytest_collection(session: pytest.Session) -> bool | None:
    """When ``--donkey-conformance`` is set, replace normal collection with the
    conformance items. Returning ``None`` (the default) leaves ordinary
    collection untouched, so the plugin is inert on every other pytest run."""
    config = session.config
    if not config.getoption("donkey_conformance"):
        return None
    factory, known = _resolve_agent(config)  # UsageError here aborts cleanly
    config.stash[_FACTORY_KEY] = factory
    config.stash[_KNOWN_KEY] = known
    collector = _ConformanceCollector.from_parent(session, name="donkey-conformance")
    session.items = list(session.genitems(collector))
    session.testscollected = len(session.items)
    return True


def _result_for(config: pytest.Config, scenario_name: str) -> Result:
    """Return the :class:`Result` for one scenario, running the whole suite once
    (lazily, on the first item) and caching it on the config stash so every item
    and the terminal summary share the same single run."""
    results = config.stash.get(_RESULTS_KEY, None)
    if results is None:
        import asyncio

        from .harness import run_conformance

        factory = config.stash[_FACTORY_KEY]
        known = config.stash[_KNOWN_KEY]
        results = asyncio.run(run_conformance(factory, known_limitations=known))
        config.stash[_RESULTS_KEY] = results
    for result in results:
        if result.scenario == scenario_name:
            return result
    # Unreachable: every scenario has a row. Fail loudly rather than silently.
    raise KeyError(f"no conformance result for scenario {scenario_name!r}")


def pytest_terminal_summary(
    terminalreporter: Any, exitstatus: int, config: pytest.Config
) -> None:
    """Print the scenario→status table once, after the run. Only fires when the
    conformance suite actually ran (results are cached on the stash)."""
    results = config.stash.get(_RESULTS_KEY, None)
    if not results:
        return
    terminalreporter.write_line("")
    for line in render_report(results).splitlines():
        terminalreporter.write_line(line)


@pytest.fixture
def donkey() -> Any:
    """A :class:`~donkey_kit.donkey.Donkey` for tests that drive it directly
    (e.g. ``with donkey.simulate(PIIDetected): ...``).

    Built from the environment, with placeholder LLM credentials filled in only
    where absent — so ``donkey.openai()`` constructs and ``simulate()`` works
    with no live gateway and no env setup, while any real config you *have* set
    is preserved. Torn down synchronously; the async transport opens no
    connection unless a real request is made (which ``simulate()`` intercepts)."""
    from ..donkey import Donkey
    from .harness import _offline_config

    fab = Donkey(_offline_config())
    try:
        yield fab
    finally:
        fab.close()


@pytest.fixture
def gateway() -> Any:
    """A running local gateway simulator on an ephemeral port, for tests whose
    subject does **not** import ``donkey_kit`` — a containerised agent, a Node
    service, an A2A client, a manual ``curl`` (#278)::

        async def test_agent_stops_after_refusal(gateway):
            gateway.set_scenarios("pii_block:every=1")
            app = deploy(env={"DONKEY_LLM_PROXY_URL": gateway.url})
            await app.run(ticket)
            assert gateway.requests_received == 1  # stopped, did not retry

    It serves the same captured rejection fixtures ``simulate()`` and ``donkey
    mock`` replay, on a real port bound to ``0`` (so parallel ``pytest -n`` runs
    never collide), and records every request in a spy the test can assert on
    (:attr:`~.gateway.Gateway.requests_received` plus per-request method, path and
    headers with ``client_secret`` redacted). Torn down when the test exits,
    including on failure.

    Needs the ``[local]`` extra (``starlette`` + ``uvicorn``); taking the fixture
    without it raises an :class:`ImportError` naming the exact ``pip install``."""
    from .gateway import start_gateway

    gw = start_gateway()
    try:
        yield gw
    finally:
        gw.close()
