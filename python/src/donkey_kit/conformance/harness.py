"""The observable ``Donkey`` the conformance suite runs against (#191, `BG §1.5`).

The suite (:mod:`donkey_kit.conformance.suite`) says *what* to check; this
module owns *how* it is observed. For each scenario it builds a fresh ``Donkey``,
lets the customer's factory build an agent against it, arms the gateway to serve
one captured fixture for every send, runs ``agent.run(...)`` while capturing what
it raised and logged, and hands the resulting :class:`~.suite.Observation` to
the scenario's check.

It is the in-process sibling of the local gateway simulator, and it reuses the
simulator's machinery so the injected refusal is the *same captured bytes*
``classify()`` is tested against (:func:`donkey_kit.simulator.inject._resolve`
gives us that fixture and asserts the classify() round-trip). The one deliberate
difference from ``simulate()``'s transport: this one counts *every* wire send
rather than deduping retries, because counting re-sends is exactly how the retry
scenario is observed.

Framework isolation (the layered architecture): imports only ``httpx``, the framework-free
``core``/``donkey`` surface, and the sibling simulator loader — never a web
framework and never ``openai`` at module top. It is a dev-only simulator-style
sibling, kept out of the five production layers by an import-linter contract.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from typing import Any

import httpx

from ..core.config import DonkeyConfig
from ..core.errors import DonkeyError
from ..donkey import Donkey
from ..simulator.app import SIMULATOR_HEADER
from ..simulator.fixtures import Fixture, load, replay_headers

# Reuse the simulator's single exception->fixture mapping (and its classify()
# round-trip assertion) rather than duplicating the table: if the mapping ever
# drifts, simulate() and this harness fail together — the "same files" rule.
from ..simulator.inject import _resolve as _refusal_fixture
from .suite import SCENARIOS, Observation, Outcome, Result, Scenario, validate_known_limitations

__all__ = [
    "DEFAULT_RUN_INPUT",
    "ConformanceHarness",
    "ConformanceUsageError",
    "run_conformance",
]

# A neutral prompt handed to every agent under test. It is never actually sent
# upstream — the transport is swapped — but agents that inspect their input want
# a real string.
DEFAULT_RUN_INPUT = "This is a Donkey conformance probe. Reply briefly."

# Filled in only when the customer's environment does not already supply the LLM
# proxy config. The harness never connects (the transport is swapped for every
# scenario), so these just need to be non-empty to satisfy validated(need="llm").
_PLACEHOLDER = "donkey-conformance"
_PLACEHOLDER_URL = "https://donkey-conformance.invalid"

AgentFactory = Callable[..., Any]


class ConformanceUsageError(Exception):
    """The agent under test does not meet the harness contract (e.g. no callable
    ``run()``). A setup problem to fix, not a scenario finding — it is surfaced
    as a hard error, never a fail verdict."""


def _offline_config() -> DonkeyConfig:
    """The customer's own config, with placeholder LLM credentials filled in
    only where absent. Real config (base URL, model catalog) is preserved so the
    agent is built as it would be in production; the placeholders exist purely so
    ``donkey.openai()`` constructs without live credentials, since nothing here
    ever reaches the network."""
    cfg = DonkeyConfig.from_env()
    # Replace with named str fields (not **dict) so the types stay checkable;
    # ``x or <placeholder>`` keeps any value the customer has set and fills only
    # the ones they have not.
    return replace(
        cfg,
        llm_proxy_url=cfg.llm_proxy_url or _PLACEHOLDER_URL,
        llm_proxy_client_id=cfg.llm_proxy_client_id or _PLACEHOLDER,
        llm_proxy_client_secret=cfg.llm_proxy_client_secret or _PLACEHOLDER,
    )


def _fixture_responder(
    fixture: Fixture, *, strip_budget: bool = False
) -> Callable[[httpx.Request], httpx.Response]:
    """Build a responder that replays one captured fixture, honesty-stamped.

    Uses the same header allow-list (:func:`replay_headers`) and honesty stamp
    (``x-donkey-simulator: true``) as ``simulate()`` and the mock server, so a
    conformance response is never mistaken for a live gateway response. With
    ``strip_budget`` the ``x-token-*`` headers are dropped, so the run observes a
    genuinely unobserved budget even if a future capture grows them."""

    def respond(request: httpx.Request) -> httpx.Response:
        headers = replay_headers(fixture)
        if strip_budget:
            headers = {k: v for k, v in headers.items() if not k.startswith("x-token-")}
        if fixture.content_type is not None:
            headers["content-type"] = fixture.content_type
        headers[SIMULATOR_HEADER] = "true"
        return httpx.Response(
            status_code=fixture.status,
            headers=headers,
            content=fixture.body,
            request=request,
        )

    return respond


class _ProbeTransport(httpx.AsyncBaseTransport, httpx.BaseTransport):
    """Serves a fixed captured response for every send, counting wire sends.

    Deliberately does NOT dedupe by request identity the way ``simulate()``'s
    transport does: the retry scenario is observed by counting re-sends, so every
    ``handle_request`` increments :attr:`wire_sends`. Implements both transport
    protocols so one instance fronts a ``DonkeyClient`` and a
    ``DonkeyAsyncClient`` at once (and its count spans both)."""

    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self._responder = responder
        self.wire_sends = 0

    def _serve(self, request: httpx.Request) -> httpx.Response:
        self.wire_sends += 1
        return self._responder(request)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._serve(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._serve(request)


# Standard LogRecord attribute names, so :func:`_render_records` can tell an
# ``extra={...}`` field apart from the built-in ones and include it in the scan.
_STD_LOGRECORD_KEYS = frozenset(vars(logging.makeLogRecord({})).keys())


class _CaptureHandler(logging.Handler):
    """Collects every record emitted anywhere during a run, for the correlation
    scenario to grep."""

    def __init__(self, sink: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        self._sink.append(record)


def _render_records(records: list[logging.LogRecord]) -> str:
    """Flatten captured records into one searchable string: the formatted
    message plus the stringified value of every ``extra=`` field, so an id logged
    either in the message or via ``extra={"correlation_id": ...}`` is found."""
    parts: list[str] = []
    for record in records:
        try:
            parts.append(record.getMessage())
        except Exception:  # noqa: BLE001 — a bad format string must not abort capture
            parts.append(str(record.msg))
        parts.extend(
            str(value)
            for key, value in record.__dict__.items()
            if key not in _STD_LOGRECORD_KEYS
        )
    return "\n".join(parts)


def _build_agent(factory: AgentFactory, donkey: Donkey) -> Any:
    """Call the customer's factory, passing the ``Donkey`` iff it declares a slot
    for it — the app-factory / dependency-injection pattern (``gunicorn
    app:create_app``). ``build(donkey)`` and ``build(*, donkey=...)`` receive it;
    a zero-argument ``build()`` does not."""
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        # Un-introspectable (some builtins/C callables): assume it wants donkey.
        return factory(donkey)
    if "donkey" in params and params["donkey"].kind is inspect.Parameter.KEYWORD_ONLY:
        return factory(donkey=donkey)
    accepts_positional = any(
        p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for p in params.values()
    ) or any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in params.values())
    return factory(donkey) if accepts_positional else factory()


class ConformanceHarness:
    """Runs one scenario against a freshly built agent. Implements
    :class:`~.suite.ScenarioContext`: the scenario check calls
    :meth:`serve_refusal`/:meth:`serve_success` to arm the gateway and
    :meth:`run` to drive the agent."""

    def __init__(self, agent_factory: AgentFactory, *, run_input: str = DEFAULT_RUN_INPUT) -> None:
        self._factory = agent_factory
        self._run_input = run_input
        self._donkey: Donkey | None = None
        self._agent: Any = None
        self._probe: _ProbeTransport | None = None

    async def run_scenario(self, scenario: Scenario) -> Outcome:
        """Build a fresh ``Donkey`` + agent, run the scenario's check against
        this harness, and tear the ``Donkey`` down — so no transport swap, budget
        state, or agent state leaks between scenarios."""
        self._donkey = Donkey(_offline_config())
        self._probe = None
        try:
            self._agent = _build_agent(self._factory, self._donkey)
            return await scenario.check(self)
        finally:
            await self._donkey.aclose()
            self._donkey = None
            self._agent = None
            self._probe = None

    # --- ScenarioContext surface -------------------------------------------
    @property
    def agent(self) -> Any:
        return self._agent

    def serve_refusal(self, error: type[DonkeyError]) -> None:
        self._arm(_fixture_responder(_refusal_fixture(error)))

    def serve_success(self, *, budget_headers: bool = True) -> None:
        self._arm(_fixture_responder(load("success"), strip_budget=not budget_headers))

    async def run(self, *, correlation_id: str | None = None) -> Observation:
        agent = self._agent
        run = getattr(agent, "run", None)
        if not callable(run):
            raise ConformanceUsageError(
                f"the agent your factory returned has no callable run(...) method "
                f"(got {type(agent).__name__}); the conformance suite drives "
                f"agent.run(<input>). Adapt your agent or expose a run() shim."
            )
        records: list[logging.LogRecord] = []
        handler = _CaptureHandler(records)
        root = logging.getLogger()
        previous_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)  # let INFO/DEBUG records reach the handler
        raised: BaseException | None = None
        returned: Any = None
        bind = (
            self._donkey.run_context(correlation_id)
            if correlation_id is not None and self._donkey is not None
            else nullcontext()
        )
        try:
            with bind:
                result = run(self._run_input)
                returned = await result if inspect.isawaitable(result) else result
        except ConformanceUsageError:
            raise
        except Exception as exc:  # noqa: BLE001 — we observe whatever the agent lets escape
            raised = exc
        finally:
            root.removeHandler(handler)
            root.setLevel(previous_level)
        return Observation(
            returned=returned,
            raised=raised,
            wire_sends=self._probe.wire_sends if self._probe is not None else 0,
            log_text=_render_records(records),
        )

    def _arm(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        """Swap a counting probe onto both transports. The sync client is built
        eagerly (it opens no connection until used) so a blocking agent's client
        is armed too, and both share one probe so the wire-send count spans
        async and sync."""
        donkey = self._donkey
        assert donkey is not None  # set by run_scenario before any check runs
        self._probe = _ProbeTransport(responder)
        donkey._http._swap_transport(self._probe)
        donkey._sync_http_client()._swap_transport(self._probe)


async def run_conformance(
    agent_factory: AgentFactory,
    *,
    known_limitations: object = None,
    run_input: str = DEFAULT_RUN_INPUT,
) -> list[Result]:
    """Run every scenario against ``agent_factory`` and return the table rows.

    A scenario listed in ``known_limitations`` is reported ``exempt`` with its
    asserted reason and is *not* run (the exemption replaces the check); every
    other scenario runs and is reported ``pass``/``fail``. ``known_limitations``
    is validated up front, so a bad key or empty reason raises before any agent
    is built."""
    known = validate_known_limitations(known_limitations)
    results: list[Result] = []
    for scenario in SCENARIOS:
        if scenario.name in known:
            results.append(
                Result(scenario.name, scenario.title, "exempt", known[scenario.name])
            )
            continue
        harness = ConformanceHarness(agent_factory, run_input=run_input)
        outcome = await harness.run_scenario(scenario)
        results.append(
            Result(
                scenario.name,
                scenario.title,
                "pass" if outcome.passed else "fail",
                outcome.detail,
            )
        )
    return results
