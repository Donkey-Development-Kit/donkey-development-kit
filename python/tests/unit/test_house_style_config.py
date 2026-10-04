"""CONTRIBUTING §3 rules that live in packaging metadata, checked as tests (#721).

"The style guide is the config": each §3 rule is enforced by a tool or a test.
Ruff, mypy and import-linter cover the code rules; these cover the ones that are
about what the package declares and ships.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from packaging.requirements import Requirement

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[2]

# A floor (`>=`) or an exclusion of one bad release (`!=`) never stops a user
# from installing a newer framework release; every other operator can.
_FLOOR_OPERATORS = frozenset({">=", "!="})


def _load_dependency_check() -> ModuleType:
    """``scripts/check_new_dependencies.py``, which owns the allowlist rules (#936)."""
    script = _PYTHON_ROOT / "scripts" / "check_new_dependencies.py"
    spec = importlib.util.spec_from_file_location("ddk_check_new_dependencies", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())


def _declared_requirements() -> list[str]:
    pyproject = _pyproject()
    declared: list[str] = list(pyproject["project"]["dependencies"])
    for extra in pyproject["project"]["optional-dependencies"].values():
        declared.extend(extra)
    for group in pyproject.get("dependency-groups", {}).values():
        declared.extend(item for item in group if isinstance(item, str))
    return declared


# The top-level module each published extra exists for: some file in src/ must
# import it (#744). `all` is the bundle of other extras, so it has no module.
_EXTRA_IMPORTS = {
    "llm": "openai",
    "langgraph": "langchain_openai",
    "adk": "google.adk",
    "agent_framework": "agent_framework",
    "openai-agents": "agents",
    "anthropic": "anthropic",
    "crewai": "crewai",
    "llamaindex": "llama_index",
    "strands": "strands",
    "local": "starlette",
    "otel": "opentelemetry",
    "cli": "typer",
    "test": "pytest",
}

# Contributor tooling: none of it may reach a user through an extra (#744).
_CONTRIBUTOR_TOOLING = frozenset(
    {"pytest", "pytest-asyncio", "respx", "mypy", "ruff", "import-linter", "vulture"}
)


def test_dependencies_are_floors_never_ceilings() -> None:
    """Extras are floors, never ceilings (§8.4): no `<`, `<=`, `==` or `~=` pins.

    Environment markers such as ``python_version < '3.12'`` are not version
    pins and are allowed.
    """
    ceilings = [
        raw
        for raw in _declared_requirements()
        for spec in Requirement(raw).specifier
        if spec.operator not in _FLOOR_OPERATORS
    ]
    assert ceilings == [], f"upper or exact pins in pyproject.toml: {ceilings}"


def test_every_direct_dependency_is_allowlisted() -> None:
    """Every direct dependency is a reviewed decision (#936).

    A name in ``dependencies``, any extra or any dependency group must have an
    entry in ``dependency_allowlist.toml`` with a reason and a review date, and
    an entry whose package is no longer declared must be removed.
    """
    check = _load_dependency_check()
    pyproject = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())
    problems = check.allowlist_problems(
        check.declared_names(pyproject), check.load_allowlist(check.DEFAULT_ALLOWLIST)
    )
    assert problems == [], "dependency allowlist out of sync:\n" + "\n".join(problems)


def test_no_contributor_tooling_extra_is_published() -> None:
    """Contributor tooling is a dependency group, never a published extra (#744)."""
    pyproject = _pyproject()
    assert "dev" not in pyproject["project"]["optional-dependencies"]
    assert "dev" in pyproject["dependency-groups"]


def test_every_published_extra_is_imported_by_src() -> None:
    """An extra with no code behind it advertises a capability that doesn't exist (#744)."""
    extras = set(_pyproject()["project"]["optional-dependencies"]) - {"all"}
    assert extras == set(_EXTRA_IMPORTS), "map each new extra to the module it exists for"
    sources = [path.read_text() for path in (_PYTHON_ROOT / "src").rglob("*.py")]
    unused = [
        extra
        for extra, module in _EXTRA_IMPORTS.items()
        if not any(
            re.search(rf"^\s*(import|from) {re.escape(module)}\b", text, re.MULTILINE)
            for text in sources
        )
    ]
    assert unused == [], f"extras no module in src/ imports: {unused}"


def test_all_extra_installs_no_test_runner_or_linter() -> None:
    """`[all]` is everything a user runs, not contributor tooling (#744)."""
    extras = _pyproject()["project"]["optional-dependencies"]
    names: set[str] = set()
    pending = list(extras["all"])
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.name == "donkey-kit":
            for extra in requirement.extras:
                pending.extend(extras[extra])
        else:
            names.add(requirement.name)
    assert names & _CONTRIBUTOR_TOOLING == set()


def test_package_ships_py_typed() -> None:
    """PEP 561 marker, so downstream users get the SDK's annotations."""
    assert (_PYTHON_ROOT / "src" / "donkey_kit" / "py.typed").is_file()


_REPO_ROOT = _PYTHON_ROOT.parent
_CI_MATRIX = re.compile(r"^\s*python-version:\s*\[([^\]]*)\]", re.MULTILINE)


def _project() -> dict[str, object]:
    project = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())["project"]
    assert isinstance(project, dict)
    return project


def _ci_matrix_versions() -> list[str]:
    ci = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        pytest.skip("not a repo checkout (no .github/workflows/ci.yml)")
    matrices = {
        tuple(v.strip().strip("\"'") for v in m.split(","))
        for m in _CI_MATRIX.findall(ci.read_text())
    }
    assert len(matrices) == 1, f"ci.yml matrices disagree: {matrices}"
    return list(matrices.pop())


def test_license_metadata_is_a_pep_639_expression() -> None:
    """``License-Expression`` plus the shipped license file, not the legacy table (#745)."""
    project = _project()
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    classifiers = project["classifiers"]
    assert isinstance(classifiers, list)
    assert not [c for c in classifiers if c.startswith("License ::")]


def test_shipped_license_matches_the_repo_license() -> None:
    """``python/LICENSE`` is a copy of the repo-root file hatch cannot reach."""
    root = _REPO_ROOT / "LICENSE"
    if not root.is_file():
        pytest.skip("not a repo checkout (no repo-root LICENSE)")
    assert (_PYTHON_ROOT / "LICENSE").read_text() == root.read_text()


def test_version_classifiers_match_the_ci_matrix() -> None:
    """A supported version is a tested version (docs/python-support.md)."""
    matrix = _ci_matrix_versions()
    classifiers = _project()["classifiers"]
    assert isinstance(classifiers, list)
    prefix = "Programming Language :: Python :: 3."
    classified = [
        c.removeprefix("Programming Language :: Python :: ")
        for c in classifiers
        if c.startswith(prefix)
    ]
    assert classified == matrix
    assert _project()["requires-python"] == f">={matrix[0]}"
