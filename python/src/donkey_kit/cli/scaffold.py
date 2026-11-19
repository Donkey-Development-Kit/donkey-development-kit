"""``donkey scaffold`` — candidate ``.donkey-kit.toml`` declarations for the
services a static scan finds (#1747, #1748, milestone 28).

A front end over :func:`donkey_kit.registry.scaffold.build_scaffold`: static
analysis only, so it never imports the code it scans, and it reads the
committed project ``.donkey-kit.toml`` alone (no local overlay, user file or
environment), so the same commit always gives the same output.

``donkey scaffold`` writes the candidate tables into the scanned root's
project file: it creates the file, or appends the missing tables after the
authored text, which is kept byte for byte (:meth:`Scaffold.apply`). The write
goes through :mod:`._files`: staged beside the file, published in one step,
never through a symlink, and refused if the file changed meanwhile. Nothing is
written when the scan reports an issue, and a run with nothing to add leaves
the file alone, so a second run is a no-op. The summary goes to stderr.

``--dry-run`` prints the candidate tables as TOML on stdout instead, and the
summary on stderr. ``--json`` prints one document holding both, and whether
anything was ``written``. Unfilled placeholders are reported, not failures: it
exits 0 with them, 1 when the scan reports an issue or the write is refused,
and 2 for a path that does not exist.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import typer

from ..core.config import TOML_NAME
from ..registry.scaffold import Scaffold, ScaffoldRefused, build_scaffold
from ..registry.scan import ScanIssue
from . import _files
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
    """Add the .donkey-kit.toml tables the scanned services still need."""
    as_json = as_json or bool((ctx.obj or {}).get("json"))
    try:
        result = build_scaffold(path, expose=expose)
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    destination = (path.parent if path.is_file() else path) / TOML_NAME
    existed = destination.exists()
    if not dry_run:
        result = _write(result, destination)
    if as_json:
        typer.echo(result.to_json(), nl=False)
    else:
        if dry_run:
            typer.echo(result.to_toml(), nl=False)
        _summary(result, destination, dry_run=dry_run, created=not existed)
    raise typer.Exit(1 if result.issues else 0)


def _write(result: Scaffold, destination: Path) -> Scaffold:
    """Write ``result``'s candidates to ``destination``, or say why not.

    Returns ``result`` marked written, or with a ``scaffold-refused`` issue
    added when the write was declined; the file is then untouched.
    """
    if result.issues or not result.candidates:
        return result
    try:
        _files.check_destination(destination)
        if destination.exists():
            before = destination.read_bytes()
            content = result.apply(before.decode("utf-8"))
            _files.replace(destination, content, expected=before)
        else:
            _files.create_new(destination, result.apply(None))
    except (_files.UnsafeDestination, _files.WriteConflict, ScaffoldRefused) as exc:
        return _refused(result, str(exc))
    except UnicodeDecodeError:
        return _refused(result, f"{destination} is not UTF-8")
    except OSError as exc:
        return _refused(result, f"{destination} could not be written ({exc.strerror or exc})")
    return dataclasses.replace(result, written=True)


def _refused(result: Scaffold, detail: str) -> Scaffold:
    issue = ScanIssue(
        "scaffold-refused", result.destination, result.destination, 0, f"{detail}; nothing written"
    )
    return dataclasses.replace(result, issues=(*result.issues, issue))


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _summary(result: Scaffold, destination: Path, *, dry_run: bool, created: bool) -> None:
    """What was proposed or written and what is left to do, on stderr so
    stdout is the TOML alone."""
    tables = _plural(len(result.candidates), "table")
    todo = f"{_plural(result.unresolved, 'placeholder')} to fill in"
    if not result.candidates:
        typer.echo(f"nothing to add to {destination}", err=True)
    elif dry_run:
        typer.echo(f"{tables} for {destination}, {todo}", err=True)
    elif result.written:
        verb = f"created {destination} with" if created else f"added to {destination}:"
        typer.echo(f"{verb} {tables}, {todo}", err=True)
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
        if not dry_run and result.candidates:
            typer.echo(f"nothing written to {destination}", err=True)
