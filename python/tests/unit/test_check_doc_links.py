"""``scripts/check_doc_links.py``, the CI rule from #785.

A fresh clone only has what git tracks, so a tracked doc may only link to
tracked paths, and plans live in the GitHub issue rather than in committed
``docs/**/plans/`` files.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_doc_links.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_check_doc_links", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()

_IN_CHECKOUT = (check._DEFAULT_ROOT / ".git").exists()
_needs_checkout = pytest.mark.skipif(
    not _IN_CHECKOUT, reason="needs the git checkout, not an sdist"
)


def _tree(tmp_path: Path, files: dict[str, str]) -> tuple[Path, set[str]]:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path, set(files)


@_needs_checkout
def test_shipped_tree_has_no_problems() -> None:
    root = check._DEFAULT_ROOT
    assert check.find_problems(root, check.tracked_files(root)) == []


@pytest.mark.parametrize(
    "line",
    [
        "See [the skills](.claude/skills/README.md).",
        "Read [`CLAUDE.md`](CLAUDE.md#rules) first.",
        'A [titled](missing.md "Title") link.',
        "[ref]: ../outside.md",
    ],
)
def test_links_to_untracked_paths_are_rejected(tmp_path: Path, line: str) -> None:
    root, tracked = _tree(tmp_path, {"CONTRIBUTING.md": f"{line}\n"})
    [found] = check.find_problems(root, tracked)
    assert found.startswith("CONTRIBUTING.md:1: link to untracked path: ")


@pytest.mark.parametrize(
    ("source", "line"),
    [
        ("README.md", "See [the guide](docs/guide.md#setup)."),
        ("README.md", "The [docs folder](docs/) and [the same](./docs)."),
        ("docs/guide.md", "Back [up](../README.md) from a subdirectory."),
        ("README.md", "An [anchor](#setup), a [route](/quickstart), a [URL](https://x.io)."),
        ("README.md", "Mail [us](mailto:team@example.com)."),
    ],
)
def test_tracked_and_external_links_pass(tmp_path: Path, source: str, line: str) -> None:
    files = {"README.md": "x\n", "docs/guide.md": "x\n"}
    files[source] = f"{line}\n"
    root, tracked = _tree(tmp_path, files)
    assert check.find_problems(root, tracked) == []


def test_links_inside_code_fences_are_ignored(tmp_path: Path) -> None:
    text = "```markdown\n[example](not/a/file.md)\n```\n"
    root, tracked = _tree(tmp_path, {"README.md": text})
    assert check.find_problems(root, tracked) == []


def test_committed_plan_files_are_rejected(tmp_path: Path) -> None:
    root, tracked = _tree(
        tmp_path,
        {
            "docs/superpowers/plans/2026-01-01-thing.md": "# Plan\n",
            "docs/plans/x.txt": "plan\n",
            # Only plans/ directories inside docs/ are banned.
            "python/tests/plans/README.md": "x\n",
            "docs/plans.md": "x\n",
        },
    )
    assert check.find_problems(root, tracked) == [
        "docs/plans/x.txt: committed plan file (put the plan in the issue)",
        "docs/superpowers/plans/2026-01-01-thing.md: committed plan file (put the plan in "
        "the issue)",
    ]


@_needs_checkout
def test_every_docs_sync_row_names_an_existing_page() -> None:
    root = check._DEFAULT_ROOT
    text = (root / "CONTRIBUTING.md").read_text(encoding="utf-8")
    section = text.split("## 4. Docs-sync rule", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| `")]
    assert rows, "the docs-sync table is missing"
    content = root / "website" / "content"
    for row in rows:
        pages = row.split("|")[2]
        for page in re.findall(r"`([\w/<>.-]+\.mdx)`", pages):
            if "<fw>" in page:
                continue
            assert (content / page).is_file(), f"docs-sync row names a missing page: {page}"
