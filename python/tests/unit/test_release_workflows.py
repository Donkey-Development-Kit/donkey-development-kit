"""Both publish workflows gate the upload on code, not on prose (#767).

A PyPI upload cannot be undone, so before ``publish-pypi.yml`` or
``publish-testpypi.yml`` uploads anything: the test suite runs at that commit,
the version check runs on the built dists (production also checks the tag),
the built wheel installs and imports in a clean virtualenv, and the upload
carries PEP 740 attestations. This pins that wiring, so removing a gate fails a
test instead of going unnoticed until a bad release.

Framework-free: it reads the workflow files only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"

# workflow file -> its upload job
_PUBLISHERS = {"publish-pypi.yml": "publish-pypi", "publish-testpypi.yml": "publish-testpypi"}


def _jobs(name: str) -> dict[str, dict[str, Any]]:
    path = _WORKFLOWS / name
    if not path.is_file():
        pytest.skip(f"not a repo checkout (no .github/workflows/{name})")
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _runs(job: dict[str, Any]) -> list[str]:
    return [str(step["run"]) for step in job["steps"] if "run" in step]


def _needs(job: dict[str, Any]) -> set[str]:
    needs = job["needs"]
    return {needs} if isinstance(needs, str) else set(needs)


@pytest.fixture(params=sorted(_PUBLISHERS))
def workflow(request: pytest.FixtureRequest) -> str:
    name = request.param
    assert isinstance(name, str)
    return name


def test_the_upload_waits_for_every_other_job(workflow: str) -> None:
    jobs = _jobs(workflow)
    upload = _PUBLISHERS[workflow]
    assert _needs(jobs[upload]) == set(jobs) - {upload}
    assert {"test", "build"} <= set(jobs)


def test_the_suite_runs_at_the_release_commit(workflow: str) -> None:
    runs = _runs(_jobs(workflow)["test"])
    assert "pytest -q" in runs


def test_the_build_checks_the_version_then_smoke_tests_the_wheel(workflow: str) -> None:
    runs = _runs(_jobs(workflow)["build"])
    build = next(i for i, r in enumerate(runs) if "python -m build" in r)
    check = next(i for i, r in enumerate(runs) if "scripts/check_release_version.py" in r)
    smoke = next(i for i, r in enumerate(runs) if "scripts/smoke_test_wheel.py dist/*.whl" in r)
    assert build < check and build < smoke
    assert "dist/*" in runs[check]


def test_production_checks_the_tag_and_requires_a_final_version() -> None:
    build = _jobs("publish-pypi.yml")["build"]
    (step,) = [s for s in build["steps"] if "check_release_version.py" in s.get("run", "")]
    assert '--tag "$TAG"' in step["run"] and "--final" in step["run"]
    # Event values reach the script through env, never interpolated into run:.
    assert step["env"]["TAG"] == "${{ github.event.release.tag_name }}"
    assert "${{" not in step["run"]


def test_production_still_skips_pre_release_releases() -> None:
    upload = _jobs("publish-pypi.yml")["publish-pypi"]
    assert "github.event.release.prerelease == false" in upload["if"]
    assert upload["environment"] == "pypi"


def test_the_upload_generates_attestations(workflow: str) -> None:
    steps = _jobs(workflow)[_PUBLISHERS[workflow]]["steps"]
    action = "pypa/gh-action-pypi-publish@"
    (publish,) = [s for s in steps if str(s.get("uses", "")).startswith(action)]
    assert publish["with"]["attestations"] is True
