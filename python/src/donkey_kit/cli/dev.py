"""``donkey dev``: serve an agent behind a simulated A2A ingress (BG §2.9.3, #1284)."""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any

import typer

from ._app import app

__all__ = ["dev"]


def _load(target: str) -> Any:  # noqa: ANN401 — whatever the user's module holds
    """Import ``module:attribute`` with the working directory on ``sys.path``,
    as ``uvicorn`` does, so ``donkey dev agent:handle`` finds ``./agent.py``."""
    module_name, sep, attribute = target.partition(":")
    if not sep or not module_name or not attribute:
        raise ValueError(f"TARGET must be 'module:attribute', not {target!r}")
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    obj: Any = importlib.import_module(module_name)
    for part in attribute.split("."):
        obj = getattr(obj, part)
    return obj


def _install_prompt(exc: ImportError) -> typer.Exit:
    typer.secho(
        "donkey dev needs the [a2a] and [local] extras. Install them with:\n"
        '    pip install "donkey-kit[a2a,local]"',
        fg="yellow",
        err=True,
    )
    return typer.Exit(1)


@app.command()
def dev(
    target: str = typer.Argument(
        ..., metavar="TARGET", help="The @donkey.agent handler, as module:attribute"
    ),
    port: int = typer.Option(8000, "--port", help="TCP port to bind"),
    host: str = typer.Option("127.0.0.1", "--host", help="host/interface to bind"),
    version: str = typer.Option("0.0.0", "--version", help="The agent version the card advertises"),
    scenario: list[str] = typer.Option(
        [],
        "--scenario",
        help=(
            "Inbound rejection rule, repeatable. e.g. 'pii_block:every=5', "
            "'request_limit:limit=3,window=60s', 'injection:on-pattern=ignore previous'."
        ),
    ),
) -> None:
    """Serve an agent over A2A behind a simulated gateway ingress (BG §2.9.3).

    Runs the app ``donkey.serve()`` runs, governed the same way, with a local
    fake ingress in front of it on one port. ``--scenario`` rules reject inbound
    calls (``SendMessage``/``SendStreamingMessage``) with the simulator's shared
    rejection fixtures, the bytes ``classify()`` and ``donkey mock`` use; every
    other request reaches the agent untouched. Every response carries
    ``x-donkey-simulator: true``.

    The ingress is SIMULATED: it enforces no policy. No A2A agent instance's
    rejections have been captured live yet, so the shapes are the LLM-proxy
    ones.

    Needs the ``[a2a]`` and ``[local]`` extras; a missing one is an install
    prompt (exit 1). A bad TARGET or ``--scenario`` is a usage error (exit 2).
    """
    from ..simulator.ingress import check_scenarios
    from ..simulator.scenarios import ScenarioError, parse_scenarios

    try:
        scenarios = parse_scenarios(scenario)
        check_scenarios(scenarios)
    except ScenarioError as exc:
        typer.secho(f"Invalid --scenario: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    try:
        handler = _load(target)
    except (ImportError, AttributeError, ValueError) as exc:
        typer.secho(f"Cannot load {target!r}: {exc}", fg="red", err=True)
        raise typer.Exit(2) from exc

    from ..a2a import build_app
    from ..donkey import Donkey
    from ..integrations import typed_refusals
    from ..simulator.ingress import build_ingress

    url = f"http://{host}:{port}/"
    donkey = Donkey()
    try:
        agent = build_app(
            handler,
            url=url,
            version=version,
            telemetry=donkey.config.telemetry,
            correlation_header=donkey.config.correlation_header,
            refusals=typed_refusals(),
        )
    except TypeError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    except ImportError as exc:
        raise _install_prompt(exc) from exc
    try:
        import uvicorn
    except ImportError as exc:
        raise _install_prompt(exc) from exc

    typer.echo(
        f"Agent {target} over A2A on {url}, behind a SIMULATED gateway ingress. "
        "Every response carries 'x-donkey-simulator: true'. Rejections replay the "
        "LLM-proxy fixtures, not shapes captured from an A2A instance. This is NOT a real "
        "gateway: no policy is enforced."
    )
    uvicorn.run(build_ingress(agent, scenarios), host=host, port=port, log_level="info")
