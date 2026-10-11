"""Every ``ADAPTERS`` entry meets the integration checklist in ``CONTRIBUTING.md``
(#742).

Each test is parametrised from ``ADAPTERS``, so a new registry entry is checked
the moment it is added. Missing one of these items fails here:

* a registry entry with a non-empty probe and an extra;
* an extra whose requirements all have a ``>=`` floor, with a §8.3 floor row in
  ``docs/verified-apis.md``;
* a lazy framework import: no top-level import of a probed module, and the
  factories import through ``_native_import`` (which raises the curated error);
* all three forms: ``donkey.<fw>.<factory>()``, ``connection_kwargs()`` and the
  module-level factory;
* a ``verify_frameworks.py`` row per factory, a website page, an example and an
  entry in the import-linter independence contract;
* the adapter contract suite: a driver per factory, a curated-error case per
  factory, a one-send retry case, and a leg in the ``adapter-contract`` CI matrix.

Framework-free: it reads source, config and docs only, so the base-only job runs
it too.
"""

from __future__ import annotations

import ast
import functools
import importlib
import importlib.util
import inspect
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from donkey_kit.integrations import ADAPTERS

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON = Path(__file__).resolve().parents[2]
_REPO = _PYTHON.parent
_INTEGRATIONS = _PYTHON / "src" / "donkey_kit" / "integrations"
_PYPROJECT = tomllib.loads((_PYTHON / "pyproject.toml").read_text())
_CI = yaml.safe_load((_REPO / ".github" / "workflows" / "ci.yml").read_text())

#: Module-level names in an adapter's ``__all__`` that are helpers, not factories.
_HELPERS = {"refusal_translator", "typed_refusals"}

#: The ``adapter-contract`` CI leg's nox session runs the whole suite (#748): the
#: contract files and every other test that needs the leg's framework run against it.
_WHOLE_SUITE = "python -m pytest -q"

_ATTRS = list(ADAPTERS)


@functools.cache
def _load(path: Path) -> ModuleType:
    """Import a test-tree file by path (``tests/`` is not a package)."""
    name = f"_checklist_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through it
    sys.path.insert(0, str(path.parent))  # its own sibling imports
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


def _module(attr: str) -> ModuleType:
    return importlib.import_module(ADAPTERS[attr].module, package="donkey_kit.integrations")


def _factories(attr: str) -> list[str]:
    """The adapter's module-level factories: the public functions in ``__all__``."""
    module = _module(attr)
    return [
        name
        for name in module.__all__
        if name not in _HELPERS and inspect.isfunction(getattr(module, name))
    ]


def _source(attr: str) -> str:
    return (_INTEGRATIONS / (ADAPTERS[attr].module.lstrip(".") + ".py")).read_text()


def _contract_job() -> dict[str, Any]:
    job: dict[str, Any] = _CI["jobs"]["adapter-contract"]
    return job


@pytest.mark.parametrize("attr", _ATTRS)
def test_registry_entry_has_a_probe_and_an_extra(attr: str) -> None:
    spec = ADAPTERS[attr]
    assert spec.attr == attr
    assert isinstance(spec.probe, tuple) and spec.probe
    assert all(isinstance(m, str) and m for m in spec.probe)
    assert spec.extra


@pytest.mark.parametrize("attr", _ATTRS)
def test_extra_has_floors_and_a_floor_row(attr: str) -> None:
    extra = ADAPTERS[attr].extra
    requirements = _PYPROJECT["project"]["optional-dependencies"].get(extra)
    assert requirements, f"no [{extra}] extra in pyproject.toml"
    assert all(">=" in r for r in requirements), requirements
    floors = (_REPO / "docs" / "verified-apis.md").read_text().split("### 8.3", 1)[1]
    assert f"\n| `{extra}` |" in floors, f"no §8.3 floor row for [{extra}]"


@pytest.mark.parametrize("attr", _ATTRS)
def test_framework_is_imported_lazily_through_base(attr: str) -> None:
    source = _source(attr)
    roots = {m.split(".")[0] for m in ADAPTERS[attr].probe}
    top_level: list[str] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            top_level += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top_level.append(node.module)
    eager = [name for name in top_level if name.split(".")[0] in roots]
    assert eager == [], f"top-level framework imports (keep them under TYPE_CHECKING): {eager}"
    assert "_native_import(" in source, "factories must import through Adapter._native_import"


@pytest.mark.parametrize("attr", _ATTRS)
def test_every_form_is_exposed(attr: str) -> None:
    module = _module(attr)
    cls = getattr(module, ADAPTERS[attr].cls)
    assert callable(getattr(cls, "connection_kwargs", None))
    factories = _factories(attr)
    assert factories, f"{attr} has no module-level factory in __all__"
    for name in factories:
        assert callable(getattr(cls, name, None)), f"{attr}.{name}() has no adapter method"


@pytest.mark.parametrize("attr", _ATTRS)
def test_every_factory_has_a_verify_frameworks_row(attr: str) -> None:
    tree = ast.parse((_PYTHON / "scripts" / "verify_frameworks.py").read_text())
    rows = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "FRAMEWORKS"
        and node.value is not None
    )
    module = "donkey_kit.integrations" + ADAPTERS[attr].module
    for factory in _factories(attr):
        assert any(
            key.split(".")[0] == attr and path == module and fn == factory
            for key, path, fn, _, _ in rows
        ), f"no verify_frameworks.py row for {attr}.{factory}"


@pytest.mark.parametrize("attr", _ATTRS)
def test_docs_page_example_and_independence_entry_exist(attr: str) -> None:
    docs = re.search(r"Docs: https://docs\.donkey-kit\.dev/frameworks/([\w-]+)", _source(attr))
    assert docs, f"{attr}'s adapter docstring has no Docs: link"
    page = _REPO / "website" / "content" / "frameworks" / f"{docs.group(1)}.mdx"
    assert page.is_file(), page
    assert (_PYTHON / "examples" / attr / "main.py").is_file()
    contracts = _PYPROJECT["tool"]["importlinter"]["contracts"]
    independent = next(c["modules"] for c in contracts if c["type"] == "independence")
    assert "donkey_kit.integrations" + ADAPTERS[attr].module in independent


@pytest.mark.parametrize("attr", _ATTRS)
def test_every_factory_is_in_the_contract_suite(attr: str) -> None:
    drivers = _load(_PYTHON / "tests" / "conformance" / "contract_drivers.py").DRIVERS
    curated = _load(_PYTHON / "tests" / "unit" / "test_missing_framework_error.py").FACTORIES
    retries = _load(_PYTHON / "tests" / "unit" / "test_framework_retries.py")._CASES
    factories = set(_factories(attr))
    assert {d.factory for d in drivers.values() if d.adapter == attr} == factories
    assert all(key == f"{d.adapter}.{d.factory}" for key, d in drivers.items())
    assert factories <= {name for name, _, _ in curated.get(attr, [])}
    assert any(case.adapter == attr for case in retries.values())


@pytest.mark.parametrize("attr", _ATTRS)
def test_contract_suite_runs_in_a_ci_leg(attr: str) -> None:
    job = _contract_job()
    assert ADAPTERS[attr].extra in job["strategy"]["matrix"]["extra"]


def test_contract_ci_matrix_is_exactly_the_registry() -> None:
    job = _contract_job()
    assert sorted(job["strategy"]["matrix"]["extra"]) == sorted(s.extra for s in ADAPTERS.values())
    assert job["env"]["DONKEY_CONTRACT_EXTRA"] == "${{ matrix.extra }}"
    # #765: the job runs its python/noxfile.py session, and the session runs
    # the whole suite (test_noxfile.py checks the job calls the session).
    runs = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert '-s "adapter-contract($DONKEY_CONTRACT_EXTRA)"' in runs, runs
    session = next(
        node
        for node in ast.walk(ast.parse((_PYTHON / "noxfile.py").read_text()))
        if isinstance(node, ast.FunctionDef) and node.name == "adapter_contract"
    )
    commands = [
        " ".join(arg.value for arg in node.args if isinstance(arg, ast.Constant))
        for node in ast.walk(session)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "session.run"
    ]
    assert _WHOLE_SUITE in commands, commands


def test_contributing_states_the_checklist() -> None:
    contributing = (_REPO / "CONTRIBUTING.md").read_text()
    assert "### Adding a framework adapter: the integration checklist" in contributing
