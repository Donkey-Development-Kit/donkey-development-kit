"""OpenAI Agents SDK support-triage demo — Scenario A, end to end, no gateway (BG §1.8, #223).

The second deep adapter's worked demo, the counterpart of
``examples/langgraph/main.py``: ``pip install`` to a drafted reply against the
**local simulator only** — no Anypoint credentials, no real gateway — through
the Agents SDK's own ``Runner.run``. Two things live here:

* :func:`build` — a conformance-able agent factory. Given a :class:`Donkey`, it
  hands the governed model to an ``agents.Agent`` and returns an object with an
  awaitable ``run(text)``. This is exactly the shape the customer-facing
  conformance plugin drives
  (``pytest --donkey-conformance --donkey-agent=examples.openai_agents.main:build``),
  and the SDK's own ``tests/conformance/test_openai_agents_conformance.py`` runs
  the four scenarios against it.
* :func:`main` — the runnable Scenario-A demo (see below). Timed in CI (the
  ``openai-agents-demo`` job) so the documented first-run experience can never
  silently rot.

What Scenario A demonstrates:

* **Runs with no gateway.** :func:`_boot_simulator` boots the local gateway
  simulator (BG §1.4) in-process on an ephemeral port; the SDK points at it via
  ``DONKEY_LLM_PROXY_URL``. The simulator ignores auth, so the credentials are
  throwaway placeholders.
* **PII masking branch.** The simulator runs the ``pii_block:every=5`` scenario
  (#188): every 5th model call (here ``POST /chat/completions``, the route
  ``OpenAIChatCompletionsModel`` calls) is served the ``pii-detected`` 403. The
  demo triages five support tickets, so the fifth — which we deliberately fill
  with an SSN — is blocked. :meth:`RunnerAgent.run` wraps ``Runner.run`` in
  :func:`~donkey_kit.typed_refusals`, so the refusal surfaces as a typed
  ``PIIDetected``, not the OpenAI client's generic ``PermissionDeniedError``.
  (Honest note: the simulator triggers on the *count*, not by scanning content
  — it is a fixture replay, not a PII detector. The gateway does the real
  detection; here the SSN just makes the blocked ticket read true.)
* **Spans to a local OTLP collector.** :class:`~examples.openai_agents.collector.
  LocalOTLPCollector` (bundled, the same stdlib receiver the LangGraph demo
  uses) terminates OTLP/HTTP on a local port. Setting
  ``OTEL_EXPORTER_OTLP_ENDPOINT`` to it lights up the SDK's zero-config export
  (#194) — every governed call's span flows over the wire and is counted,
  refusals included. The Agents SDK's own tracing stays off: its default
  exporter posts to OpenAI, not the proxy.
* **Correlation reaches the agent.** The agent's dynamic instructions log
  ``current_correlation_id()`` without it ever being passed through the run
  context; the run id bound with ``donkey.run(id=…)`` shows up there for free
  because ``Runner.run`` runs them inside the caller's context. Every model
  request carries it too, on the shared transport.

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route) and §8 records the
``OpenAIChatCompletionsModel`` constructor and its kwargs. ``Agent``/``Runner``
are the framework's own classes and ``Runner.run`` is its documented API — the
SDK builds the model; everything after is the framework's own runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agents import Agent, RunConfig, RunContextWrapper, Runner, RunResult

from donkey_kit import Donkey, PIIDetected, typed_refusals
from donkey_kit.core.telemetry import current_correlation_id

if TYPE_CHECKING:
    from .collector import LocalOTLPCollector

logger = logging.getLogger("examples.openai_agents")

# Five support tickets. The fifth carries an SSN so the blocked call — the
# ``pii_block:every=5`` scenario fires on the 5th model call — reads as a real
# PII refusal rather than an arbitrary one.
TICKETS: tuple[str, ...] = (
    "The mobile app crashes when I tap Export on the reports screen.",
    "I was charged twice for my October subscription — can you refund one?",
    "How do I invite a teammate to my workspace?",
    "The password-reset email never arrives; I checked spam.",
    "Please update my account: my SSN is 123-45-6789 and DOB 1985-02-14.",
)

# Tracing off: the Agents SDK's default trace exporter posts to OpenAI, not the
# proxy. The governed spans come from the SDK's own transport instead.
_RUN_CONFIG = RunConfig(tracing_disabled=True)


class RunnerAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through ``agents.Runner``."""

    def __init__(self, agent: Agent[Any]) -> None:
        self._agent = agent

    async def run(self, text: str) -> RunResult:
        # A proxy refusal escapes Runner.run as the OpenAI client's status
        # error; typed_refusals() re-raises it as the SDK's typed DonkeyError.
        async with typed_refusals():
            return await Runner.run(self._agent, text, run_config=_RUN_CONFIG)


def _instructions(_ctx: RunContextWrapper[Any], _agent: Agent[Any]) -> str:
    """Dynamic instructions, resolved by ``Runner.run`` on every turn. Logs the
    run's correlation id, read from the contextvar rather than the run context —
    proving it propagated into the agent on its own."""
    logger.info("triage: preparing", extra={"correlation_id": current_correlation_id()})
    return "You draft one-line replies to customer support tickets."


def build(donkey: Donkey) -> RunnerAgent:
    """Hand the governed model to an ``agents.Agent`` and return the agent. Uses
    ``donkey.openai_agents`` (not the module-level factory) so the model shares
    this ``Donkey``'s HTTP client — which is what lets the conformance harness
    swap a fixture transport in and have the agent observe it."""
    agent: Agent[Any] = Agent(
        name="triage",
        instructions=_instructions,
        model=donkey.openai_agents.model(os.environ.get("DEMO_MODEL", "gpt-4o")),
    )
    return RunnerAgent(agent)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    sock.close()
    return port


def _boot_simulator(scenarios: list[str]) -> tuple[str, Callable[[], None]]:
    """Boot the local gateway simulator with ``scenarios`` on an ephemeral port
    in a daemon thread. Returns its base URL and a shutdown callable. Mirrors the
    quickstart's boot (#203); the only addition is passing a scenario set."""
    import uvicorn

    from donkey_kit.simulator import SimulatorConfig, build_app
    from donkey_kit.simulator.scenarios import parse_scenarios

    config = SimulatorConfig(scenarios=parse_scenarios(scenarios))
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(build_app(config), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10.0
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5.0)
        raise RuntimeError("local gateway simulator did not start within 10s")

    def _shutdown() -> None:
        server.should_exit = True
        thread.join(timeout=10.0)

    return f"http://127.0.0.1:{port}", _shutdown


def _flush_spans() -> None:
    """Force the batch span processor to export now, so the collector has the
    spans before we read its tally (a no-op if export was never configured)."""
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        flush()


def _draft_text(result: RunResult) -> str:
    """The agent's final text, truncated so the demo output stays one line per
    ticket."""
    text = str(result.final_output).strip()
    return text if len(text) <= 80 else text[:77] + "…"


async def _triage(donkey: Donkey) -> int:
    """Run the five tickets through the agent, printing a draft for each and the
    typed refusal for the PII-laden one. Returns the number of tickets blocked."""
    agent = build(donkey)
    blocked = 0
    for i, ticket in enumerate(TICKETS, start=1):
        # A run id per ticket; it reaches the agent's instructions via the contextvar.
        async with donkey.run(id=f"ticket-{i}"):
            try:
                result = await agent.run(f"Draft a one-line support reply to: {ticket}")
            except PIIDetected as refusal:
                blocked += 1
                print(f"  ticket {i}: BLOCKED by policy — PIIDetected ({refusal.policy})")
                continue
        print(f"  ticket {i}: {_draft_text(result)}")
    return blocked


def _collector_cls() -> type[LocalOTLPCollector]:
    """Import the bundled collector. Relative import when run as a package module
    (``python -m examples.openai_agents.main``); a by-path fallback when this file
    is run directly as a script, so both entry points work. ``build()`` never
    needs it, so keeping this out of the module top lets the conformance harness
    load ``main.py`` by file path without dragging the collector in."""
    try:
        from .collector import LocalOTLPCollector
    except ImportError:
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "ddk_example_openai_agents_collector", Path(__file__).with_name("collector.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        loaded: type[LocalOTLPCollector] = module.LocalOTLPCollector
        return loaded
    return LocalOTLPCollector


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    started = time.perf_counter()

    with _collector_cls()() as collector:
        # Zero-config export (#194): setting the standard endpoint is all it
        # takes — Donkey.from_env() builds the OTLP exporter for us. By default
        # that exporter stays DDK-scoped and the global provider is left alone
        # (#732); this demo opts in to the global install only because it
        # flushes through trace.get_tracer_provider() before reading the tally.
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = collector.endpoint
        os.environ["DONKEY_TELEMETRY_INSTALL_GLOBAL"] = "true"

        base_url, shutdown = _boot_simulator(["pii_block:every=5"])
        # The simulator ignores auth; these are throwaway placeholders, never
        # real credentials. from_env() still requires the header pair to exist.
        os.environ["DONKEY_LLM_PROXY_URL"] = base_url
        os.environ.setdefault("DONKEY_LLM_PROXY_CLIENT_ID", "local")
        os.environ.setdefault("DONKEY_LLM_PROXY_CLIENT_SECRET", "local")

        try:
            print("Triaging 5 support tickets (no gateway, simulator only):")
            with Donkey.from_env() as donkey:
                blocked = asyncio.run(_triage(donkey))
                print("\nBudget remaining:", donkey.budget.remaining, "tokens")
            _flush_spans()
        finally:
            shutdown()

        spans = collector.sink.span_count
        print(f"Spans exported to the local OTLP collector: {spans}")
        if collector.sink.span_names:
            print("  span names:", sorted(set(collector.sink.span_names)))

    # The demo is its own smoke test: the PII-laden ticket must be blocked, and
    # every governed call — the refusal included — must put a span on the wire.
    # A regression that breaks either fails the CI ``openai-agents-demo`` job.
    if blocked < 1:
        raise SystemExit("expected the PII-laden ticket to be blocked, none were")
    if spans < len(TICKETS):
        raise SystemExit(
            f"expected a span per governed call (>= {len(TICKETS)}), collector saw {spans}"
        )

    print(f"\nDone in {time.perf_counter() - started:.2f}s — no gateway, no credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
