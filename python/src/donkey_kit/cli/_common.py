"""Helpers ``donkey publish``, ``donkey expose`` and ``donkey verify`` share."""

from __future__ import annotations

import os
from typing import Any

import typer

from ..registry.exchange_page import PageSource
from ..registry.publish_plan import PublishPlan

__all__ = ["ci_source", "document", "print_issues"]


def ci_source() -> PageSource | None:
    """Where this command runs from, from the GitHub Actions environment."""
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


def document(plan: PublishPlan, key: str, version: int) -> dict[str, Any]:
    """The plan's JSON with the command's own format key in place of ``plan_format``."""
    data = plan.to_dict()
    del data["plan_format"]
    data[key] = version
    return data


def print_issues(plan: PublishPlan) -> None:
    """The plan's issues on stderr, one per line, after their count."""
    if plan.issues:
        count = len(plan.issues)
        typer.secho(f"{count} issue{'' if count == 1 else 's'}", fg="red", err=True)
        for i in plan.issues:
            typer.secho(f"  {i.path}:{i.line}  {i.kind}  {i.subject}: {i.detail}", err=True)
