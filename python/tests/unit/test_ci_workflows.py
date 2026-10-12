"""The workflows' aggregate jobs cover every other job (#755, docs/ci.md).

`ci-ok` is the one required status check, so a job missing from its `needs:`
is a job whose failure no longer blocks a merge. The nightly `alert` job opens
the tracking issue, so a job missing from its `needs:` fails silently again.
Both lists are hand-maintained; this check catches the drift.
"""

from __future__ import annotations

import re
import shlex
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
    [
        ("ci.yml", "ci-ok"),
        ("nightly-matrix.yml", "alert"),
        ("lock-refresh.yml", "alert"),
        ("framework-release-watch.yml", "alert"),
    ],
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


def test_framework_release_watch_alert_is_the_only_job_that_writes_issues() -> None:
    # #1066: `alert` closes stale issues even when nothing is new, so it runs
    # always(), and it is the one job with a write scope. `ai-review` (#1067)
    # restates the read-only default on itself.
    workflow = _workflow("framework-release-watch.yml")
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert jobs["alert"]["if"] == "always()"
    assert jobs["alert"]["permissions"] == {"contents": "read", "issues": "write"}
    others = {
        k: v.get("permissions", {"contents": "read"}) for k, v in jobs.items() if k != "alert"
    }
    assert others == {k: {"contents": "read"} for k in others}


def test_framework_release_watch_dry_runs_write_nothing() -> None:
    # Every step that calls `gh` with the write token is skipped on a PR
    # (#1066) and on a replay of a past release (#1067).
    steps = _workflow("framework-release-watch.yml")["jobs"]["alert"]["steps"]
    writers = [s for s in steps if "GH_TOKEN" in s.get("env", {})]
    assert writers
    for step in writers:
        assert "github.event_name != 'pull_request'" in step["if"], step["name"]
    for step in writers[:-1]:  # the failure tracker still reports a broken replay
        assert "!inputs.replay" in step["if"], step["name"]


def _ai_review() -> dict[str, Any]:
    return _workflow("framework-release-watch.yml")["jobs"]["ai-review"]


def test_ai_review_is_the_only_job_on_the_ai_review_environment() -> None:
    # #1067: ANTHROPIC_API_KEY is an `ai-review` environment secret, so this is
    # the one job that can read it.
    found = []
    for path in sorted(_WORKFLOWS.glob("*.y*ml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, job in workflow.get("jobs", {}).items():
            environment = job.get("environment")
            if isinstance(environment, dict):
                environment = environment.get("name")
            if environment == "ai-review":
                found.append((path.name, name))
    assert found == [("framework-release-watch.yml", "ai-review")]


def test_ai_review_never_runs_on_a_pull_request() -> None:
    condition = _ai_review()["if"]
    assert "github.event_name != 'pull_request'" in condition
    # The environment admits only develop: skip, not fail, anywhere else.
    assert "github.ref == 'refs/heads/develop'" in condition


def test_ai_review_is_read_only_bounded_and_advisory() -> None:
    job = _ai_review()
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] == 15
    assert job["strategy"]["fail-fast"] is False
    assert job["needs"] == ["detect"]  # beside `evaluate`, not after it
    (step,) = [s for s in job["steps"] if str(s.get("uses", "")).startswith("anthropics/")]
    assert step["continue-on-error"] is True


def _review_args() -> dict[str, list[str]]:
    (step,) = [s for s in _ai_review()["steps"] if str(s.get("uses", "")).startswith("anthropics/")]
    text = re.sub(r"\$\{\{\s*(.*?)\s*\}\}", r"<\1>", step["with"]["claude_args"])
    args = shlex.split(text)
    return {f: v.split(",") for f, v in zip(args[::2], args[1::2], strict=True)}


def test_ai_review_tools_are_read_and_fetch_only() -> None:
    flags = _review_args()
    allowed = flags["--allowedTools"]
    assert {t.split("(")[0] for t in allowed} == {"Read", "Grep", "Glob", "WebFetch"}
    # WebFetch is scoped to the hosts the release notes are on, never bare.
    assert all(t.startswith("WebFetch(domain:") for t in allowed if t.startswith("WebFetch"))
    denied = set(flags["--disallowedTools"])
    assert {"Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "WebSearch"} <= denied
    assert "Read(//proc/**)" in denied  # the key is in the process environment
    assert flags["--setting-sources"] == ["user"]  # no in-repo settings widen this
    assert flags["--json-schema"] == ["<steps.prompt.outputs.schema>"]
    assert flags["--model"] == ["<env.MODEL>"]
    assert _ai_review()["env"]["MODEL"] == "claude-sonnet-5-5"


def test_ai_review_key_is_an_environment_secret_only() -> None:
    # The step that reads the key is on the environment job, and no other job
    # in the watch names it.
    jobs = _workflow("framework-release-watch.yml")["jobs"]
    for name, job in jobs.items():
        text = yaml.safe_dump(job)
        assert ("ANTHROPIC_API_KEY" in text) == (name == "ai-review"), name


def test_framework_release_watch_matches_the_script_paths_it_runs() -> None:
    triggers = _workflow("framework-release-watch.yml")[True]
    assert set(triggers["pull_request"]["paths"]) == {
        ".github/workflows/framework-release-watch.yml",
        "python/scripts/check_framework_releases.py",
        "python/scripts/review_framework_release.py",
    }
    assert {"package", "replay"} <= set(triggers["workflow_dispatch"]["inputs"])
    assert triggers["schedule"] == [{"cron": "30 5 * * *"}]
