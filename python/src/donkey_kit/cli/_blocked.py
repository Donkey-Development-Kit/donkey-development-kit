"""The hidden, verification-blocked ``donkey`` commands (§0.3, BG §2.5).

``status``, ``publish`` and ``verify`` are scaffolded for the Exchange
publication flow (BG §2.5), which is in scope but waits on a verified platform
API. Each prints a ``blocked on verification`` message and exits 3, so a script
wrapping the CLI can tell "blocked" from "failed". They are hidden from
``--help`` so the CLI never advertises a capability it lacks.
"""

from __future__ import annotations

import typer

from ._app import app

__all__ = ["publish", "status", "verify"]


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
    _exit_blocked("Exchange + API Manager read APIs (BG §2.5)")


@app.command(hidden=True)
def publish(if_changed: bool = typer.Option(True, "--if-changed/--always")) -> None:
    """Publish code-first assets to Exchange (CI-only, BG §2.5)."""
    _exit_blocked("Exchange publication mechanism + digest metadata (BG §2.5)")


@app.command(hidden=True)
def verify() -> None:
    """Check the live server against the Exchange descriptor (BG §2.5)."""
    _exit_blocked("Exchange descriptor read + live introspection (BG §2.5)")
