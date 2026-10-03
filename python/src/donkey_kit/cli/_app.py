"""The shared ``typer`` app, the global flags, and the console-script entry point.

Every command module in :mod:`donkey_kit.cli` registers on :data:`app`; the
package ``__init__`` imports them so ``donkey_kit.cli:main`` sees the full set.
"""

from __future__ import annotations

from pathlib import Path

try:
    import typer
except ImportError as exc:
    # A curated ImportError, not SystemExit: importing this module must not
    # kill the interpreter (test collection, docs tooling), #811.
    raise ImportError(
        'The donkey CLI needs the [cli] extra. Install it with:\n'
        '    pip install "donkey-kit[cli]"'
    ) from exc

from ..core.config import LOCAL_TOML_NAME, TOML_NAME
from ..core.errors import DonkeyError

__all__ = ["app", "main"]

app = typer.Typer(
    add_completion=False,
    help="SDK for Agent Fabric — governed model access, refusals, budgets, and telemetry.",
)


@app.callback()
def _global(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None,
        "--config",
        metavar="PATH",
        help=f"Where `init` writes {TOML_NAME} (default: cwd). init only.",
    ),
    env: str | None = typer.Option(
        None,
        "--env",
        metavar="NAME",
        help="Anypoint environment `init` writes (e.g. Sandbox). init only.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON where the command supports it."
    ),
) -> None:
    """Global flags. Precede the subcommand: ``donkey --json init``,
    ``donkey --config ./cfg.toml init``.

    ``--config`` and ``--env`` only apply to ``init``. Any other command
    resolves config from env and the working directory's files, so it rejects
    them rather than silently diagnosing a different configuration (#811).
    Pointing the loader at an explicit file is #727."""
    command = ctx.invoked_subcommand
    if command != "init":
        given = [flag for flag, value in (("--config", config), ("--env", env)) if value]
        if given:
            typer.secho(
                f"{' and '.join(given)} only apply to `donkey init`; `donkey {command}` "
                "resolves config from environment variables and the working directory's "
                f"{TOML_NAME} / {LOCAL_TOML_NAME}. Set ANYPOINT_ENV or run from the "
                "directory holding the config file instead.",
                fg="red",
                err=True,
            )
            raise typer.Exit(2)
    ctx.obj = {"config": config, "env": env, "json": as_json}


def main() -> None:
    """The ``donkey`` console script. A :class:`DonkeyError` any command lets
    escape exits 1 with its message, never a traceback (#811)."""
    try:
        app()
    except DonkeyError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise SystemExit(1) from exc
