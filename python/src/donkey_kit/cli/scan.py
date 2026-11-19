"""``donkey scan`` — the design-time model of a repository's agents, tools and
MCP servers (BG §2.5, #217).

A front end over :func:`donkey_kit.registry.scan.scan`: static analysis only, so
it never imports the code it scans. It prints a summary, or with ``--json`` the
canonical scan model for a CI diff. It writes no file. Each MCP server line
shows the transport and endpoint ``[donkey.mcp_servers]`` declares, or says
which is undeclared (#1193). It exits 1 when scan reports an issue (a missing
or tautological description, an invalid service ID, ...) so CI fails before
anything is published.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..core.config import TOML_NAME
from ..registry.remedy import scaffold_command, table_name
from ..registry.scan import ScannedMcpServer, ScanResult
from ..registry.scan import scan as scan_repository
from ._app import app

__all__ = ["scan"]


@app.command()
def scan(
    ctx: typer.Context,
    path: Path = typer.Argument(Path("."), help="The repository (or one .py file) to scan."),
    as_json: bool = typer.Option(False, "--json", help="emit the canonical scan model as JSON"),
) -> None:
    """Find @donkey.agent and @donkey.tool markers statically and report them."""
    as_json = as_json or bool((ctx.obj or {}).get("json"))
    try:
        result = scan_repository(path)
    except FileNotFoundError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    if as_json:
        typer.echo(result.to_json(), nl=False)
    else:
        _summary(result, path)
    raise typer.Exit(1 if result.issues else 0)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _reached(server: ScannedMcpServer, root: Path) -> str:
    """How the server is reached, or which key to declare where (#1193, #1750)."""
    table = table_name("donkey", "mcp_servers", server.id)
    hint = f"`{scaffold_command(root)}` adds the table if {TOML_NAME} lacks it"
    if server.transport is None:
        return f"transport undeclared (set transport in [{table}]; {hint})"
    if server.transport == "stdio":
        return "stdio"
    if server.endpoint is None:
        return f"{server.transport} endpoint undeclared (set endpoint in [{table}])"
    return f"{server.transport} {server.endpoint}"


def _summary(result: ScanResult, root: Path) -> None:
    for a in result.agents:
        typer.echo(f"  agent  {a.name}  ({a.path}:{a.line})")
    for s in result.mcp_servers:
        typer.echo(
            f"  mcp    {s.id}  ({_plural(len(s.tools), 'tool')}: {', '.join(s.tools)})"
            f"  {_reached(s, root)}"
        )
    if result.unattributed:
        names = ", ".join(t.qualname for t in result.unattributed)
        typer.echo(f"  unattributed  {names}  (set @donkey.tool(server=...))")
    typer.echo(
        f"found {_plural(len(result.agents), 'agent')}, {_plural(len(result.tools), 'tool')}, "
        f"{_plural(len(result.mcp_servers), 'MCP server')}"
    )
    if result.issues:
        typer.secho(_plural(len(result.issues), "issue"), fg="red", err=True)
        for i in result.issues:
            typer.secho(f"  {i.path}:{i.line}  {i.kind}  {i.subject}: {i.detail}", err=True)
