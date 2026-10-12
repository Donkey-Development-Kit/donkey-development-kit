"""``scripts/check_framework_releases.py``, the framework release watch (#1066).

The script finds each framework extra's direct requirement whose newest stable
PyPI release is ahead of the adapter-contract lock, and plans the
``framework-release`` issues for ``framework-release-watch.yml``. These tests
never touch the network: the PyPI lookup is injected.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from donkey_kit.integrations import ADAPTERS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the 3.10 floor
    import tomli as tomllib

_PYTHON = Path(__file__).resolve().parents[2]
_SCRIPTS = _PYTHON / "scripts"


def _load() -> ModuleType:
    name = "ddk_check_framework_releases"
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / "check_framework_releases.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through it
    sys.path.insert(0, str(_SCRIPTS))  # its sibling import of check_new_dependencies
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(_SCRIPTS))
    return module


cfr = _load()

_LOCK_HEADER = "# GENERATED FILE — do not edit by hand.\n#\n"


def _file(day: str, *, yanked: bool = False) -> dict[str, Any]:
    return {"upload_time_iso_8601": f"{day}T12:00:00.000000Z", "yanked": yanked}


def _project(releases: dict[str, list[dict[str, Any]]], **urls: str) -> dict[str, Any]:
    return {"info": {"name": "pkg", "project_urls": urls or None}, "releases": releases}


# --- versions -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("locked", "latest", "kind"),
    [
        ("1.15.23", "1.15.27", "patch"),
        ("1.6.7", "1.7.0", "minor"),
        ("0.23.1", "1.0", "major"),
        ("1.2", "1.2.1", "patch"),
        ("1.2.0", "1.2.0.post1", "patch"),
        ("2.0", "1!0.1", "major"),
    ],
)
def test_bump_kind_is_the_first_release_component_that_moved(
    locked: str, latest: str, kind: str
) -> None:
    assert cfr.bump_kind(locked, latest) == kind


@pytest.mark.parametrize(
    ("older", "newer"),
    [("1.9", "1.10"), ("1.2", "1.2.post1"), ("1.2.0", "1.2.1"), ("9.0", "1!0.1")],
)
def test_versions_order_numerically(older: str, newer: str) -> None:
    assert cfr.Version.parse(older) < cfr.Version.parse(newer)


def test_trailing_zeros_do_not_change_a_version() -> None:
    assert cfr.Version.parse("1.2.0") == cfr.Version.parse("1.2")


@pytest.mark.parametrize("text", ["1.0a1", "2.0.0rc3", "1.5.dev2", "1.5.post1.dev0", "3.0b2"])
def test_pre_and_dev_releases_are_flagged(text: str) -> None:
    version = cfr.Version.parse(text)
    assert version is not None and version.prerelease


# --- the newest stable release --------------------------------------------------


def test_latest_skips_pre_releases_yanked_and_empty_releases() -> None:
    project = _project(
        {
            "1.0": [_file("2026-01-01")],
            "1.1": [_file("2026-02-01")],
            "1.2": [_file("2026-03-01", yanked=True)],  # every file yanked
            "1.3": [],  # no files at all
            "2.0rc1": [_file("2026-04-01")],
            "2.0.dev1": [_file("2026-04-02")],
            "not-a-version": [_file("2026-05-01")],
        }
    )
    latest = cfr.latest_release(project)
    assert latest is not None and latest.version == "1.1"


def test_a_release_with_one_live_file_still_counts() -> None:
    project = _project(
        {
            "1.0": [_file("2026-01-01")],
            "1.1": [_file("2026-02-03", yanked=True), _file("2026-02-02")],
        }
    )
    assert cfr.latest_release(project) == cfr.Release(
        "1.1", "2026-02-02", "https://pypi.org/project/pkg/1.1/"
    )


def test_a_project_with_only_pre_releases_has_no_latest() -> None:
    assert cfr.latest_release(_project({"0.1a1": [_file("2026-01-01")]})) is None


def test_release_notes_prefer_the_changelog_then_source_then_home() -> None:
    releases = {"1.0": [_file("2026-01-01")]}
    project = _project(releases, Homepage="h", Source="s", Changelog="c")
    assert cfr.latest_release(project).release_notes_url == "c"
    assert cfr.latest_release(_project(releases, Homepage="h", Source="s")).release_notes_url == "s"
    assert cfr.latest_release(_project(releases, Homepage="h")).release_notes_url == "h"


# --- the lock -------------------------------------------------------------------


def test_read_pins_canonicalizes_names_and_skips_comments() -> None:
    text = _LOCK_HEADER + "Google_ADK==2.11.0\nlitellm==1.104.0\n\n# a comment\n"
    assert cfr.read_pins(text) == {"google-adk": "2.11.0", "litellm": "1.104.0"}


def test_rewrite_pin_moves_only_that_package() -> None:
    text = _LOCK_HEADER + "anyio==4.0\nlangchain-core==1.6.7\nlangchain-openai==1.6.7\n"
    rewritten = cfr.rewrite_pin(text, "langchain_core", "1.6.9")
    assert (
        rewritten == _LOCK_HEADER + "anyio==4.0\nlangchain-core==1.6.9\nlangchain-openai==1.6.7\n"
    )


def test_rewrite_pin_refuses_a_package_the_lock_does_not_pin() -> None:
    with pytest.raises(ValueError, match="found 0"):
        cfr.rewrite_pin(_LOCK_HEADER + "anyio==4.0\n", "crewai", "2.0")


def _tree(tmp_path: Path, locks: dict[str, str]) -> Path:
    constraints = tmp_path / "constraints"
    constraints.mkdir()
    for extra, body in locks.items():
        (constraints / f"adapter-contract-{extra}-py3.12.txt").write_text(_LOCK_HEADER + body)
    (constraints / "test-py3.12.txt").write_text(_LOCK_HEADER + "unrelated==1.0\n")
    return constraints


def test_watched_is_each_lock_extras_direct_requirements(tmp_path: Path) -> None:
    extras = {"openai-agents": ["openai-agents>=0.20", "openai>=2.45"], "llm": ["httpx>=0.27"]}
    locks = {"openai-agents": "openai==3.24.0\nopenai-agents==0.23.1\nanyio==4\n"}
    constraints = _tree(tmp_path, locks)
    pyproject = {"project": {"optional-dependencies": extras}}
    assert cfr.watched(pyproject, constraints) == [
        cfr.Watch("openai_agents", "openai-agents", "openai-agents", "0.23.1"),
        cfr.Watch("openai_agents", "openai-agents", "openai", "3.24.0"),
    ]


def test_watched_fails_when_the_lock_misses_a_direct_requirement(tmp_path: Path) -> None:
    constraints = _tree(tmp_path, {"crewai": "anyio==4\n"})
    pyproject = {"project": {"optional-dependencies": {"crewai": ["crewai>=1.15.3"]}}}
    with pytest.raises(ValueError, match="no pin"):
        cfr.watched(pyproject, constraints)


def test_watched_set_is_adapters_times_their_extras_direct_requirements() -> None:
    # The roster-drift check (#1066): the script derives the watched set from
    # the lock files, without importing donkey_kit, so it must equal ADAPTERS,
    # pair for pair, like test_nightly_framework_legs_match_adapters.
    pyproject = tomllib.loads((_PYTHON / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    expected = {
        (key, spec.extra, cfr.requirement_name(raw))
        for key, spec in ADAPTERS.items()
        for raw in extras[spec.extra]
    }
    actual = {
        (w.framework, w.extra, w.package) for w in cfr.watched(pyproject, _PYTHON / "constraints")
    }
    assert actual == expected


# --- detection ------------------------------------------------------------------


def test_find_releases_lists_only_packages_ahead_of_the_lock() -> None:
    watches = [
        cfr.Watch("crewai", "crewai", "crewai", "1.15.23"),
        cfr.Watch("anthropic", "anthropic", "anthropic", "1.11.0"),
    ]
    projects = {
        "crewai": _project({"1.15.23": [_file("2026-09-01")], "1.15.27": [_file("2026-10-09")]}),
        # Only a pre-release is newer than the pin: nothing to report.
        "anthropic": _project(
            {"1.11.0": [_file("2026-09-01")], "1.12.0rc1": [_file("2026-10-01")]}
        ),
    }
    assert cfr.find_releases(watches, projects.get) == [
        {
            "framework": "crewai",
            "extra": "crewai",
            "package": "crewai",
            "locked": "1.15.23",
            "latest": "1.15.27",
            "bump": "patch",
            "released_at": "2026-10-09",
            "release_notes_url": "https://pypi.org/project/pkg/1.15.27/",
        }
    ]


def test_find_releases_fails_on_a_package_pypi_does_not_know() -> None:
    with pytest.raises(LookupError):
        cfr.find_releases([cfr.Watch("x", "x", "gone", "1.0")], lambda _name: None)


# --- one leg's result -------------------------------------------------------------

_RELEASE = {
    "framework": "crewai",
    "extra": "crewai",
    "package": "crewai",
    "locked": "1.15.23",
    "latest": "1.15.27",
    "bump": "patch",
    "released_at": "2026-10-09",
    "release_notes_url": "https://example.test/notes",
}
_GREEN = {"install": "success", "verify": "success", "pytest": "success", "smoke": "success"}


def _result(**outcomes: str) -> dict[str, Any]:
    installed = {"crewai": "1.15.27", "anyio": "4.0"}
    return cfr.record_result(
        _RELEASE, "locked", _GREEN | outcomes, {"crewai": "1.15.23", "anyio": "4.0"}, installed.get
    )


def test_record_result_lists_the_lock_pins_the_venv_moved() -> None:
    installed = {"crewai": "1.15.27", "openai": "3.29.0", "anyio": "4.0"}
    lock = {"crewai": "1.15.23", "openai": "3.24.0", "anyio": "4.0", "absent": "1.0"}
    result = cfr.record_result(_RELEASE, "co-bump", {"install": "success"}, lock, installed.get)
    assert result["installed"] == "1.15.27"
    assert result["moved"] == {"openai": ["3.24.0", "3.29.0"]}
    assert result["outcomes"] == {
        "install": "success",
        "verify": "skipped",
        "pytest": "skipped",
        "smoke": "skipped",
    }


def test_record_result_keeps_only_failing_verify_rows() -> None:
    verify = [
        {"framework": "crewai", "verdict": "UNVERIFIED (signature confirmed)", "detail": ""},
        {"framework": "crewai.x", "verdict": "SIGNATURE FAIL", "detail": "TypeError: base_url"},
    ]
    result = cfr.record_result(_RELEASE, "locked", _GREEN, {}, lambda _n: None, verify)
    assert result["verify_failures"] == ["crewai.x: SIGNATURE FAIL TypeError: base_url"]


# --- issues ----------------------------------------------------------------------

_WATCHES = [cfr.Watch("crewai", "crewai", "crewai", "1.15.23")]


def _open(body: str, number: int = 7) -> list[Any]:
    return cfr.parse_open_issues([{"number": number, "body": body}])


def _plan(results: list[dict[str, Any]], issues: list[Any], watches: Any = None) -> list[Any]:
    return cfr.plan_issues(
        results, issues, _WATCHES if watches is None else watches, "RUN", lambda e: f"COMMIT-{e}"
    )


def test_a_new_release_opens_one_issue() -> None:
    (op,) = _plan([_result()], [])
    assert op["op"] == "create"
    assert op["title"] == "framework-release: crewai 1.15.23 → 1.15.27"
    assert "**Safe to bump.**" in op["body"]
    assert "--only crewai --emit-verified" in op["body"]
    assert "RUN" in op["body"]


def test_a_red_result_says_adapt_or_record_never_a_ceiling() -> None:
    (op,) = _plan([_result(pytest="failure")], [])
    assert "Not safe to bump yet" in op["body"]
    assert "never add a version ceiling" in op["body"].lower()
    assert "| `pytest -q` (skip guard armed) | ❌ fail |" in op["body"]


def test_a_rerun_with_unchanged_results_edits_but_does_not_comment() -> None:
    body = cfr.render_issue(_result(), "OLD-RUN")
    ops = _plan([_result()], _open(body))
    assert [op["op"] for op in ops] == ["edit"]
    assert ops[0]["number"] == 7 and "RUN" in ops[0]["body"]


def test_a_rerun_with_changed_results_comments_once() -> None:
    body = cfr.render_issue(_result(), "OLD-RUN")
    ops = _plan([_result(smoke="failure")], _open(body))
    assert [op["op"] for op in ops] == ["edit", "comment"]
    assert ops[1]["body"].startswith("The results changed.")


def test_an_edit_keeps_the_impact_section() -> None:
    impact = "<!-- impact:start -->\nreviewed: no breaking change\n<!-- impact:end -->"
    body = cfr.render_issue(_result(), "OLD-RUN", impact)
    (op,) = _plan([_result()], _open(body))
    assert impact in op["body"]


def test_a_newer_release_supersedes_the_older_issue() -> None:
    older = dict(_RELEASE, latest="1.15.25")
    body = cfr.render_issue(cfr.record_result(older, "locked", _GREEN, {}, lambda _n: None), "R")
    ops = _plan([_result()], _open(body))
    assert [(op["op"], op.get("number")) for op in ops] == [("close", 7), ("create", None)]
    assert ops[0]["reason"] == "not planned"


def test_an_issue_the_lock_caught_up_with_is_closed_with_the_lock_commit() -> None:
    body = cfr.render_issue(_result(), "R")
    bumped = [cfr.Watch("crewai", "crewai", "crewai", "1.15.27")]
    (op,) = _plan([], _open(body), bumped)
    assert op == {
        "op": "close",
        "number": 7,
        "reason": "completed",
        "comment": "The lock now pins `crewai==1.15.27` (COMMIT-crewai), which covers 1.15.27.",
    }


def test_an_issue_still_ahead_of_the_lock_but_not_evaluated_stays_open() -> None:
    # A dispatch run for another package evaluates nothing for crewai.
    assert _plan([], _open(cfr.render_issue(_result(), "R"))) == []


def test_an_issue_for_an_unwatched_package_is_closed() -> None:
    (op,) = _plan([], _open(cfr.render_issue(_result(), "R")), [])
    assert op["op"] == "close" and "no longer a direct requirement" in op["comment"]


def test_issues_without_the_marker_are_left_alone() -> None:
    assert _open("a hand-written issue that happens to carry the label") == []


def test_the_marker_round_trips() -> None:
    (issue,) = _open(cfr.render_issue(_result(), "R"), number=3)
    assert (issue.number, issue.extra, issue.package, issue.version) == (
        3,
        "crewai",
        "crewai",
        "1.15.27",
    )


def test_the_plan_summary_flags_a_dry_run() -> None:
    summary = cfr.render_plan(_plan([_result()], []), dry_run=True)
    assert "Dry run" in summary and "## create: framework-release: crewai" in summary


# --- the CLI -----------------------------------------------------------------------


def test_cli_json_matrix_is_empty_when_nothing_is_new(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Every package's only release is a pre-release far ahead of any pin.
    project = _project({"999.0rc1": [_file("2026-10-01")]})
    assert cfr.main(["--json"], fetch=lambda _name: project) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_rejects_an_unwatched_package(capsys: pytest.CaptureFixture[str]) -> None:
    assert cfr.main(["--package", "requests"], fetch=lambda _n: None) == 2
    assert "not a watched package" in capsys.readouterr().out


def test_cli_constraints_for_writes_the_rewritten_lock(tmp_path: Path) -> None:
    out = tmp_path / "lock.txt"
    argv = ["--constraints-for", "crewai==99.0", "--extra", "crewai", "--out", str(out)]
    assert cfr.main(argv) == 0
    lock = cfr.lock_path("crewai").read_text(encoding="utf-8")
    pins = cfr.read_pins(lock)
    assert cfr.read_pins(out.read_text(encoding="utf-8")) == pins | {"crewai": "99.0"}
