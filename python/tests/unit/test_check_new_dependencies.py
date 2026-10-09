"""``scripts/check_new_dependencies.py``, the CI rule from #936.

Every direct dependency is a reviewed decision recorded in
``dependency_allowlist.toml``, and a name new to a PR must exist on PyPI. These
tests never touch the network: the PyPI lookup is injected.
"""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_new_dependencies.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ddk_check_new_dependencies", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()

_TODAY = date(2026, 10, 3)


def _project(*uploads: str) -> dict[str, Any]:
    """A PyPI JSON payload with one release per upload date."""
    return {
        "releases": {
            f"1.{i}": [{"upload_time_iso_8601": f"{day}T12:00:00.000000Z"}]
            for i, day in enumerate(uploads)
        }
    }


_HEALTHY = _project("2020-01-01", "2026-09-01")


def test_declared_names_cover_base_extras_and_groups() -> None:
    pyproject = {
        "project": {
            "dependencies": ["httpx>=0.27", "typing-extensions>=4.10; python_version < '3.12'"],
            "optional-dependencies": {
                "strands": ["strands-agents[openai]>=1.57.1"],
                "all": ["donkey-kit[llm,cli]"],
            },
        },
        "dependency-groups": {
            "lint": ["types-PyYAML>=6.0", "Ruff_Tool.x>=1"],
            "dev": [{"include-group": "lint"}, "pytest>=8.0"],
        },
    }
    assert check.declared_names(pyproject) == {
        "httpx",
        "typing-extensions",
        "strands-agents",
        "types-pyyaml",
        "ruff-tool-x",
        "pytest",
    }


def test_allowlist_problems_report_missing_stale_and_incomplete_entries() -> None:
    allowlist = {
        "httpx": {"reason": "transport", "reviewed": date(2026, 10, 3)},
        "left-behind": {"reason": "was removed", "reviewed": date(2026, 10, 3)},
        "no-reason": {"reason": " ", "reviewed": date(2026, 10, 3)},
        "no-date": {"reason": "why", "reviewed": "2026-10-03"},
    }
    problems = check.allowlist_problems({"httpx", "brand-new", "no-reason", "no-date"}, allowlist)
    assert problems == [
        "brand-new: declared in pyproject.toml but not in dependency_allowlist.toml",
        "left-behind: in dependency_allowlist.toml but no longer declared",
        "no-date: 'reviewed' must be a TOML date (YYYY-MM-DD)",
        "no-reason: 'reason' must be a non-empty string",
    ]


def test_shipped_allowlist_parses_into_entries() -> None:
    entries = check.load_allowlist(check.DEFAULT_ALLOWLIST)
    assert "httpx" in entries
    assert isinstance(entries["httpx"]["reviewed"], date)


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [("mcp", "mcp", 0), ("requests", "reqeusts", 2), ("openai", "opanai", 1), ("ruff", "", 4)],
)
def test_edit_distance(a: str, b: str, distance: int) -> None:
    assert check.edit_distance(a, b) == distance


def test_assess_fails_a_name_that_does_not_exist_on_pypi() -> None:
    errors, warnings = check.assess("ghost-pkg", None, {"httpx"}, _TODAY)
    assert errors == ["ghost-pkg: not found on PyPI"]
    assert warnings == []


def test_assess_fails_a_project_with_no_released_files() -> None:
    errors, _ = check.assess("placeholder", {"releases": {"0.0.0": []}}, set(), _TODAY)
    assert errors == ["placeholder: exists on PyPI but has no released files"]


def test_assess_passes_an_established_project_silently() -> None:
    assert check.assess("httpx", _HEALTHY, {"pydantic"}, _TODAY) == ([], [])


def test_assess_warns_on_young_stale_and_lookalike_names() -> None:
    errors, warnings = check.assess(
        "langchain-opanai", _project("2026-08-01"), {"langchain-openai"}, _TODAY
    )
    assert errors == []
    assert warnings == [
        "langchain-opanai: first release 2026-08-01 is less than 90 days old",
        "langchain-opanai: name is within edit distance 2 of 'langchain-openai'",
    ]
    _, stale = check.assess("old-thing", _project("2015-01-01", "2023-01-01"), set(), _TODAY)
    assert stale == ["old-thing: last release 2023-01-01 is more than 2 years old"]


def _write(tmp_path: Path, name: str, deps: list[str]) -> Path:
    path = tmp_path / name
    quoted = ", ".join(f'"{d}"' for d in deps)
    path.write_text(f"[project]\ndependencies = [{quoted}]\n", encoding="utf-8")
    return path


def _run(
    tmp_path: Path, base: list[str], head: list[str], projects: dict[str, Any]
) -> tuple[int, list[str]]:
    looked_up: list[str] = []

    def fetch(name: str) -> dict[str, Any] | None:
        looked_up.append(name)
        return projects.get(name)

    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(
        "".join(
            f'[packages.{check.requirement_name(r)}]\nreason = "r"\nreviewed = 2026-10-03\n'
            for r in head
        ),
        encoding="utf-8",
    )
    argv = [
        "--base",
        str(_write(tmp_path, "base.toml", base)),
        "--pyproject",
        str(_write(tmp_path, "head.toml", head)),
        "--allowlist",
        str(allowlist),
    ]
    return check.main(argv, fetch=fetch, today=_TODAY), looked_up


def test_main_skips_pypi_when_no_name_is_new(tmp_path: Path) -> None:
    status, looked_up = _run(tmp_path, ["httpx>=0.27"], ["httpx>=0.28"], {})
    assert (status, looked_up) == (0, [])


def test_main_fails_a_new_name_missing_from_pypi(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, looked_up = _run(tmp_path, ["httpx>=0.27"], ["httpx>=0.27", "ghost-pkg>=1"], {})
    assert (status, looked_up) == (1, ["ghost-pkg"])
    assert "::error::ghost-pkg: not found on PyPI" in capsys.readouterr().out


def test_main_warns_but_passes_a_young_new_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, _ = _run(tmp_path, [], ["fresh>=1"], {"fresh": _project("2026-09-20")})
    assert status == 0
    assert "::warning::fresh: first release 2026-09-20" in capsys.readouterr().out


def test_main_treats_a_missing_base_as_empty(tmp_path: Path) -> None:
    looked_up: list[str] = []

    def fetch(name: str) -> dict[str, Any] | None:
        looked_up.append(name)
        return _HEALTHY

    head = _write(tmp_path, "head.toml", ["httpx>=0.27"])
    argv = ["--base", str(tmp_path / "none.toml"), "--pyproject", str(head)]
    assert check.main(argv, fetch=fetch, today=_TODAY) == 0
    assert looked_up == ["httpx"]


def test_main_fails_when_pypi_is_unreachable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def fetch(name: str) -> dict[str, Any] | None:
        raise OSError("connection refused")

    head = _write(tmp_path, "head.toml", ["httpx>=0.27"])
    argv = ["--base", str(tmp_path / "none.toml"), "--pyproject", str(head)]
    assert check.main(argv, fetch=fetch, today=_TODAY) == 1
    assert "::error::httpx: could not query PyPI (connection refused)" in capsys.readouterr().out
