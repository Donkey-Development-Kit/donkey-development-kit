"""LangGraph support-triage demo — Scenario A, end to end, no gateway (BG §1.8, #199).

This is the Phase-1 acceptance artefact: ``pip install`` to a drafted reply
against the **local simulator only** — no Anypoint credentials, no real
gateway — while exercising the governance layer a bare ``base_url`` cannot give
you. Two things live here:

* :func:`build` — a conformance-able agent factory. Given a :class:`Donkey`, it
  wires a small two-node graph (``prepare`` → ``call_model``) and returns an
  object with an awaitable ``run(text)``. This is exactly the shape the
  customer-facing conformance plugin drives
  (``pytest --donkey-conformance --agent=examples.langgraph.main:build``), and
  the SDK's own ``tests/conformance/test_langgraph_conformance.py`` runs the
  four scenarios against it.
* :func:`main` — the runnable Scenario-A demo (see below). Timed in CI (the
  ``langgraph-demo`` job) so the documented first-run experience can never
  silently rot.

What Scenario A demonstrates (the #199 acceptance criteria):

* **Runs with no gateway.** :func:`_boot_simulator` boots the local gateway
  simulator (BG §1.4) in-process on an ephemeral port; the SDK points at it via
  ``DONKEY_LLM_PROXY_URL``. The simulator ignores auth, so the credentials are
  throwaway placeholders.
* **PII masking branch.** The simulator runs the ``pii_block:every=5`` scenario
  (#188): every 5th ``POST /responses`` is served the ``pii-detected`` 403. The
  demo triages five support tickets, so the fifth — which we deliberately fill
  with an SSN — is blocked. ``call_model`` wraps ``model.ainvoke`` in
  :func:`~donkey_kit.integrations.langgraph.typed_refusals`, so the refusal
  surfaces out of ``graph.ainvoke`` as a typed ``PIIDetected``, not a
  framework-wrapped generic error. (Honest note: the simulator triggers on the
  *count*, not by scanning content — it is a fixture replay, not a PII detector.
  The gateway does the real detection; here the SSN just makes the blocked
  ticket read true.)
* **Spans to a local OTLP collector.** :class:`~examples.langgraph.collector.
  LocalOTLPCollector` (bundled, ~40 lines of stdlib) terminates OTLP/HTTP on a
  local port. Setting ``OTEL_EXPORTER_OTLP_ENDPOINT`` to it lights up the SDK's
  zero-config export (#194) automatically — every governed call's span flows
  over the wire and is counted, refusals included.
* **Correlation reaches every node.** ``prepare`` logs
  ``current_correlation_id()`` without it ever being threaded through graph
  state; the run id bound with ``donkey.run(id=…)`` shows up there for free
  because LangGraph runs nodes on context-copying ``asyncio`` tasks (#195).

Honest status (§0.3/§8): the proxy *contract* (base URL, client_id/secret auth,
attribution headers) is live-verified. ``ChatOpenAI``/``StateGraph`` are the
frameworks' own classes and ``.ainvoke`` is their documented API — construction
via the SDK factory is the verified surface; everything after is the
frameworks' own runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from donkey_kit import Donkey, PIIDetected
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.langgraph import typed_refusals

logger = logging.getLogger("examples.langgraph")

# Five support tickets. The fifth carries an SSN so the blocked call — the
# ``pii_block:every=5`` scenario fires on the 5th ``POST /responses`` — reads as
# a real PII refusal rather than an arbitrary one.
TICKETS: tuple[str, ...] = (
    "The mobile app crashes when I tap Export on the reports screen.",
    "I was charged twice for my October subscription — can you refund one?",
    "How do I invite a teammate to my workspace?",
    "The password-reset email never arrives; I checked spam.",
    "Please update my account: my SSN is 123-45-6789 and DOB 1985-02-14.",
)


class State(TypedDict):
    """Graph state. The ``add_messages`` reducer accumulates turns across nodes,
    so a node that returns no message update (like ``prepare``) doesn't drop the
    conversation — untyped ``dict`` state would only carry the last node's
    return."""

    messages: Annotated[list, add_messages]


class TriageAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that pushes one turn through the compiled graph."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def run(self, text: str) -> Any:
        return await self._graph.ainvoke({"messages": [("user", text)]})


async def _prepare(_state: State) -> dict:
    """First node: emit the run's correlation id into our own logs (AC2). The id
    is read from the contextvar, never from graph state — proving it propagated
    into the node on its own."""
    logger.info("triage: preparing", extra={"correlation_id": current_correlation_id()})
    return {}


def build(donkey: Donkey) -> TriageAgent:
    """Wire the two-node graph against ``donkey``'s governed transport and return
    the agent. Uses ``donkey.langgraph`` (not the module-level factory) so the
    model shares this ``Donkey``'s HTTP client — which is what lets the
    conformance harness swap a fixture transport in and have the node observe
    it."""
    model = donkey.langgraph.chat_model(os.environ.get("DEMO_MODEL", "gpt-4o"))

    async def _call_model(state: State) -> dict:
        # A proxy refusal raised here comes back typed, not framework-wrapped (AC3).
        with typed_refusals():
            reply = await model.ainvoke(state["messages"])
        return {"messages": [reply]}  # add_messages appends to the running list

    builder = StateGraph(State)
    builder.add_node("prepare", _prepare)
    builder.add_node("call_model", _call_model)
    builder.set_entry_point("prepare")
    builder.add_edge("prepare", "call_model")
    builder.add_edge("call_model", END)
    return TriageAgent(builder.compile())


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


def _draft_text(reply: Any) -> str:
    """Pull the human-readable text out of a LangChain reply, whose ``content``
    is a plain string (Chat Completions) or a list of ``{type, text}`` segments
    (the responses API). Truncated so the demo output stays one line per ticket."""
    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        parts = [seg.get("text", "") for seg in content if isinstance(seg, dict)]
        content = " ".join(p for p in parts if p) or str(content)
    text = str(content).strip()
    return text if len(text) <= 80 else text[:77] + "…"


async def _triage(donkey: Donkey) -> int:
    """Run the five tickets through the agent, printing a draft for each and the
    typed refusal for the PII-laden one. Returns the number of tickets blocked."""
    agent = build(donkey)
    blocked = 0
    for i, ticket in enumerate(TICKETS, start=1):
        # A run id per ticket; it reaches every graph node via the contextvar.
        async with donkey.run(id=f"ticket-{i}"):
            try:
                result = await agent.run(f"Draft a one-line support reply to: {ticket}")
            except PIIDetected as refusal:
                blocked += 1
                print(f"  ticket {i}: BLOCKED by policy — PIIDetected ({refusal.policy})")
                continue
        print(f"  ticket {i}: {_draft_text(result['messages'][-1])}")
    return blocked


def _collector_cls() -> type:
    """Import the bundled collector. Relative import when run as a package module
    (``python -m examples.langgraph.main``); a by-path fallback when this file is
    run directly as a script, so both entry points work. ``build()`` never needs
    it, so keeping this out of the module top lets the conformance harness load
    ``main.py`` by file path without dragging the collector in."""
    try:
        from .collector import LocalOTLPCollector
    except ImportError:
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "ddk_example_langgraph_collector", Path(__file__).with_name("collector.py")
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.LocalOTLPCollector  # type: ignore[no-any-return]
    return LocalOTLPCollector


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    started = time.perf_counter()

    with _collector_cls()() as collector:
        # Zero-config export (#194): setting the standard endpoint is all it
        # takes — Donkey.from_env() installs the OTLP exporter for us.
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = collector.endpoint

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
    # A regression that breaks either fails the CI ``langgraph-demo`` job.
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
