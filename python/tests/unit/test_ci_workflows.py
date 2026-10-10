"""The workflows' aggregate jobs cover every other job (#755, docs/ci.md).

`ci-ok` is the one required status check, so a job missing from its `needs:`
is a job whose failure no longer blocks a merge. The nightly `alert` job opens
the tracking issue, so a job missing from its `needs:` fails silently again.
Both lists are hand-maintained; this check catches the drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _workflow(name: str) -> dict[str, Any]:
    path = _WORKFLOWS / name
    if not path.is_file():
        pytest.skip(f"not a repo checkout (no .github/workflows/{name})")
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _needs(job: dict[str, Any]) -> set[str]:
    needs = job["needs"]
    return {needs} if isinstance(needs, str) else set(needs)


@pytest.mark.parametrize(
    ("workflow", "aggregate"),
    [("ci.yml", "ci-ok"), ("nightly-matrix.yml", "alert"), ("lock-refresh.yml", "alert")],
)
def test_aggregate_job_needs_every_other_job(workflow: str, aggregate: str) -> None:
    jobs = _workflow(workflow)["jobs"]
    assert _needs(jobs[aggregate]) == set(jobs) - {aggregate}


def test_ci_ok_reports_even_when_a_dependency_failed() -> None:
    # Without `always()` a failed dependency skips ci-ok, and a skipped
    # required check counts as passing.
    assert _workflow("ci.yml")["jobs"]["ci-ok"]["if"] == "always()"


def test_ci_runs_on_pushes_to_develop() -> None:
    # PyYAML reads the bare `on:` key as the boolean True.
    triggers = _workflow("ci.yml")[True]
    assert set(triggers["push"]["branches"]) == {"main", "develop"}


def test_nightly_alert_is_the_only_job_that_writes_issues() -> None:
    workflow = _workflow("nightly-matrix.yml")
    assert workflow["permissions"] == {"contents": "read"}
    alert = workflow["jobs"]["alert"]
    assert alert["if"] == "failure()"
    assert alert["permissions"] == {"issues": "write"}
    others = {k: v for k, v in workflow["jobs"].items() if k != "alert"}
    assert not [k for k, v in others.items() if "permissions" in v]


def test_lock_refresh_follows_the_nightly_by_its_display_name() -> None:
    # workflow_run matches on the display name, so renaming `Nightly matrix`
    # would stop the lock refresh silently (#763).
    nightly = _workflow("nightly-matrix.yml")["name"]
    triggers = _workflow("lock-refresh.yml")[True]
    assert triggers["workflow_run"] == {"workflows": [nightly], "types": ["completed"]}
    assert "workflow_dispatch" in triggers


def test_lock_refresh_runs_only_after_a_green_nightly_on_develop() -> None:
    condition = _workflow("lock-refresh.yml")["jobs"]["refresh"]["if"]
    for clause in (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.head_branch == 'develop'",
    ):
        assert clause in condition


def test_lock_refresh_write_scopes_stay_on_their_own_jobs() -> None:
    workflow = _workflow("lock-refresh.yml")
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert jobs["refresh"]["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert jobs["alert"]["if"] == "failure()"
    assert jobs["alert"]["permissions"] == {"issues": "write"}
    assert set(jobs) == {"refresh", "alert"}
