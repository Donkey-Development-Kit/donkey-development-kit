"""``donkey test``: run the conformance suite against your agent (BG §1.5)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys

import typer

from ._app import app

__all__ = ["test"]


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def test(
    ctx: typer.Context,
    agent: str | None = typer.Option(
        None,
        "--agent",
        metavar="MODULE:FACTORY",
        help="Import path to your agent factory, e.g. my.pkg:make_agent.",
    ),
) -> None:
    """Run the conformance suite against your agent (BG §1.5).

    A thin front end to ``pytest --donkey-conformance`` — it does not
    re-implement the runner. Your ``--agent MODULE:FACTORY`` and any trailing
    pytest arguments pass straight through, and pytest's exit code becomes
    ``donkey test``'s own, so it drops into CI unchanged. Needs the ``[test]``
    extra (a missing pytest is an install prompt, exit 1, not a ``blocked on
    verification`` message).
    """
    if importlib.util.find_spec("pytest") is None:
        typer.secho(
            'donkey test needs the [test] extra. Install it with:\n'
            '    pip install "donkey-kit[test]"',
            fg="yellow",
            err=True,
        )
        raise typer.Exit(1)

    argv = [sys.executable, "-m", "pytest", "--donkey-conformance"]
    if agent:
        argv += ["--agent", agent]
    argv += list(ctx.args)

    completed = subprocess.run(argv, check=False)
    raise typer.Exit(completed.returncode)
