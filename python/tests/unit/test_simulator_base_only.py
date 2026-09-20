"""The base-only guarantee (the layered architecture, BG §1.4): importing the simulator package must
NOT require the ``[local]`` extra.

Unguarded on purpose — this runs in the base-only CI job under ``[dev]`` only,
where ``starlette``/``uvicorn`` are absent, so a module-top framework import
would make these imports fail *there*. The AST assertion below also catches such
an import in environments where the extra IS installed.
"""

from __future__ import annotations

import ast
import inspect
from types import ModuleType

import donkey_kit.simulator as sim
import donkey_kit.simulator.app as app_mod
import donkey_kit.simulator.fixtures as fixtures_mod
import donkey_kit.simulator.inject as inject_mod
import donkey_kit.simulator.server as server_mod


def test_package_and_submodules_import_without_local_extra() -> None:
    assert callable(sim.build_app)
    assert callable(sim.serve)
    assert callable(fixtures_mod.load)
    # In-process injection (#190) is framework-free too — importable with [dev]
    # only, so simulate() carries no web-framework dependency onto the base path.
    assert callable(inject_mod.simulate)


def _module_scope_import_roots(module: ModuleType) -> set[str]:
    """Root package name of every MODULE-SCOPE import in ``module``. Imports
    nested inside a function/class body — i.e. the lazy ones — are *not* module
    scope, so they are excluded (which is exactly the point)."""
    tree = ast.parse(inspect.getsource(module))
    roots: set[str] = set()
    for node in tree.body:  # only direct children of the module = module scope
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_module_top_framework_import() -> None:
    # starlette/uvicorn must be imported lazily INSIDE build_app()/serve(), never
    # at module scope — otherwise `import donkey_kit.simulator` would drag a web
    # framework onto the base import path the base-only CI job protects. An AST
    # scan of module-scope imports catches every form (`import starlette` AND
    # `from starlette.applications import Starlette`), unlike a hasattr() probe.
    for module in (app_mod, server_mod, fixtures_mod, inject_mod):
        roots = _module_scope_import_roots(module)
        assert "starlette" not in roots, f"{module.__name__} imports starlette at module top"
        assert "uvicorn" not in roots, f"{module.__name__} imports uvicorn at module top"
