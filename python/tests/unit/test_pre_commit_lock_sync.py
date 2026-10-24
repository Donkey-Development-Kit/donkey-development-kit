"""The lock refresh moves the pre-commit tool pins with the lint lock (#1206).

`tests/unit/test_noxfile.py` holds the ruff-pre-commit `rev:` and the
lint-imports hook's `import-linter==` pin equal to the typecheck-and-lint
job's lock. `lock-refresh.yml` moves that lock, so a ruff or import-linter
release used to turn every leg of the bump PR red until someone bumped
`.pre-commit-config.yaml` by hand (#1143, #1165, #1206). These checks hold the
refresh to rewriting those two pins itself, from the lock it just wrote, and
to committing the result with the lock.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_REPO = _PYTHON_ROOT.parent
_PRE_COMMIT = _REPO / ".pre-commit-config.yaml"
_LOCK_REFRESH = _REPO / ".github" / "workflows" / "lock-refresh.yml"

sys.path.insert(0, str(_PYTHON_ROOT / "scripts"))
from compile_constraints import (  # noqa: E402
    _LINT_LOCK,
    PreCommitSyncError,
    sync_pre_commit_pins,
)


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"not a repo checkout (no {path.relative_to(_REPO)})")
    return path


def _lock(**pins: str) -> str:
    """A lint lock with the committed one's pins, ``pins`` replacing some."""
    lines = []
    for line in _LINT_LOCK.read_text(encoding="utf-8").splitlines():
        name = line.split("==", 1)[0]
        lines.append(f"{name}=={pins[name]}" if name in pins else line)
    return "\n".join(lines) + "\n"


def _hook_pins(config_text: str) -> tuple[str, str, list[str]]:
    hooks: dict[str, dict[str, Any]] = {
        hook["id"]: {**hook, "rev": repo.get("rev")}
        for repo in yaml.safe_load(config_text)["repos"]
        for hook in repo["hooks"]
    }
    return (
        hooks["ruff-check"]["rev"],
        hooks["ruff-format"]["rev"],
        hooks["lint-imports"]["additional_dependencies"],
    )


def test_a_ruff_release_in_the_lock_moves_the_ruff_pre_commit_rev() -> None:
    # The #1206 case: the lock takes ruff 0.17.2 while the hook says v0.17.1.
    config = _require(_PRE_COMMIT).read_text(encoding="utf-8")
    stale = sync_pre_commit_pins(config, _lock(ruff="0.17.1"))
    assert _hook_pins(stale)[0] == "v0.17.1"
    synced = sync_pre_commit_pins(stale, _lock(ruff="0.17.2"))
    check_rev, format_rev, linter = _hook_pins(synced)
    assert check_rev == format_rev == "v0.17.2"
    assert linter == [f"import-linter=={_pin(_LINT_LOCK.read_text(encoding='utf-8'))}"]


def test_an_import_linter_release_in_the_lock_moves_the_hook_dependency() -> None:
    config = _require(_PRE_COMMIT).read_text(encoding="utf-8")
    synced = sync_pre_commit_pins(config, _lock(**{"import-linter": "9.9.9"}))
    assert _hook_pins(synced)[2] == ["import-linter==9.9.9"]


def test_the_sync_rewrites_only_the_two_pins() -> None:
    # Comments, the gitleaks rev and every other line survive byte for byte.
    config = _require(_PRE_COMMIT).read_text(encoding="utf-8")
    synced = sync_pre_commit_pins(config, _lock(ruff="99.0.0", **{"import-linter": "9.9.9"}))
    assert len(config.splitlines()) == len(synced.splitlines())
    changed = [
        new for old, new in zip(config.splitlines(), synced.splitlines(), strict=True) if old != new
    ]
    assert [new.strip() for new in changed] == [
        "rev: v99.0.0",
        "additional_dependencies: [import-linter==9.9.9]",
    ]


def test_the_committed_hooks_already_match_the_committed_lock() -> None:
    # A refresh with no ruff or import-linter release leaves the file alone.
    config = _require(_PRE_COMMIT).read_text(encoding="utf-8")
    assert sync_pre_commit_pins(config, _LINT_LOCK.read_text(encoding="utf-8")) == config


def test_a_lock_without_the_pin_is_an_error_not_a_silent_skip() -> None:
    config = _require(_PRE_COMMIT).read_text(encoding="utf-8")
    lock = "".join(
        line + "\n"
        for line in _LINT_LOCK.read_text(encoding="utf-8").splitlines()
        if not line.startswith("ruff==")
    )
    with pytest.raises(PreCommitSyncError, match="ruff"):
        sync_pre_commit_pins(config, lock)


def test_a_config_without_the_hook_is_an_error_not_a_silent_skip() -> None:
    config = _require(_PRE_COMMIT).read_text(encoding="utf-8")
    moved = config.replace("astral-sh/ruff-pre-commit", "example/elsewhere")
    with pytest.raises(PreCommitSyncError, match="ruff-pre-commit"):
        sync_pre_commit_pins(moved, _LINT_LOCK.read_text(encoding="utf-8"))


def test_the_lock_refresh_commits_the_synced_pre_commit_config() -> None:
    # The bump PR carries the rewritten hooks, or test_noxfile.py fails on it.
    workflow = yaml.safe_load(_require(_LOCK_REFRESH).read_text(encoding="utf-8"))
    runs = [step.get("run", "") for step in workflow["jobs"]["refresh"]["steps"]]
    adds = [
        line.strip() for run in runs for line in run.splitlines() if re.match(r"\s*git add ", line)
    ]
    assert len(adds) == 1, adds
    assert "../.pre-commit-config.yaml" in adds[0].split()


def _pin(lock_text: str) -> str:
    return next(
        line.split("==", 1)[1].strip()
        for line in lock_text.splitlines()
        if line.startswith("import-linter==")
    )
