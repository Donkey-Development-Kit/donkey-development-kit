"""``donkey verify`` — check each published MCP server and A2A agent against
what it was published with (BG §2.5, #1352, #1354).

It plans the repository's assets the way ``donkey publish`` does, then runs
:meth:`~donkey_kit.registry.publication.Publication.verify` on each one at its
declared version: an MCP server's ``mcp-metadata`` tools in Exchange against
its live ``tools/list``, and an ``a2a_v1`` agent's published card against the
card it serves, each through its gateway instance. It writes nothing. Any
other agent (protocol ``other`` or undeclared) is skipped: it has no card to
compare (docs/verified-apis.md §7).

It needs the control-plane credentials, to read Exchange, and the gateway
credential, to read the live side, as a ``donkey.tools`` binding does.

``--json`` prints the verify document (``verify_format`` 1): the plan's
assets, each with its ``status`` (``match``, ``drift``, ``error``, ``skipped``,
or ``not_attempted`` when an issue stopped the verify), ``detail`` and
``differences`` (#1427). Issues still go to stderr too.

Exit codes: 0 when every verified asset matches, 1 when one drifted, a read failed,
or the scan or the declaration reports an issue (nothing is read), 2 when there
is nothing at the path or the config is invalid or lacks the control-plane
credentials.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import typer

from ..core.config import TOML_NAME, DonkeyConfig
from ..core.errors import ConfigError, DonkeyError
from ..donkey import Donkey
from ..registry.publication import Publication, PublicationAssetType
from ..registry.publish_plan import PlannedAsset, PublishPlan, plan_publication
from ..registry.scan import scan as scan_repository
from ._app import app
from .publish import _document, _issues

__all__ = ["VERIFY_FORMAT", "verify"]

#: The version of ``donkey verify --json``'s document. Bumped when its shape
#: changes incompatibly, as ``PLAN_FORMAT`` is (#1427).
VERIFY_FORMAT = 1


@dataclass(frozen=True)
class VerifyOutcome:
    """What verifying one planned asset found: ``status`` is ``match``,
    ``drift``, ``error``, ``skipped`` or ``not_attempted``, and
    ``differences`` the drift lines."""

    asset: PlannedAsset
    status: str
    detail: str
    differences: tuple[str, ...] = ()


@app.command()
def verify(
    ctx: typer.Context,
    path: Path = typer.Argument(Path("."), help="The repository (or one .py file) to verify."),
    as_json: bool = typer.Option(
        False, "--json", help="emit the plan with each asset's outcome as JSON"
    ),
) -> None:
    """Check published MCP servers and A2A agents against their live side (read-only, BG §2.5)."""
    as_json = as_json or bool((ctx.obj or {}).get("json"))
    try:
        result = scan_repository(path)
        project = (path.parent if path.is_file() else path) / TOML_NAME
        cfg = DonkeyConfig.resolve(path=project if project.is_file() else None)
    except (FileNotFoundError, ConfigError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    plan = plan_publication(result, path, default_group_id=cfg.org_id)
    if plan.issues:
        if as_json:
            detail = "not attempted: the plan has issues"
            stopped = tuple(VerifyOutcome(a, "not_attempted", detail) for a in plan.assets)
            typer.echo(_outcomes_json(plan, stopped), nl=False)
        _issues(plan)
        typer.secho("nothing was verified", fg="red", err=True)
        raise typer.Exit(1)
    try:
        cfg = cfg.validated(need="control_plane")
    except ConfigError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(2) from exc
    outcomes = asyncio.run(_verify(cfg, plan))
    if as_json:
        typer.echo(_outcomes_json(plan, outcomes), nl=False)
    else:
        _summary(outcomes)
    if any(o.status in ("drift", "error") for o in outcomes):
        raise typer.Exit(1)


def _donkey(cfg: DonkeyConfig) -> Donkey:
    """The ``Donkey`` whose registry and gateway credential verify; a test seam."""
    return Donkey(cfg)


async def _verify(cfg: DonkeyConfig, plan: PublishPlan) -> tuple[VerifyOutcome, ...]:
    async with _donkey(cfg) as donkey:
        return tuple([await _verify_one(donkey, a) for a in plan.assets])


async def _verify_one(donkey: Donkey, asset: PlannedAsset) -> VerifyOutcome:
    if asset.type == PublicationAssetType.MCP_SERVER.value:
        asset_type, live = PublicationAssetType.MCP_SERVER, "the live tools match"
    elif asset.protocol == "a2a_v1":
        asset_type, live = PublicationAssetType.AGENT, "the live card matches"
    else:
        return VerifyOutcome(asset, "skipped", "only an a2a_v1 agent has a card to compare")
    # plan.issues is empty, so the coordinates are declared.
    assert asset.group_id is not None and asset.version is not None
    publication = Publication(
        asset_type=asset_type,
        group_id=asset.group_id,
        asset_id=asset.asset_id,
        version=asset.version,
        name=asset.name,
        description=asset.description or "",
    )
    try:
        differences = await publication.verify(donkey)
    except DonkeyError as exc:
        return VerifyOutcome(asset, "error", f"{exc} {exc.remediation}".strip())
    if differences:
        n = len(differences)
        return VerifyOutcome(asset, "drift", f"{n} difference{'' if n == 1 else 's'}", differences)
    return VerifyOutcome(asset, "match", f"{live} the published version")


def _outcomes_json(plan: PublishPlan, outcomes: tuple[VerifyOutcome, ...]) -> str:
    """The verify document: the plan's assets with each one's outcome, in plan order."""
    data = _document(plan, "verify_format", VERIFY_FORMAT)
    for asset, outcome in zip(data["assets"], outcomes, strict=True):
        asset["status"], asset["detail"] = outcome.status, outcome.detail
        asset["differences"] = list(outcome.differences)
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def _summary(outcomes: tuple[VerifyOutcome, ...]) -> None:
    for o in outcomes:
        a = o.asset
        line = f"  {o.status:<7} {a.type:<6} {a.group_id}:{a.asset_id}:{a.version}  {o.detail}"
        typer.secho(line, fg="red" if o.status in ("drift", "error") else None)
        for difference in o.differences:
            typer.echo(f"      {difference}")
    drifted = sum(o.status in ("drift", "error") for o in outcomes)
    verified = sum(o.status != "skipped" for o in outcomes)
    typer.echo(
        f"verified {verified} asset{'' if verified == 1 else 's'}: "
        + ("all match" if not drifted else f"{drifted} did not")
    )
