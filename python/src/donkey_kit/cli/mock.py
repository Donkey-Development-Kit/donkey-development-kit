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
            "'budget:limit=20000,window=60s', 'request_limit:limit=3,window=60s', "
            "'injection:on-pattern=ignore previous', 'ciba:pending=2,then=approve'."
        ),
    ),
) -> None:
    """Run the local gateway simulator.

    Serves the captured gateway responses the SDK's error typing is tested
    against, so a stock client pointed at `DONKEY_LLM_PROXY_URL=http://{host}:{port}`
    sees the real rejection shapes locally. Every response carries
    `x-donkey-simulator: true`: it is a replay, never a real gateway.

    It replays captured shapes and does not evaluate policy. It tests how your
    agent handles a refusal, not which prompts get refused: you choose the
    refusal, with the `donkey-sim/<shape>` model name or a `--scenario` rule.

    `--scenario` scripts a failure on demand: `pii_block` fails every Nth call,
    `injection` matches request text, `budget` runs a windowed token counter
    (a 429 once spent), `request_limit` runs a request counter with the
    `x-ratelimit-*` headers (a 429 once spent), and `ciba` plays the OAuth2 OBO
    policy's step-up: `pending=N` calls get its 401, then the user approves
    (calls pass) or, with `then=deny`, denies (calls get its 403). Repeatable.

    Needs the \\[local] extra (starlette and uvicorn); without it, mock prints
    the install command and exits 1.
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
            "The local gateway simulator needs the [local] extra. Install it with:\n"
            '    pip install "donkey-kit[local]"',
            fg="yellow",
            err=True,
        )
        raise typer.Exit(1) from exc
