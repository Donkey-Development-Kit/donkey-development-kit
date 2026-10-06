"""``ADAPTERS`` is the one adapter roster; every other list of adapters matches it
(#726, ADR 0004).

The adapter set is declared once, in :data:`donkey_kit.integrations.ADAPTERS`.
Several other places must name the same set but cannot be generated from it:
pyproject's extras, import-linter contract and mypy overrides are static TOML,
the ``Donkey`` class annotations must be static for a type checker, and the
conformance exemption table, the nightly matrix and ``verify_frameworks.py``
live outside the package. Each is checked here, so adding, renaming or dropping
an adapter in one place and not the others fails this suite.

Everything is read from source files or from adapter classes, which import
their framework only inside methods, so this needs no framework installed (the
base-only ``tests/unit`` job).
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the 3.10 floor
    import tomli as tomllib

_PYTHON = Path(__file__).resolve().parents[2]
_REPO = _PYTHON.parent
_PYPROJECT = _PYTHON / "pyproject.toml"
_DONKEY = _PYTHON / "src" / "donkey_kit" / "donkey.py"
_SUITE = _PYTHON / "tests" / "conformance" / "suite.py"
_VERIFY = _PYTHON / "scripts" / "verify_frameworks.py"
_NIGHTLY = _REPO / ".github" / "workflows" / "nightly-matrix.yml"
_EXAMPLES = _PYTHON / "examples"

# Every pyproject extra that is not one framework's: the framework extras are
# whatever is left, and must be exactly the roster's.
_NON_FRAMEWORK_EXTRAS = {"llm", "mcp", "a2a", "local", "otel", "cli", "test", "all", "dev"}
_INDEPENDENCE = "integrations are mutually independent"


def _pyproject() -> dict[str, Any]:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves a module's string annotations through sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _adapter_class(attr: str) -> type[Adapter]:
    spec = ADAPTERS[attr]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls


def test_every_roster_entry_names_its_own_module_and_class() -> None:
    for attr, spec in ADAPTERS.items():
        assert spec.attr == attr
        assert spec.module == f".{attr}"
        cls = _adapter_class(attr)
        assert cls.__name__ == spec.cls
        assert issubclass(cls, Adapter)


def test_framework_extras_are_the_roster() -> None:
    extras = set(_pyproject()["project"]["optional-dependencies"])
    assert extras - _NON_FRAMEWORK_EXTRAS == {s.extra for s in ADAPTERS.values()}


def test_independence_contract_lists_every_adapter_module() -> None:
    contracts = _pyproject()["tool"]["importlinter"]["contracts"]
    [contract] = [c for c in contracts if c["name"].startswith(_INDEPENDENCE)]
    assert contract["type"] == "independence"
    assert sorted(contract["modules"]) == sorted(
        f"donkey_kit.integrations.{attr}" for attr in ADAPTERS
    )


def test_mypy_overrides_cover_every_framework_probe() -> None:
    # Each framework is absent from the base typecheck, so its top-level module
    # must be listed for mypy, or the base `mypy` job fails on the import.
    patterns = [
        module.removesuffix(".*")
        for override in _pyproject()["tool"]["mypy"]["overrides"]
        for module in override["module"]
    ]
    for attr, spec in ADAPTERS.items():
        for probe in spec.probe:
            if probe == "openai":  # the [llm] client, installed in the typecheck job
                continue
            assert any(probe == p or probe.startswith(f"{p}.") for p in patterns), (
                f"{attr}: no mypy override covers {probe!r}"
            )


def test_donkey_annotations_are_the_roster() -> None:
    # The annotations stay static so a type checker sees each adapter's type;
    # this keeps them equal to the roster rather than generated from it.
    tree = ast.parse(_DONKEY.read_text())
    [donkey] = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Donkey"]
    annotations: dict[str, str] = {}
    for node in donkey.body:
        if isinstance(node, ast.If) and ast.unparse(node.test) == "TYPE_CHECKING":
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    annotations[stmt.target.id] = ast.unparse(stmt.annotation)
    assert annotations == {attr: spec.cls for attr, spec in ADAPTERS.items()}


def test_verify_frameworks_covers_every_adapter_factory() -> None:
    harness = _load(_VERIFY, "ddk_verify_frameworks_roster")
    rows = harness.FRAMEWORKS
    # A dotted key is a second factory, or a variant, of the same framework.
    assert {key.split(".")[0] for key, *_ in rows} == set(ADAPTERS)
    for key, module, factory, *_ in rows:
        attr = key.split(".")[0]
        assert module == f"donkey_kit.integrations.{attr}", key
        assert factory in _adapter_class(attr).factories, (
            f"verify_frameworks {key!r} checks {factory!r}, not a declared factory"
        )
    # Every declared factory is signature-checked.
    for attr in ADAPTERS:
        checked = {factory for key, _m, factory, *_ in rows if key.split(".")[0] == attr}
        assert checked == set(_adapter_class(attr).factories), attr


def test_nightly_matrix_is_the_conformance_tested_set() -> None:
    workflow = yaml.safe_load(_NIGHTLY.read_text())
    matrix = workflow["jobs"]["matrix"]["strategy"]["matrix"]["framework"]
    assert set(matrix) == {attr for attr, s in ADAPTERS.items() if s.conformance_tested}


def test_nightly_framework_legs_match_adapters() -> None:
    # #748: one framework-legs entry per ADAPTERS key, pair for pair against
    # the registry's own (attr, extra) — not just the attr set — because the
    # FRAMEWORKS/DONKEY_CONTRACT_EXTRA key (e.g. "openai_agents") and the pip
    # extra (e.g. "openai-agents") are not always the same string. A mismatched
    # pair here would install the wrong extra for a leg's own
    # verify_frameworks.py / DONKEY_CONTRACT_EXTRA check.
    workflow = yaml.safe_load(_NIGHTLY.read_text())
    include = workflow["jobs"]["framework-legs"]["strategy"]["matrix"]["include"]
    pairs = {(entry["framework"], entry["extra"]) for entry in include}
    assert pairs == {(attr, s.extra) for attr, s in ADAPTERS.items()}
    env = workflow["jobs"]["framework-legs"]["env"]
    assert env["DONKEY_CONTRACT_EXTRA"] == "${{ matrix.extra }}"
    assert env["DONKEY_STRICT_EXAMPLE"] == "1"
    steps = [str(step.get("run", "")) for step in workflow["jobs"]["framework-legs"]["steps"]]
    assert any("verify_frameworks.py" in s and "--require-installed" in s for s in steps)
    assert any(s.strip() == "python -m pytest -q" for s in steps)
    assert any("examples/$FRAMEWORK/main.py" in s for s in steps)


@pytest.mark.parametrize("attr", sorted(ADAPTERS))
def test_every_adapter_has_a_runnable_example(attr: str) -> None:
    # The nightly matrix runs examples/<fw>/main.py for a promoted framework.
    assert (_EXAMPLES / attr / "main.py").is_file()


def test_known_limitations_name_roster_adapters_and_match_capabilities() -> None:
    # The conformance exemption table records the facts AdapterCapabilities
    # declares for each adapter's default factory, so neither can drift.
    suite = _load(_SUITE, "ddk_conformance_suite_roster")
    limits: dict[str, dict[str, str]] = suite.KNOWN_LIMITATIONS
    assert set(limits) <= set(ADAPTERS)
    for attr in ADAPTERS:
        caps = _adapter_class(attr).capabilities()
        exempt = limits.get(attr, {})
        assert ("gateway_identity_observed" in exempt) is (not caps.observes_last_call), attr
        assert ("typed_refusal_bridged" in exempt) is (not caps.typed_refusals), attr
        assert ("jwt_token_refreshed" in exempt) is (caps.transport == "framework"), attr
