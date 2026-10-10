"""``scripts/check_docs_sync.py``: the docs-sync reminder from #797.

A PR that changes a mapped SDK surface (CONTRIBUTING.md §4) without touching
its docs page gets a reminder. The map itself is pinned against the tree, so
it cannot point at pages that do not exist, which is how the old prose-only
rule drifted.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_docs_sync.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_check_docs_sync", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the script's dataclasses look their module up
    spec.loader.exec_module(module)
    return module


sync = _load()

_ROOT = sync._DEFAULT_ROOT
_needs_tree = pytest.mark.skipif(
    not (_ROOT / "website" / "content").is_dir(), reason="needs the git checkout, not an sdist"
)

_SRC = "python/src/donkey_kit/"


@pytest.mark.parametrize(
    ("changed", "page"),
    [
        (f"{_SRC}integrations/langgraph.py", "website/content/frameworks/langgraph.mdx"),
        (f"{_SRC}integrations/openai_agents.py", "website/content/frameworks/openai.mdx"),
        (f"{_SRC}integrations/_base.py", "website/content/frameworks/index.mdx"),
        (f"{_SRC}core/config.py", "website/content/reference/configuration.mdx"),
        (f"{_SRC}cli/doctor.py", "website/content/cli.mdx"),
    ],
)
def test_code_only_change_to_a_mapped_surface_fires(changed: str, page: str) -> None:
    (reminder,) = sync.reminders([changed, "python/tests/unit/test_x.py"])
    assert reminder.changed == (changed,)
    assert page in reminder.pages


def test_changing_the_mapped_page_too_is_silent() -> None:
    changed = [f"{_SRC}core/config.py", "website/content/reference/configuration.mdx"]
    assert sync.reminders(changed) == []


def test_any_one_listed_page_satisfies_the_surface() -> None:
    changed = [f"{_SRC}integrations/adk.py", "website/content/examples/adk.mdx"]
    assert sync.reminders(changed) == []


def test_unmapped_changes_are_silent() -> None:
    assert sync.reminders(["python/tests/unit/test_x.py", "README.md", f"{_SRC}py.typed"]) == []


def test_several_files_of_one_surface_give_one_reminder() -> None:
    (reminder,) = sync.reminders([f"{_SRC}cli/init.py", f"{_SRC}cli/mock.py"])
    assert reminder.changed == (f"{_SRC}cli/init.py", f"{_SRC}cli/mock.py")


@_needs_tree
def test_every_mapped_page_exists() -> None:
    missing = [
        page
        for surface in sync.SURFACES
        for page in surface.page_paths
        if not (_ROOT / page).is_file()
    ]
    assert missing == []


@_needs_tree
def test_every_code_pattern_matches_a_real_file() -> None:
    files = [p.relative_to(_ROOT).as_posix() for p in (_ROOT / _SRC).rglob("*.py")]
    dead = [
        pattern
        for surface in sync.SURFACES
        for pattern in surface.code
        if not any(sync.Surface((pattern,), ()).matches(path) for path in files)
    ]
    assert dead == []


@_needs_tree
@pytest.mark.parametrize("package", ["integrations", "cli"])
def test_every_adapter_and_cli_module_is_mapped(package: str) -> None:
    # The surfaces the issue names: a new adapter or command must get a page.
    modules = [p.relative_to(_ROOT).as_posix() for p in (_ROOT / _SRC / package).glob("*.py")]
    unmapped = [m for m in modules if not any(s.matches(m) for s in sync.SURFACES)]
    assert unmapped == []


def test_main_warns_but_passes_unless_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    listing = tmp_path / "changed.txt"
    listing.write_text(f"{_SRC}core/config.py\n", encoding="utf-8")
    assert sync.main(["--files", str(listing)]) == 0
    assert "docs-sync: python/src/donkey_kit/core/config.py changed without" in (
        capsys.readouterr().err
    )
    assert sync.main(["--files", str(listing), "--strict"]) == 1


def test_main_annotates_and_summarises_under_actions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    listing = tmp_path / "changed.txt"
    listing.write_text(f"{_SRC}integrations/strands.py\n", encoding="utf-8")
    assert sync.main(["--files", str(listing)]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"::warning file={_SRC}integrations/strands.py,title=docs-sync::")
    assert "`website/content/frameworks/strands.mdx`" in summary.read_text(encoding="utf-8")


def test_base_mode_diffs_against_the_merge_base(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("a")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "feature")
    config = tmp_path / _SRC / "core" / "config.py"
    config.parent.mkdir(parents=True)
    config.write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "change")
    assert sync.changed_files(tmp_path, "main", "HEAD") == [f"{_SRC}core/config.py"]
    assert sync.main(["--root", str(tmp_path), "--base", "main", "--strict"]) == 1
