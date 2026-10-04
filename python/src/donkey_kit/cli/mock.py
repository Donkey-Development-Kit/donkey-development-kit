"""``donkey mock``: run the local gateway simulator (BG §1.4)."""

from __future__ import annotations

import typer

from ._app import app

__all__ = ["mock"]


@app.command()
def mock(
    port: int = typer.Option(8080, "--port", help="TCP port to bind"),
    host: str = typer.Option("127.0.0.1", "--host", help="host/interface to bind"),
    scenario: list[str] = typer.Option(
        [],
        "--scenario",
        help=(
            "Fault-injection rule, repeatable. e.g. 'pii_block:every=5', "
            "'budget:limit=20000,window=60s', 'injection:on-pattern=ignore previous'."
        ),
    ),
) -> None:
    """Run the local gateway simulator (BG §1.4).

    Serves the same captured fixtures ``core.errors.classify()`` is tested
    against, so a stock client pointed at ``DONKEY_LLM_PROXY_URL=http://{host}:{port}``
    sees the real rejection shapes locally. Every response carries
    ``x-donkey-simulator: true`` — it is a fixture replay, never a real gateway.

    It REPLAYS captured shapes; it does NOT evaluate policy. It tests how your
    agent handles a refusal, never which prompts get refused — you choose the
    refusal (the ``donkey-sim/<shape>`` model sentinel or a ``--scenario`` rule),
    the simulator does not decide it. Testing against real policy configuration
    needs gateway-side dry-run mode (#250).

    ``--scenario`` scripts a specific failure on demand (#188): ``pii_block``
    fails every Nth call, ``injection`` matches request text, and ``budget``
    runs a real windowed token counter (429 on exhaustion). Repeatable.

    Needs the ``[local]`` extra (starlette + uvicorn); this is NOT one of the
    verification-gated platform commands, so a missing extra is an install
    prompt (exit 1), not a ``blocked on verification`` message (exit 3).
    """
    from ..simulator import server
    from ..simulator.app import SimulatorConfig
    from ..simulator.scenarios import ScenarioError, parse_scenarios

    try:
        scenarios = parse_scenarios(scenario)
    except ScenarioError as exc:
        typer.secho(f"Invalid --scenario: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    config = SimulatorConfig(scenarios=scenarios) if scenarios else None
    try:
        server.serve(host=host, port=port, config=config)
    except ImportError as exc:
        typer.secho(
            'The local gateway simulator needs the [local] extra. Install it with:\n'
            '    pip install "donkey-kit[local]"',
            fg="yellow",
            err=True,
        )
        raise typer.Exit(1) from exc
