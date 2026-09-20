"""The gateway-free 15-minute quickstart, as runnable code (#203, BG §1.4).

From ``pip install`` to a typed refusal caught in code, with **no Anypoint
credentials** and **no real gateway**. This script boots the local gateway
simulator (BG §1.4) in-process, points the SDK at it, and shows the three
things a bare ``base_url`` + headers cannot give you:

  1. a **span** — the ``donkey.llm.chat`` GenAI span, printed to the console;
  2. a **budget** object — ``donkey.budget``, updated from the response headers;
  3. a **typed refusal** — ``PIIDetected``, not a status code you parse by hand.

Run it::

    pip install "donkey-kit[llm,local,otel]"
    python examples/quickstart/main.py

It exits ``0`` on success and is timed in CI (the ``quickstart`` job) so the
documented first-run experience can never silently rot. The simulator ignores
auth — the ``client_id`` / ``client_secret`` below are throwaway placeholders,
never real credentials. This is a *fixture replay*, not a real gateway: every
response it serves carries ``x-donkey-simulator: true`` (BG §1.4).

The same code lives in ``website/content/quickstart.mdx`` — the two are kept in
lockstep so the documented snippet is exactly the executed one.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Callable


def _install_hint(extra: str, exc: ImportError) -> SystemExit:
    return SystemExit(
        f"This quickstart needs the [{extra}] extra. Install it with:\n"
        f'    pip install "donkey-kit[llm,local,otel]"\n'
        f"({type(exc).__name__}: {exc})"
    )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    sock.close()
    return port


def _boot_simulator() -> tuple[str, Callable[[], None]]:
    """Boot the local gateway simulator on an ephemeral port in a daemon thread.

    Returns its base URL and a shutdown callable. Mirrors the conformance suite's
    ``simulator_base_url`` fixture — the same out-of-process boot a stock client
    (or ``donkey.llm.client()``) points ``DONKEY_LLM_PROXY_URL`` at.
    """
    try:
        import uvicorn

        from donkey_kit.simulator import build_app
    except ImportError as exc:  # pragma: no cover - install-time guidance
        raise _install_hint("local", exc) from exc

    port = _free_port()
    config = uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
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


def _wire_console_tracing() -> None:
    """Send every span to the console so the ``donkey.llm.chat`` span is visible.

    The SDK emits spans only when OpenTelemetry is installed *and* a
    ``TracerProvider`` is configured; wiring a ``ConsoleSpanExporter`` here is all
    it takes to see one. ``core.telemetry`` reads the global provider when it opens
    each span, so this must run before the first governed call.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    except ImportError as exc:  # pragma: no cover - install-time guidance
        raise _install_hint("otel", exc) from exc

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)


def _run_demo() -> None:
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - install-time guidance
        raise _install_hint("llm", exc) from exc

    from donkey_kit import Donkey, PIIDetected
    from donkey_kit.core.errors import classify

    with Donkey.from_env() as donkey:
        # A real ``openai.OpenAI``, but pointed at the simulator and governed by
        # the Donkey transport — so the call auto-emits a span and updates budget.
        client = donkey.llm.client(sync=True)

        # (1) + (2): one happy call. The span prints to the console (above), and
        # ``donkey.budget`` is updated from the response's rate-limit header.
        reply = client.responses.create(model="gpt-5.1", input="Say hi in three words.")
        print("\nModel reply:", reply.output_text)
        print("Budget remaining:", donkey.budget.remaining, "tokens")

        # (3): a typed refusal. The ``donkey-sim/<shape>`` model id makes the
        # simulator replay a captured rejection body byte-for-byte; ``classify()``
        # turns the raw response into a typed exception you branch on.
        try:
            client.responses.create(
                model="donkey-sim/pii-detected",
                input="My SSN is 123-45-6789, please store it.",
            )
        except openai.APIStatusError as exc:
            governed = classify(exc.response)
            if not isinstance(governed, PIIDetected):  # pragma: no cover - defensive
                raise AssertionError(
                    f"expected PIIDetected, got {type(governed).__name__}"
                ) from None
            print("Blocked by policy: PIIDetected. Entities:", governed.entities)


def main() -> int:
    _wire_console_tracing()
    base_url, shutdown = _boot_simulator()

    # No Anypoint credentials: the simulator ignores auth, so these are throwaway
    # placeholders. ``Donkey.from_env()`` still requires the header pair to exist.
    os.environ["DONKEY_LLM_PROXY_URL"] = base_url
    os.environ.setdefault("DONKEY_LLM_PROXY_CLIENT_ID", "local")
    os.environ.setdefault("DONKEY_LLM_PROXY_CLIENT_SECRET", "local")

    started = time.perf_counter()
    try:
        _run_demo()
    finally:
        shutdown()
    print(f"\nDone in {time.perf_counter() - started:.2f}s — no gateway, no credentials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
