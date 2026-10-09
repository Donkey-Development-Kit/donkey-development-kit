"""Every workflow job is bounded and every matrix reports per leg (#761).

``docs/ci.md`` convention 6: a job with no ``timeout-minutes`` gets GitHub's
360-minute default, so one hang costs six runner-hours per matrix leg, and a
matrix left on the default ``fail-fast: true`` cancels its other legs on the
first failure, hiding the per-version signal it exists to give. ``ci.yml``
also cancels a PR's superseded run, and saves pip caches from pushes only
(#1005). Parametrised over the workflow files, so a
new job or workflow is checked the moment it is added.

Framework-free: it reads the workflow files only, so the base-only job runs it
too.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
_FILES = sorted(_WORKFLOWS.glob("*.yml")) if _WORKFLOWS.is_dir() else []

#: GitHub's own ceiling is 360; anything near it is no bound at all.
_MAX_TIMEOUT_MINUTES = 30


def _jobs(path: Path) -> dict[str, dict[str, Any]]:
    jobs = yaml.safe_load(path.read_text())["jobs"]
    assert isinstance(jobs, dict)
    return jobs


@pytest.fixture(params=_FILES, ids=[f.name for f in _FILES])
def workflow(request: pytest.FixtureRequest) -> Path:
    path = request.param
    assert isinstance(path, Path)
    return path


@pytest.mark.skipif(not _FILES, reason="not a repo checkout (no .github/workflows)")
def test_every_job_sets_a_bounded_timeout(workflow: Path) -> None:
    unbounded = {
        name: job.get("timeout-minutes")
        for name, job in _jobs(workflow).items()
        if not isinstance(job.get("timeout-minutes"), int)
        or not 0 < job["timeout-minutes"] <= _MAX_TIMEOUT_MINUTES
    }
    assert not unbounded, f"{workflow.name}: jobs without a 1-{_MAX_TIMEOUT_MINUTES} min timeout"


@pytest.mark.skipif(not _FILES, reason="not a repo checkout (no .github/workflows)")
def test_every_matrix_reports_each_leg(workflow: Path) -> None:
    fail_fast = {
        name
        for name, job in _jobs(workflow).items()
        if "matrix" in job.get("strategy", {}) and job["strategy"].get("fail-fast") is not False
    }
    assert not fail_fast, f"{workflow.name}: matrices without fail-fast: false"


@pytest.mark.skipif(not _FILES, reason="not a repo checkout (no .github/workflows)")
def test_ci_cancels_a_superseded_pr_run() -> None:
    concurrency = yaml.safe_load((_WORKFLOWS / "ci.yml").read_text())["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def _ci_steps() -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, step)
        for name, job in _jobs(_WORKFLOWS / "ci.yml").items()
        for step in job.get("steps", [])
    ]


@pytest.mark.skipif(not _FILES, reason="not a repo checkout (no .github/workflows)")
def test_ci_saves_pip_caches_from_pushes_only() -> None:
    """A PR run restores the pip cache and never saves one (#1005).

    ``setup-python``'s own ``cache: pip`` saves on every run, PRs included, and a
    PR's cache is readable by that PR alone, so it is not used at all.
    """
    steps = _ci_steps()
    builtin = {name for name, step in steps if "cache" in step.get("with", {})}
    assert not builtin, (
        f"setup-python cache: in ci.yml jobs {builtin}; use actions/cache/restore + save"
    )
    saves = [
        (name, step)
        for name, step in steps
        if str(step.get("uses", "")).startswith("actions/cache/save@")
    ]
    assert saves, "ci.yml saves no pip cache"
    unguarded = {
        name for name, step in saves if "github.event_name == 'push'" not in str(step.get("if", ""))
    }
    assert not unguarded, f"cache save steps not limited to pushes: {unguarded}"


@pytest.mark.skipif(not _FILES, reason="not a repo checkout (no .github/workflows)")
def test_ci_pip_cache_keys_hash_a_real_file() -> None:
    """A ``${{ }}`` nested inside ``hashFiles('...')`` is not expanded (#1005).

    The glob then matches nothing, ``hashFiles`` returns ``''`` and the key never
    changes when the constraints do. Matrix paths go through ``format()``.
    """
    keys = {
        name: str(step["with"]["key"])
        for name, step in _ci_steps()
        if str(step.get("uses", "")).startswith("actions/cache/restore@")
    }
    assert keys, "ci.yml restores no pip cache"
    bad = {
        name
        for name, key in keys.items()
        if not (args := re.findall(r"hashFiles\(([^)]*)\)", key))
        or any("${{" in arg for arg in args)
    }
    assert not bad, f"cache keys with no hashFiles or a nested ${{{{ }}}} in it: {bad}"
