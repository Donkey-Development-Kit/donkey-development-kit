"""``donkey publish`` — register a repository's MCP servers in Exchange (BG §2.5,
#218).

It scans the repository the way ``donkey scan`` does, reads the Exchange
coordinates ``[donkey.publication]`` declares, and plans one asset per MCP
server and agent, each with a content digest. The group ID defaults to the
``org_id`` configured for the scanned root (its ``.donkey-kit.toml`` when it has
one, else the working directory's).

``--dry-run`` prints the plan and makes no network call. Without it each MCP
asset is published over the control plane (see
:mod:`donkey_kit.registry.exchange_publish`): with ``--if-changed``, the
default, an asset whose latest published version has the same digest is left
alone. Agents are listed as skipped; publishing them is not supported yet.

Exit codes: 1 when the scan or the declaration reports an issue (nothing is
sent) or Exchange refuses a publish, 2 when there is nothing at the path or the
config is invalid or lacks the control-plane credentials.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from ..core.config import TOML_NAME, DonkeyConfig
from ..core.errors import ConfigError, DonkeyError
from ..core.runtime import Runtime
from ..registry.exchange_publish import PublishOutcome, publish_plan
from ..registry.publish_plan import PublishPlan, plan_publication
from ..registry.scan import ScanResult
from ..registry.scan import scan as scan_repository
from ._app import app

__all__ = ["publish"]


@app.command(hidden=True)
def publish(
    ctx: typer.Context,
    path: Path = typer.Argument(Path("."), help="The repository (or one .py file) to publish."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="print the assets and digests it would publish; no network call"
    ),
    if_changed: bool = typer.Option(
        True,
        "--if-changed/--always",
        help="skip an asset whose latest published version has the same digest",
    ),
    as_json: bool = typer.Option(False, "--json", help="emit the plan as JSON (with --dry-run)"),
) -> None:
    """Publish code-first assets to Exchange (CI-only, BG §2.5)."""
    as_json = as_json or bool((ctx.obj or {}).get("json"))
    try:
        result = scan_repository(path)
        project = (path.parent if path.is_file() else path) / TOML_NAME
        cfg = DonkeyConfig.resolve(path=project if project.is_file() else None)
    except (FileNotFoundError, ConfigError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    plan = plan_publication(result, path, default_group_id=cfg.org_id)
    if dry_run:
        if as_json:
            typer.echo(plan.to_json(), nl=False)
        else:
            _summary(plan)
        raise typer.Exit(1 if plan.issues else 0)
    if plan.issues:
        _issues(plan)
        typer.secho("nothing was published", fg="red", err=True)
        raise typer.Exit(1)
    try:
        cfg = cfg.validated(need="control_plane")
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    try:
        outcomes = asyncio.run(_publish(cfg, plan, result, if_changed=if_changed))
    except DonkeyError as exc:
        typer.secho(str(exc), fg="red", err=True)
        typer.secho(exc.remediation, err=True)
        raise typer.Exit(1) from exc
    for o in outcomes:
        a = o.asset
        line = f"  {o.status:<9} {a.type:<6} {a.group_id}:{a.asset_id}:{a.version}  {o.detail}"
        typer.secho(line, err=o.status == "skipped", fg="yellow" if o.status == "skipped" else None)
    published = sum(o.status == "published" for o in outcomes)
    typer.echo(f"published {published} asset{'' if published == 1 else 's'}")


def _runtime(cfg: DonkeyConfig) -> Runtime:
    """The runtime whose control-plane client publishes; a test seam."""
    return Runtime(cfg)


async def _publish(
    cfg: DonkeyConfig, plan: PublishPlan, result: ScanResult, *, if_changed: bool
) -> tuple[PublishOutcome, ...]:
    runtime = _runtime(cfg)
    try:
        assert cfg.org_id is not None  # validated(need="control_plane")
        return await publish_plan(
            plan,
            result,
            runtime.control_http,
            base_url=cfg.control_plane_url,
            org_id=cfg.org_id,
            if_changed=if_changed,
        )
    finally:
        await runtime.aclose()


def _summary(plan: PublishPlan) -> None:
    for a in plan.assets:
        gav = f"{a.group_id or '<group_id>'}:{a.asset_id}:{a.version or '<version>'}"
        typer.echo(f"  {a.type:<6} {gav}  {a.name}  {a.digest}")
    n = len(plan.assets)
    typer.echo(f"would publish {n} asset{'' if n == 1 else 's'} (dry run: nothing was sent)")
    _issues(plan)


def _issues(plan: PublishPlan) -> None:
    if plan.issues:
        count = len(plan.issues)
        typer.secho(f"{count} issue{'' if count == 1 else 's'}", fg="red", err=True)
        for i in plan.issues:
            typer.secho(f"  {i.path}:{i.line}  {i.kind}  {i.subject}: {i.detail}", err=True)
