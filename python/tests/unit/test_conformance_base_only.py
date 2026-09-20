"""The base-only guarantee for the conformance kit (the layered architecture, BG §1.5).

The pytest11 entry point auto-loads ``donkey_kit.conformance.plugin`` on EVERY
pytest run once ``donkey-kit`` is installed — including this base-only job,
which installs ``[dev]`` only (no ``openai``, no ``starlette``). So importing the
package and the auto-loaded modules must NOT require any framework or ``openai``.

Unguarded on purpose (no ``importorskip``): this module runs under ``[dev]`` in
the base-only job, so a module-top ``openai``/framework import in any of these
modules would make these imports fail *there*. The AST assertion also catches
such an import in environments where the extra IS installed.
"""

from __future__ import annotations

import ast
import inspect
from types import ModuleType

import donkey_kit.conformance as conf
import donkey_kit.conformance.harness as harness_mod
import donkey_kit.conformance.plugin as plugin_mod
import donkey_kit.conformance.report as report_mod
import donkey_kit.conformance.suite as suite_mod


def test_package_and_modules_import_without_openai_or_framework() -> None:
    # The public surface is reachable with [dev] alone.
    assert callable(conf.validate_known_limitations)
    assert callable(conf.render_report)
    assert len(conf.SCENARIOS) == 4
    # The auto-loaded plugin exposes its hooks without importing openai/harness.
    assert callable(plugin_mod.pytest_addoption)
    assert callable(plugin_mod.pytest_collection)
    # Lazy harness symbols resolve through __getattr__ (still no openai needed).
    assert callable(conf.run_conformance)


def _module_scope_import_roots(module: ModuleType) -> set[str]:
    """Root package name of every MODULE-SCOPE import in ``module``. Imports
    nested inside a function/class body (the lazy ones) are excluded — which is
    exactly the point."""
    tree = ast.parse(inspect.getsource(module))
    roots: set[str] = set()
    for node in tree.body:  # only direct children of the module = module scope
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_no_module_top_heavy_import() -> None:
    # openai and the web frameworks must never be imported at module scope in any
    # conformance module: the entry point loads the plugin on the base import
    # path, and the harness is loaded lazily by the plugin, so none of them may
    # drag openai/starlette/uvicorn on import. An AST scan of module-scope imports
    # catches every form (`import openai` AND `from openai import ...`).
    forbidden = {"openai", "starlette", "uvicorn"}
    for module in (conf, suite_mod, report_mod, plugin_mod, harness_mod):
        roots = _module_scope_import_roots(module)
        leaked = roots & forbidden
        assert not leaked, f"{module.__name__} imports {leaked} at module top"
