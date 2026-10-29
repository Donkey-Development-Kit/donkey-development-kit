"""``donkey publish`` — register a repository's MCP servers and agents in
Exchange (BG §2.5, #218, #1260).

It scans the repository the way ``donkey scan`` does, reads the Exchange
coordinates ``[donkey.publication]`` declares, and plans one asset per MCP
server and agent, each with a content digest. The group ID defaults to the
``org_id`` configured for the scanned root (its ``.donkey-kit.toml`` when it has
one, else the working directory's).

``--dry-run`` prints the plan and makes no network call. Without it each
asset is published over the control plane (see
:mod:`donkey_kit.registry.exchange_publish`): with ``--if-changed``, the
default, an asset whose latest published version has the same digest is left
alone. Each new version also gets a generated Exchange page; under GitHub
Actions it links the repository, commit and run it was published from.
An agent is published with its ``[donkey.agents]`` protocol, ``other`` when
none is declared, and an ``a2a_v1`` agent with its A2A agent card.

A refused call stops the publish. That asset is ``failed``, or ``incomplete``
when its version was published but its endpoint, page or name and description
were refused, and the assets after it are ``not_attempted``; what was
published before stays published. A rerun finishes an ``incomplete`` publish
rather than reporting it ``unchanged`` (#1426).

``--json`` with ``--dry-run`` prints the plan (``plan_format`` 1). Without it,
it prints the publish document (``publish_format`` 1): the plan's assets, each
with the ``status`` (``published``, ``unchanged``, ``incomplete``, ``failed``
or ``not_attempted``) and ``detail`` of its outcome, also when the publish
failed, and every asset ``not_attempted`` when an issue stopped it (#1427).
Issues and errors still go to stderr.

Exit codes: 1 when the scan or the declaration reports an issue (nothing is
sent) or an asset is ``failed`` or ``incomplete``, 2 when there is nothing at
the path or the config is invalid or lacks the control-plane credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import typer

from ..core.config import TOML_NAME, DonkeyConfig
from ..core.errors import ConfigError, DonkeyError
from ..core.runtime import Runtime
from ..registry.exchange_page import PageSource
from ..registry.exchange_publish import PUBLISH_FORMAT, PublishOutcome, publish_plan
from ..registry.publish_plan import PublishPlan, plan_publication
from ..registry.scan import ScanResult
from ..registry.scan import scan as scan_repository
from ._app import app

__all__ = ["publish"]

# The outcomes that exit 1, and the ones the text summary counts after "published".
_FAILED = ("failed", "incomplete")
_NOT_DONE = ("incomplete", "failed", "not_attempted")


@app.command()
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
    as_json: bool = typer.Option(
        False, "--json", help="emit the plan, or what was published, as JSON"
    ),
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
            _issues(plan)
        else:
            _summary(plan)
        raise typer.Exit(1 if plan.issues else 0)
    if plan.issues:
        if as_json:
            detail = "not attempted: the plan has issues"
            stopped = tuple(PublishOutcome(a, "not_attempted", detail) for a in plan.assets)
            typer.echo(_outcomes_json(plan, stopped), nl=False)
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
        # Refused before any asset was tried, e.g. building the runtime.
        detail = f"not attempted: {exc}"
        outcomes = tuple(PublishOutcome(a, "not_attempted", detail, exc) for a in plan.assets)
        typer.secho(str(exc), fg="red", err=True)
        typer.secho(exc.remediation, err=True)
    if as_json:
        typer.echo(_outcomes_json(plan, outcomes), nl=False)
    else:
        _outcomes(outcomes)
    for o in outcomes:
        if o.status in _FAILED and o.error is not None:
            if as_json:  # the text table has the detail already
                typer.secho(o.detail, fg="red", err=True)
            typer.secho(o.error.remediation, err=True)
    if any(o.error is not None for o in outcomes):
        raise typer.Exit(1)


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
            source=_ci_source(),
        )
    finally:
        await runtime.aclose()


def _ci_source() -> PageSource | None:
    """Where this publish runs from, from the GitHub Actions environment."""
    env = os.environ
    if env.get("GITHUB_ACTIONS") != "true":
        return None
    server, repository = env.get("GITHUB_SERVER_URL"), env.get("GITHUB_REPOSITORY")
    repository_url = f"{server}/{repository}" if server and repository else None
    run_id = env.get("GITHUB_RUN_ID")
    return PageSource(
        repository_url=repository_url,
        commit=env.get("GITHUB_SHA") or None,
        ref=env.get("GITHUB_REF_NAME") or None,
        run_url=f"{repository_url}/actions/runs/{run_id}" if repository_url and run_id else None,
    )


def _document(plan: PublishPlan, key: str, version: int) -> dict[str, Any]:
    """The plan's JSON with the command's own format key in place of ``plan_format``."""
    data = plan.to_dict()
    del data["plan_format"]
    data[key] = version
    return data


def _outcomes_json(plan: PublishPlan, outcomes: tuple[PublishOutcome, ...]) -> str:
    """The publish document: the plan's assets with each one's outcome, in plan
    order. A refused asset's detail ends with the remediation, as ``donkey
    verify``'s does."""
    data = _document(plan, "publish_format", PUBLISH_FORMAT)
    for asset, outcome in zip(data["assets"], outcomes, strict=True):
        detail = outcome.detail
        if outcome.status in _FAILED and outcome.error is not None:
            detail = f"{detail} {outcome.error.remediation}".strip()
        asset["status"], asset["detail"] = outcome.status, detail
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def _outcomes(outcomes: tuple[PublishOutcome, ...]) -> None:
    for o in outcomes:
        a = o.asset
        line = f"  {o.status:<9} {a.type:<6} {a.group_id}:{a.asset_id}:{a.version}  {o.detail}"
        typer.secho(line, fg="red" if o.status in _FAILED else None)
    published = sum(o.status == "published" for o in outcomes)
    line = f"published {published} asset{'' if published == 1 else 's'}"
    counts = [(s, sum(o.status == s for o in outcomes)) for s in _NOT_DONE]
    rest = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts if n)
    typer.echo(f"{line}; {rest}" if rest else line)


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
