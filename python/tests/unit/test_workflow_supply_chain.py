"""The Actions supply chain stays hardened (#757, docs/ci.md convention 7).

A tag or branch ref can be moved to a different commit after review, so a
compromised or re-tagged action would run with whatever the job holds: the PyPI
publishing identity in the publish jobs, Pages write in the docs deploy. These
checks keep the hardening from eroding one workflow edit at a time:

- every ``uses:`` is a full commit SHA with a ``# vX.Y.Z`` comment (Dependabot
  moves both);
- no checkout leaves the job token in ``.git/config``;
- ``id-token: write`` and ``pages: write`` are granted per job, only to the
  jobs that publish;
- Dependabot, CODEOWNERS and the binary-image attributes exist and cover what
  the issue asked for.

``workflow-lint`` in ci.yml runs actionlint and zizmor over the same files; this
suite is the offline, framework-free half that the base-only job also runs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_GITHUB = _REPO / ".github"
_WORKFLOWS = _GITHUB / "workflows"
_FILES = sorted(_WORKFLOWS.glob("*.yml")) if _WORKFLOWS.is_dir() else []

pytestmark = pytest.mark.skipif(not _FILES, reason="not a repo checkout (no .github/workflows)")

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
_PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
_VERSION_COMMENT = re.compile(r"^\s+#\s+v\d+\.\d+\.\d+\s*$")

#: (workflow file, job) pairs allowed to mint an OIDC token. The two publish
#: jobs use it for PyPI Trusted Publishing, the docs deploy for deploy-pages.
_ID_TOKEN_JOBS = {
    ("publish-pypi.yml", "publish-pypi"),
    ("publish-testpypi.yml", "publish-testpypi"),
    ("docs.yml", "deploy"),
}


def _load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(params=_FILES, ids=[f.name for f in _FILES])
def workflow(request: pytest.FixtureRequest) -> Path:
    path = request.param
    assert isinstance(path, Path)
    return path


def test_every_action_is_pinned_to_a_full_sha_with_a_version_comment(workflow: Path) -> None:
    bad = []
    for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
        match = _USES.match(line)
        if not match or match["ref"].startswith("./"):
            continue
        if not _PINNED.match(match["ref"]) or not _VERSION_COMMENT.match(match["rest"]):
            bad.append(f"{workflow.name}:{number}: {line.strip()}")
    assert not bad, "uses: not pinned as <action>@<40-hex sha> # vX.Y.Z:\n" + "\n".join(bad)


def test_no_checkout_persists_the_job_token(workflow: Path) -> None:
    persisted = [
        name
        for name, job in _load(workflow)["jobs"].items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and step.get("with", {}).get("persist-credentials") is not False
    ]
    assert not persisted, f"{workflow.name}: checkout without persist-credentials: false"


def test_publish_permissions_are_granted_per_job_only(workflow: Path) -> None:
    data = _load(workflow)
    top = data.get("permissions", {})
    assert isinstance(top, dict), f"{workflow.name}: top-level permissions must be a mapping"
    for scope in ("id-token", "pages"):
        assert scope not in top, f"{workflow.name}: {scope} granted to the whole workflow"
    for name, job in data["jobs"].items():
        perms = job.get("permissions", {})
        if perms.get("id-token") == "write":
            assert (workflow.name, name) in _ID_TOKEN_JOBS, (
                f"{workflow.name}: job {name!r} mints an OIDC token but is not a publish job"
            )
        if perms.get("pages") == "write":
            assert (workflow.name, name) == ("docs.yml", "deploy"), (
                f"{workflow.name}: job {name!r} has pages: write"
            )


def test_docs_build_job_cannot_publish() -> None:
    # The build job runs third-party npm code (`npm ci`, `next build`).
    jobs = _load(_WORKFLOWS / "docs.yml")["jobs"]
    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["deploy"]["permissions"]["pages"] == "write"
    assert jobs["deploy"]["permissions"]["id-token"] == "write"


def test_dependabot_covers_actions_npm_and_pip() -> None:
    config = _load(_GITHUB / "dependabot.yml")
    updates = {(u["package-ecosystem"], u["directory"]): u for u in config["updates"]}
    assert set(updates) == {
        ("github-actions", "/"),
        ("npm", "/website"),
        ("pip", "/python"),
    }
    for (ecosystem, _), update in updates.items():
        assert update["target-branch"] == "develop", ecosystem
        assert update["schedule"]["interval"] == "weekly", ecosystem
    for key in (("github-actions", "/"), ("npm", "/website")):
        assert updates[key].get("groups"), f"{key[0]} updates are not grouped"
    # python/pyproject.toml holds floors, never ceilings (§8.4): a scheduled bump
    # there would raise a floor. Version updates belong to the dev/release lock
    # (#763), so pointed at /python they stay off.
    assert updates[("pip", "/python")]["open-pull-requests-limit"] == 0


def _codeowner_rules() -> dict[str, list[str]]:
    rules: dict[str, list[str]] = {}
    for line in (_GITHUB / "CODEOWNERS").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pattern, *owners = line.split()
        rules[pattern] = owners
    return rules


def test_codeowners_cover_the_sensitive_paths() -> None:
    rules = _codeowner_rules()
    for path in (
        "/.github/",
        "/python/pyproject.toml",
        "/python/scripts/",
        "/python/src/donkey_kit/core/",
        "/python/tests/fixtures/",
    ):
        assert rules.get(path), f"CODEOWNERS has no owner for {path}"
        assert (_REPO / path.strip("/")).exists(), f"CODEOWNERS path {path} does not exist"
    assert all(o.startswith("@") for owners in rules.values() for o in owners)


def test_images_are_marked_binary() -> None:
    rules = {
        tuple(line.split()[:2])
        for line in (_REPO / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for ext in ("png", "jpg", "jpeg", "gif", "ico", "webp"):
        assert (f"*.{ext}", "binary") in rules, f"*.{ext} is not marked binary"
