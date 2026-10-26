"""``donkey publish`` — register a repository's agents and MCP servers in
Exchange (BG §2.5, #218).

Only ``--dry-run`` runs today. It scans the repository the way ``donkey scan``
does, reads the Exchange coordinates ``[donkey.publication]`` declares, and
prints the asset it would register per MCP server and agent, with the digest
``--if-changed`` would compare. It makes no network call and needs no
credential: the group ID defaults to the ``org_id`` configured for the scanned
root (its ``.donkey-kit.toml`` when it has one, else the working directory's). It exits 1 when
the scan or the declaration reports an issue, 2 when there is nothing at the
path or the config is invalid.

Without ``--dry-run`` the command stays blocked on verification and exits 3:
the Exchange write contract, a republish of an existing version and where the
digest is stored are not yet confirmed against a sandbox (§0.3,
docs/verified-apis.md §7).
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..core.config import TOML_NAME, DonkeyConfig
from ..core.errors import ConfigError
from ..registry.publish_plan import PublishPlan, plan_publication
from ..registry.scan import scan as scan_repository
from ._app import app
from ._blocked import exit_blocked

__all__ = ["publish"]


@app.command(hidden=True)
def publish(
    ctx: typer.Context,
    path: Path = typer.Argument(Path("."), help="The repository (or one .py file) to publish."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="print the assets and digests it would publish; no network call"
    ),
    if_changed: bool = typer.Option(True, "--if-changed/--always"),
    as_json: bool = typer.Option(False, "--json", help="emit the plan as JSON (with --dry-run)"),
) -> None:
    """Publish code-first assets to Exchange (CI-only, BG §2.5)."""
    if not dry_run:
        exit_blocked("Exchange publication mechanism + digest metadata (BG §2.5)")
    as_json = as_json or bool((ctx.obj or {}).get("json"))
    try:
        result = scan_repository(path)
        project = (path.parent if path.is_file() else path) / TOML_NAME
        org_id = DonkeyConfig.resolve(path=project if project.is_file() else None).org_id
    except (FileNotFoundError, ConfigError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    plan = plan_publication(result, path, default_group_id=org_id)
    if as_json:
        typer.echo(plan.to_json(), nl=False)
    else:
        _summary(plan)
    raise typer.Exit(1 if plan.issues else 0)


def _summary(plan: PublishPlan) -> None:
    for a in plan.assets:
        gav = f"{a.group_id or '<group_id>'}:{a.asset_id}:{a.version or '<version>'}"
        typer.echo(f"  {a.type:<6} {gav}  {a.name}  {a.digest}")
    n = len(plan.assets)
    typer.echo(f"would publish {n} asset{'' if n == 1 else 's'} (dry run: nothing was sent)")
    if plan.issues:
        count = len(plan.issues)
        typer.secho(f"{count} issue{'' if count == 1 else 's'}", fg="red", err=True)
        for i in plan.issues:
            typer.secho(f"  {i.path}:{i.line}  {i.kind}  {i.subject}: {i.detail}", err=True)
