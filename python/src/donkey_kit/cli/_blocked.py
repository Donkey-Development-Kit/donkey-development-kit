"""The hidden, verification-blocked ``donkey`` command (§0.3, BG §2.5).

``status`` is scaffolded for the Exchange publication flow (BG §2.5). The reads
behind "published" and "governed" are verified, but "reachable" has no ledger
row, so the command waits on it. It prints a
``blocked on verification`` message and exits 3, so a script wrapping the CLI
can tell "blocked" from "failed". It is hidden from ``--help`` so the CLI never
advertises a capability it lacks. ``verify`` was here until #1352 wired it
(:mod:`.verify`).
"""

from __future__ import annotations

import typer

from ._app import app

__all__ = ["status"]


def _exit_blocked(what: str) -> None:
    typer.secho(f"blocked on verification: {what}", fg="yellow", err=True)
    typer.secho(
        "See docs/verified-apis.md — this command is scaffolded but not wired until the underlying "
        "platform API is confirmed against a sandbox (verification discipline).",
        err=True,
    )
    raise typer.Exit(3)


@app.command(hidden=True)
def status() -> None:
    """Render published / reachable / governed per asset (BG §2.5)."""
    _exit_blocked(
        "a per-asset 'reachable' check (BG §2.5). The Exchange and API Manager reads "
        "behind 'published' and 'governed' are verified, and `donkey verify` makes them today"
    )
