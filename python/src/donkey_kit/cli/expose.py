"""``donkey expose`` and ``donkey preflight``: put one MCP server or A2A agent
behind an existing gateway, or check that it can be (BG §2.9.2, ADR 0015,
#226, #1279).

Both scan the repository the way ``donkey publish`` does, then read the
service's ``[donkey.expose]`` declaration (see
:mod:`donkey_kit.registry.expose_plan`). ``expose`` publishes the service's
Exchange asset, creates its API Manager instance on the named gateway, adds
the declared policies it lacks and deploys it, reporting one line per step
(see :mod:`donkey_kit.registry.expose`). A re-run changes nothing. It never
deletes, never edits what exists and never creates the gateway or the
environment.

``preflight``, like ``expose --dry-run``, only reads: it prints what would be
created and exits non-zero when anything stands in the way, such as a missing
gateway, environment or connected-app scope.

``--json`` prints the report (``expose_format`` 1), also when the declaration
has an issue: then ``ok`` is false, ``steps`` is empty and ``issues`` lists
them. Issues still go to stderr too.

Exit codes: 0 when every step is present, created or planned; 1 when a step is
missing, differs from the declaration or failed, or the declaration has an
issue (nothing is sent); 2 when there is nothing at the path or the config is
invalid or lacks the control-plane credentials.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from ..core.config import TOML_NAME, DonkeyConfig
from ..core.errors import ConfigError
from ..core.runtime import Runtime
from ..registry.expose import ExposeReport
from ..registry.expose import expose as run_exposure
from ..registry.expose_plan import ExposePlan, plan_exposure
from ..registry.publish_plan import plan_publication
from ..registry.scan import ScanResult
from ..registry.scan import scan as scan_repository
from ._app import app
from ._common import ci_source

__all__ = ["expose", "preflight"]

_MARKS = {
    "present": "✓",
    "created": "✓",
    "planned": "·",
    "differs": "!",
    "missing": "✗",
    "failed": "✗",
    "skipped": "-",
}

_SERVICE = typer.Argument(
    None, help="The service ID to expose; optional when the project has exactly one."
)
_PATH = typer.Option(Path("."), "--path", help="The repository (or one .py file) to scan.")
_ENV = typer.Option(None, "--env", metavar="NAME", help="The Anypoint environment (ANYPOINT_ENV).")
_TARGET = typer.Option(
    None, "--target", metavar="NAME", help="The gateway's name; overrides the declared target."
)
_JSON = typer.Option(False, "--json", help="emit the report as JSON")


@app.command()
def expose(
    ctx: typer.Context,
    service: str | None = _SERVICE,
    path: Path = _PATH,
    env: str | None = _ENV,
    target: str | None = _TARGET,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="only read: print what would be created, and change nothing"
    ),
    as_json: bool = _JSON,
) -> None:
    """Expose one MCP server or A2A agent through an existing gateway."""
    _run(ctx, service, path, env, target, dry_run=dry_run, as_json=as_json)


@app.command()
def preflight(
    ctx: typer.Context,
    service: str | None = _SERVICE,
    path: Path = _PATH,
    env: str | None = _ENV,
    target: str | None = _TARGET,
    as_json: bool = _JSON,
) -> None:
    """Check, read-only, that a service can be exposed."""
    _run(ctx, service, path, env, target, dry_run=True, as_json=as_json)


def _run(
    ctx: typer.Context,
    service: str | None,
    path: Path,
    env: str | None,
    target: str | None,
    *,
    dry_run: bool,
    as_json: bool,
) -> None:
    as_json = as_json or bool((ctx.obj or {}).get("json"))
    try:
        result = scan_repository(path)
        project = (path.parent if path.is_file() else path) / TOML_NAME
        file = project if project.is_file() else None
        cfg = (
            DonkeyConfig.resolve(path=file, environment=env)
            if env
            else DonkeyConfig.resolve(path=file)
        )
    except (FileNotFoundError, ConfigError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    publication = plan_publication(result, path, default_group_id=cfg.org_id)
    plan = plan_exposure(publication, path, service=service, target=target)
    if plan.issues:
        if as_json:
            _echo_json(ExposeReport((), dry_run=dry_run, issues=plan.issues))
        count = len(plan.issues)
        typer.secho(f"{count} issue{'' if count == 1 else 's'}", fg="red", err=True)
        for i in plan.issues:
            typer.secho(f"  {i.path}:{i.line}  {i.kind}  {i.subject}: {i.detail}", err=True)
        typer.secho("nothing was sent", fg="red", err=True)
        raise typer.Exit(1)
    try:
        cfg = cfg.validated(need="control_plane")
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    report = asyncio.run(_expose(cfg, plan, result, dry_run=dry_run))
    if as_json:
        _echo_json(report)
    else:
        _print(plan, report)
    raise typer.Exit(0 if report.ok else 1)


def _echo_json(report: ExposeReport) -> None:
    typer.echo(json.dumps(report.to_dict(), sort_keys=True, indent=2))


def _runtime(cfg: DonkeyConfig) -> Runtime:
    """The runtime whose control-plane client exposes; a test seam."""
    return Runtime(cfg)


async def _expose(
    cfg: DonkeyConfig, plan: ExposePlan, result: ScanResult, *, dry_run: bool
) -> ExposeReport:
    runtime = _runtime(cfg)
    try:
        return await run_exposure(
            plan, result, runtime.control_http, cfg=cfg, dry_run=dry_run, source=ci_source()
        )
    finally:
        await runtime.aclose()


def _print(plan: ExposePlan, report: ExposeReport) -> None:
    assert plan.asset is not None
    width = max(len(s.name) for s in report.steps)
    for s in report.steps:
        good = s.status in ("present", "created", "planned")
        typer.secho(
            f"  {_MARKS[s.status]} {s.name:<{width}}  {s.status:<8} {s.detail}",
            fg=None if good else ("yellow" if s.status in ("differs", "skipped") else "red"),
        )
        if s.remediation and not good and s.status != "skipped":
            typer.echo(f"    {' ' * width}  {s.remediation}")
    kind = "A2A agent" if plan.endpoint_type == "a2a" else "MCP server"
    if not report.ok:
        typer.secho(f"{plan.asset.service_id} is not exposed", fg="red", err=True)
    elif report.dry_run:
        typer.echo(f"would expose {kind} {plan.asset.service_id} (dry run: nothing was changed)")
    else:
        where = f" at {report.url}" if report.url else ""
        typer.echo(f"{kind} {plan.asset.service_id} is exposed{where}")
