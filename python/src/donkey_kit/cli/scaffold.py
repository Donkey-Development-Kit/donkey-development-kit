"""``donkey scaffold`` — candidate ``.donkey-kit.toml`` declarations for the
services a static scan finds (#1747, milestone 28).

A front end over :func:`donkey_kit.registry.scaffold.build_scaffold`: static
analysis only, so it never imports the code it scans, and it reads the
committed project ``.donkey-kit.toml`` alone (no local overlay, user file or
environment), so the same commit always gives the same output.

``--dry-run`` prints the candidate tables as TOML on stdout and a summary on
stderr; with ``--json``, one document holding both. Unfilled placeholders are
reported, not failures: it exits 0 with them, 1 when the scan reports an
issue, and 2 for a path that does not exist.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..core.config import TOML_NAME
from ..registry.scaffold import Scaffold, build_scaffold
from ._app import app

__all__ = ["scaffold"]


@app.command()
def scaffold(
    ctx: typer.Context,
    path: Path = typer.Argument(Path("."), help="The repository (or one .py file) to scan."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help=f"print the candidate tables instead of writing {TOML_NAME}"
    ),
    expose: bool = typer.Option(
        False, "--expose", help="also propose the [donkey.expose] tables `donkey expose` reads"
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the scaffold as one JSON document"),
) -> None:
    """Propose the .donkey-kit.toml tables the scanned services still need."""
    as_json = as_json or bool((ctx.obj or {}).get("json"))
    if not dry_run:
        typer.secho(
            f"donkey scaffold cannot write {TOML_NAME} yet; preview with --dry-run",
            fg="red",
            err=True,
        )
        raise typer.Exit(2)
    try:
        result = build_scaffold(path, expose=expose)
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    if as_json:
        typer.echo(result.to_json(), nl=False)
    else:
        typer.echo(result.to_toml(), nl=False)
        _summary(result)
    raise typer.Exit(1 if result.issues else 0)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _summary(result: Scaffold) -> None:
    """What the preview holds and what is left to do, on stderr so stdout is
    the TOML alone."""
    if result.candidates:
        typer.echo(
            f"{_plural(len(result.candidates), 'table')} for {result.destination}, "
            f"{_plural(result.unresolved, 'placeholder')} to fill in",
            err=True,
        )
    else:
        typer.echo(f"nothing to add to {result.destination}", err=True)
    if result.declared:
        typer.echo(f"  already declared  {', '.join(result.declared)}", err=True)
    if result.unattributed:
        typer.echo(
            f"  unattributed  {', '.join(result.unattributed)}  "
            "(set @donkey.tool(server=...) to give each an MCP server)",
            err=True,
        )
    if result.issues:
        typer.secho(_plural(len(result.issues), "issue"), fg="red", err=True)
        for i in result.issues:
            typer.secho(f"  {i.path}:{i.line}  {i.kind}  {i.subject}: {i.detail}", err=True)
