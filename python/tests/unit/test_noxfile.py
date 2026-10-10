"""python/noxfile.py mirrors CI, and CI runs it (#765, docs/ci.md convention 5).

`nox -s <job>` is the documented way to reproduce a CI job, so a session that
drifts from its job is a local gate that passes while CI fails. These checks
read the noxfile's source (no nox import, so they run in every CI job) and
.github/workflows/ci.yml:

* every job but ci-ok is a session of the same name, and its check step calls it;
* each session's install string is the job's install step, verbatim;
* each session installs its own job's line, not another job's;
* the matrices match, and no job runs a check (a tool, a repo script, npm,
  node or timeout) outside its session, bar the named CI-only steps;
* the pre-commit hooks pin the tool versions the lint job locks, and the
  byte-exact fixtures are excluded from the formatter.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON = Path(__file__).resolve().parents[2]
_REPO = _PYTHON.parent
_NOXFILE = _PYTHON / "noxfile.py"
_CI = _REPO / ".github" / "workflows" / "ci.yml"
_PRE_COMMIT = _REPO / ".pre-commit-config.yaml"
_LINT_LOCK = _PYTHON / "constraints" / "typecheck-and-lint-py3.11.txt"
_FIXTURE_DIRS = ("src/donkey_kit/simulator/_fixtures", "tests/fixtures")

#: How a job calls its session: the pinned nox, in the job's own environment.
_NOX = re.compile(
    r'pipx run --spec "nox==\$NOX_VERSION" nox (?:-f \.\./python/noxfile\.py )?'
    r'--no-venv --no-install -s (?P<session>"[^"]+"|\S+)'
)
#: Checks that belong in a session, never inline in a job's step: the check
#: tools, the repo's own scripts, the website's npm/node steps, and `timeout`
#: (a wall-clock bound the session applies itself).
_TOOLS = re.compile(
    r"(?<![\w-])(pytest|mypy|ruff|lint-imports|vulture|coverage|npm|node|timeout)(?![\w-])"
    r"|scripts/"
)
#: (job, step name) of the steps that are CI-only by design, not a local check:
#: the benchmark trend alert reads the previous main run's artifact (#753).
_CI_ONLY_STEPS = {
    ("benchmark", "Alert on a relative regression vs. the previous main run (#753)"),
}
#: The matrix env var each parametrized job names its session leg with.
_LEG_VARS = {
    "anthropic-stacks": "$ANTHROPIC_SPEC",
    "adk-stacks": "$ADK_SPEC",
    "adapter-contract": "$DONKEY_CONTRACT_EXTRA",
}


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"not a repo checkout (no {path.relative_to(_REPO)})")
    return path


def _noxfile() -> ast.Module:
    return ast.parse(_require(_NOXFILE).read_text(encoding="utf-8"))


def _literal(name: str) -> Any:
    for node in _noxfile().body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == name and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"noxfile.py has no module-level {name}")


def _sessions() -> dict[str, ast.FunctionDef]:
    """Each ``@nox.session(name=...)`` function, by session name."""
    sessions = {}
    for node in ast.walk(_noxfile()):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == "nox.session":
                name = next(k.value for k in decorator.keywords if k.arg == "name")
                sessions[ast.literal_eval(name)] = node
    return sessions


def _session_names() -> set[str]:
    return set(_sessions())


def _installed_jobs(function: ast.FunctionDef) -> list[str]:
    """The job keys a session function passes to ``_install(session, "<job>")``."""
    return [
        ast.literal_eval(call.args[1])
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_install"
    ]


def _workflow() -> dict[str, Any]:
    workflow = yaml.safe_load(_require(_CI).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _jobs() -> dict[str, dict[str, Any]]:
    jobs = dict(_workflow()["jobs"])
    del jobs["ci-ok"]  # the aggregate: it runs no checks of its own
    return jobs


def _steps(job: dict[str, Any]) -> list[tuple[str, str]]:
    """Each ``run:`` step of ``job`` as (step name, whitespace-normalised command)."""
    return [
        (str(step.get("name", "")), " ".join(str(step["run"]).split()))
        for step in job["steps"]
        if "run" in step
    ]


def _runs(job: dict[str, Any]) -> list[str]:
    return [run for _, run in _steps(job)]


def test_every_ci_job_has_a_session_of_the_same_name() -> None:
    installs = _literal("CI_INSTALLS")
    assert set(installs) == set(_jobs())
    assert _session_names() == set(installs)


@pytest.mark.parametrize("job", sorted(_literal("CI_INSTALLS")) if _NOXFILE.is_file() else [])
def test_each_job_runs_its_session(job: str) -> None:
    sessions = [m["session"] for run in _runs(_jobs()[job]) for m in _NOX.finditer(run)]
    leg = _LEG_VARS.get(job)
    assert sessions == [f'"{job}({leg})"' if leg else job]


@pytest.mark.parametrize("job", sorted(_literal("CI_INSTALLS")) if _NOXFILE.is_file() else [])
def test_each_session_installs_what_its_job_installs(job: str) -> None:
    expected = _literal("CI_INSTALLS")[job]
    installs = [run for run in _runs(_jobs()[job]) if "pip install" in run and "nox" not in run]
    assert installs == ([] if expected is None else [" ".join(expected.split())])


@pytest.mark.parametrize("job", sorted(_literal("CI_INSTALLS")) if _NOXFILE.is_file() else [])
def test_each_session_runs_its_own_jobs_install(job: str) -> None:
    # A session that installed another job's line would still pass the
    # verbatim-string check above, so the call itself is checked too.
    expected = [] if _literal("CI_INSTALLS")[job] is None else [job]
    assert _installed_jobs(_sessions()[job]) == expected


def test_no_job_runs_a_check_outside_its_session() -> None:
    inline = {
        (name, step): run
        for name, job in _jobs().items()
        for step, run in _steps(job)
        if (name, step) not in _CI_ONLY_STEPS
        and not _NOX.search(run)
        and "pip install" not in run
        and _TOOLS.search(run)
    }
    assert inline == {}


def test_the_ci_only_steps_exist() -> None:
    # A renamed step would silently drop out of the allowlist above.
    jobs = _jobs()
    for job, step in _CI_ONLY_STEPS:
        assert step in [name for name, _ in _steps(jobs[job])], (job, step)


def test_session_matrices_are_the_ci_matrices() -> None:
    jobs = _jobs()
    pythons = _literal("TEST_PYTHONS")
    assert jobs["test"]["strategy"]["matrix"]["python-version"] == pythons
    assert jobs["all-extra-resolves"]["strategy"]["matrix"]["python-version"] == pythons
    assert jobs["anthropic-stacks"]["strategy"]["matrix"]["anthropic"] == _literal(
        "ANTHROPIC_SPECS"
    )
    assert jobs["adk-stacks"]["strategy"]["matrix"]["adk"] == _literal("ADK_SPECS")
    assert jobs["adapter-contract"]["strategy"]["matrix"]["extra"] == _literal("CONTRACT_EXTRAS")


def test_ci_pins_the_nox_version_the_noxfile_needs() -> None:
    version = _workflow()["env"]["NOX_VERSION"]
    assert re.fullmatch(r"\d{4}\.\d+\.\d+", version), version
    needs = next(
        ast.literal_eval(node.value)
        for node in _noxfile().body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "nox.needs_version"
    )
    assert needs == f">={version}"


def _lock_pin(package: str) -> str:
    for line in _require(_LINT_LOCK).read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{package}=="):
            return line.split("==", 1)[1].strip()
    raise AssertionError(f"{package} is not pinned in {_LINT_LOCK.name}")


def _hooks() -> dict[str, dict[str, Any]]:
    config = yaml.safe_load(_require(_PRE_COMMIT).read_text(encoding="utf-8"))
    return {
        hook["id"]: {**hook, "rev": repo.get("rev")}
        for repo in config["repos"]
        for hook in repo["hooks"]
    }


def test_pre_commit_runs_the_lint_jobs_tool_versions() -> None:
    hooks = _hooks()
    assert hooks["ruff-check"]["rev"] == hooks["ruff-format"]["rev"] == f"v{_lock_pin('ruff')}"
    assert hooks["lint-imports"]["additional_dependencies"] == [
        f"import-linter=={_lock_pin('import-linter')}"
    ]
    assert {"gitleaks", "generate-llms"} <= set(hooks)


def test_the_formatter_never_rewrites_the_byte_exact_fixtures() -> None:
    pyproject = tomllib.loads((_PYTHON / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = pyproject["tool"]["ruff"]["format"]["exclude"]
    assert all(f"{directory}/**" in excluded for directory in _FIXTURE_DIRS)
    for hook in ("ruff-check", "ruff-format"):
        pattern = re.compile(_hooks()[hook]["exclude"])
        assert all(pattern.search(f"python/{d}/README.md") for d in _FIXTURE_DIRS), hook


def test_the_import_contract_hook_runs_when_the_contracts_change() -> None:
    # The contracts live in pyproject.toml, so a commit that edits only them
    # must still run the hook.
    pattern = re.compile(_hooks()["lint-imports"]["files"])
    assert pattern.search("python/pyproject.toml")
    assert pattern.search("python/src/donkey_kit/core/config.py")
