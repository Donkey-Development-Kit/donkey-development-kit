"""The architecture rules that import-linter can't express, as tests (#729).

``lint-imports`` holds the import graph (the contracts in ``pyproject.toml``).
This module holds the rest, one named test per invariant that ``ARCHITECTURE.md``
states, so each invariant cites the test that keeps it true:

- core's dependencies: httpx and the stdlib only, checked as an allowlist over
  every import in ``core/``, including those inside functions (§1.1);
- ``import donkey_kit`` loads only the production layers, checked in a fresh
  interpreter (the import-linter contract must ignore ``Donkey.simulate()``);
- no cross-package private import: ``from ..<pkg> import _name`` is banned
  across top-level packages, except through the named seam modules;
- the module size budget for ``core/`` (subpackages included), a ratchet over
  the files already past it;
- one shared client per credential plane (BG §1.1);
- config precedence as the code resolves it today (§2.1 is #727);
- the transport hook table matches both clients (BG §1.1, #179).

Framework-free at module level (the base-only job runs ``tests/unit``).
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import donkey_kit
from donkey_kit import Donkey
from donkey_kit import donkey as donkey_module
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient
from donkey_kit.integrations import ADAPTERS

_SRC = Path(donkey_kit.__file__).parent
_CORE = _SRC / "core"
_REPO = Path(__file__).resolve().parents[3]


def _module_name(path: Path) -> tuple[str, bool]:
    """The dotted module name of a file under ``src/``, and whether it is a package."""
    parts = list(path.relative_to(_SRC.parent).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _sources(root: Path) -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(root.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# --- core's dependencies (§1.1) -------------------------------------------------

# Third-party packages core may import at module level: httpx (the one runtime
# dependency core uses) and the stdlib backfills for the 3.10 floor. tomllib is
# stdlib from 3.11, so 3.10's sys.stdlib_module_names doesn't list it.
_CORE_MODULE_LEVEL_DEPS = {"httpx", "tomli", "tomllib", "typing_extensions"}
# ...and lazily, inside a function or under TYPE_CHECKING: the optional [otel]
# extra (BG §1.6), so `import donkey_kit` never needs it.
_CORE_LAZY_DEPS = _CORE_MODULE_LEVEL_DEPS | {"opentelemetry"}


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _third_party_imports(tree: ast.Module) -> Iterator[tuple[str, int, bool]]:
    """Each absolute import's top-level package, its line, and whether it runs at
    import time (module level, not in a function or a TYPE_CHECKING block)."""

    def walk(node: ast.AST, eager: bool) -> Iterator[tuple[str, int, bool]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                for alias in child.names:
                    yield alias.name.split(".")[0], child.lineno, eager
            elif isinstance(child, ast.ImportFrom) and not child.level and child.module:
                yield child.module.split(".")[0], child.lineno, eager
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                yield from walk(child, eager=False)
            elif isinstance(child, ast.If) and _is_type_checking(child.test):
                for stmt in child.body:
                    yield from walk(ast.Module(body=[stmt], type_ignores=[]), eager=False)
                for stmt in child.orelse:
                    yield from walk(ast.Module(body=[stmt], type_ignores=[]), eager=eager)
            else:
                yield from walk(child, eager)

    for name, line, eager in walk(tree, eager=True):
        if name not in sys.stdlib_module_names and name not in {"__future__", "donkey_kit"}:
            yield name, line, eager


def test_core_depends_only_on_httpx_and_the_stdlib() -> None:
    """ARCHITECTURE.md, "Layered architecture": core has zero framework
    dependencies. An allowlist, so a new package fails until it is argued for;
    imports inside functions count, which the base-only job can't see. The
    import-linter contract "core depends on httpx only" is the denylist twin."""
    offenders = []
    for path, tree in _sources(_CORE):
        for name, line, eager in _third_party_imports(tree):
            allowed = _CORE_MODULE_LEVEL_DEPS if eager else _CORE_LAZY_DEPS
            if name not in allowed:
                where = "at module level" if eager else "lazily"
                offenders.append(f"core/{path.name}:{line} imports {name} {where}")
    assert offenders == []


def test_the_core_dependency_scan_sees_lazy_imports() -> None:
    tree = ast.parse(
        "import httpx\n"
        "def f():\n    import openai\n"
        "if TYPE_CHECKING:\n    import pydantic\n"
        "else:\n    import yaml\n"
    )
    assert sorted(_third_party_imports(tree)) == [
        ("httpx", 1, True),
        ("openai", 3, False),
        ("pydantic", 5, False),
        ("yaml", 7, True),
    ]


# --- import donkey_kit loads only the production layers (#729) ---------------

# The packages `import donkey_kit` must never load: the dev-only siblings, the
# CLI, and the third-party packages only they need.
_NOT_ON_THE_IMPORT_PATH = (
    "donkey_kit._testing",
    "donkey_kit.cli",
    "donkey_kit.conformance",
    "donkey_kit.simulator",
    "pytest",
    "starlette",
    "typer",
    "uvicorn",
    "yaml",
)


def test_import_donkey_kit_loads_only_the_production_layers() -> None:
    """ARCHITECTURE.md, "Layered architecture": the root package reaches neither
    the dev-only siblings nor the CLI. The import-linter contract has
    to ignore Donkey.simulate()'s lazy import of simulator.inject, which would
    also hide that import turning eager, so this checks a fresh interpreter."""
    probe = (
        "import sys, donkey_kit\n"
        f"banned = {_NOT_ON_THE_IMPORT_PATH!r}\n"
        "print(sorted(m for m in sys.modules if m in banned or m.startswith("
        "tuple(b + '.' for b in banned))))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"


# --- no cross-package private imports (#719, #729) ----------------------------

# Private modules other top-level packages may import from, each for one reason.
# Importing a private NAME across packages is never allowed, from these or any
# other module.
_SEAMS = {
    "donkey_kit._testing": "the fixture seam the dev-only siblings reach Donkey through (#719)",
    "donkey_kit.core._verify": "the one home of the verification guards every layer raises (§0.3)",
    "donkey_kit.core._wire": "the one source of gateway wire names (#720)",
    "donkey_kit.integrations._base": (
        "the Adapter base type Donkey's lazy attributes return; the adapter "
        "contract is #726 (docs/adr/0004-*.md)"
    ),
}


def _is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _top(module: str) -> str:
    """The top-level package (or module) a dotted name belongs to; ``""`` for the root."""
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _private_prefix(module: str) -> str | None:
    """The module path up to its first private segment, if it has one."""
    parts = module.split(".")
    for i, part in enumerate(parts[1:], start=1):
        if _is_private(part):
            return ".".join(parts[: i + 1])
    return None


def _is_module(dotted: str) -> bool:
    path = _SRC.parent.joinpath(*dotted.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


def _cross_package_imports(
    module: str, is_package: bool, tree: ast.Module
) -> Iterator[tuple[int, str, str | None]]:
    """Every ``donkey_kit`` import in ``tree`` that reaches another top-level
    package: its line, the module it reaches, and the name imported from that
    module (``None`` when the import names the module itself)."""
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(tree):
        targets: list[tuple[str, str | None]] = []
        if isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = parts[: len(parts) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            if not target.startswith("donkey_kit"):
                continue
            for alias in node.names:
                full = f"{target}.{alias.name}"
                targets.append((full, None) if _is_module(full) else (target, alias.name))
        elif isinstance(node, ast.Import):
            targets = [(a.name, None) for a in node.names if a.name.startswith("donkey_kit")]
        for target, name in targets:
            if _top(target) != _top(module):
                yield node.lineno, target, name


def _private_imports(module: str, is_package: bool, tree: ast.Module) -> Iterator[str]:
    """The cross-package imports that reach a private name, or a private module
    other than a seam, as ``"line: description"``."""
    for line, target, name in _cross_package_imports(module, is_package, tree):
        if name is not None and _is_private(name):
            yield f"{line}: private name {name} from {target}"
            continue
        prefix = _private_prefix(target)
        if prefix is not None and prefix not in _SEAMS:
            yield f"{line}: private module {prefix}"


def _parsed_modules() -> Iterator[tuple[str, bool, ast.Module]]:
    for path, tree in _sources(_SRC):
        module, is_package = _module_name(path)
        yield module, is_package, tree


def test_no_private_import_across_top_level_packages() -> None:
    """A top-level package (core, llm, registry, tools, integrations, donkey,
    experimental, cli, simulator, conformance, _testing, the root) never
    imports another's private names, nor its private modules other than the
    named seams."""
    found = {
        module: hits
        for module, is_package, tree in _parsed_modules()
        if (hits := list(_private_imports(module, is_package, tree)))
    }
    assert found == {}


def test_every_seam_is_still_imported_across_packages() -> None:
    used = {
        _private_prefix(target)
        for module, is_package, tree in _parsed_modules()
        for _, target, _ in _cross_package_imports(module, is_package, tree)
    }
    stale = {seam: reason for seam, reason in _SEAMS.items() if seam not in used}
    assert stale == {}, "no longer imported across packages: drop them from _SEAMS"


@pytest.mark.parametrize(
    ("module", "source"),
    [
        # The three sites the review found at 11b806b (#729), fixed in #719.
        (
            "donkey_kit.conformance.harness",
            "from ..simulator.inject import _resolve as _refusal_fixture\n",
        ),
        ("donkey_kit.provisioning.cli", "from ..core.config import _TOML_NAME, DonkeyConfig\n"),
        ("donkey_kit.provisioning.doctor", "from ..core.config import _TOML_NAME, DonkeyConfig\n"),
        # A private module that is not a seam, relative and absolute.
        ("donkey_kit.donkey", "from .integrations import _httpx2_bridge\n"),
        ("donkey_kit.tools.session", "import donkey_kit.integrations._httpx2_bridge\n"),
    ],
)
def test_the_private_import_scan_flags_the_historical_sites(module: str, source: str) -> None:
    assert list(_private_imports(module, False, ast.parse(source))) != []


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("donkey_kit.registry.exchange", "from ..core import _verify\n"),
        ("donkey_kit.simulator.inject", "from .._testing import swap_transport\n"),
        ("donkey_kit.core.config", "from ._verify import REGION_HOSTS\n"),
        ("donkey_kit.integrations.adk", "from ._base import Adapter\n"),
    ],
)
def test_the_private_import_scan_allows_seams_and_same_package_imports(
    module: str, source: str
) -> None:
    assert list(_private_imports(module, False, ast.parse(source))) == []


# --- module size budget for core/ (#729) --------------------------------------

_CORE_MODULE_BUDGET = 500  # lines

# A ratchet: the core modules already past the budget, each at its line count
# when the budget landed. A ceiling may only come down. Lower it in the PR that
# shrinks the file, and drop the entry once the file is within budget. The
# transport split (#728) removes transport.py's entry.
_OVERSIZED_CORE_MODULES = {
    "config.py": 984,
    "errors.py": 1118,
    "lastcall.py": 828,
    "telemetry.py": 737,
    "transport.py": 2007,
}


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_core_modules_stay_within_the_size_budget() -> None:
    # Recursive, so a subpackage (the transport split, #728) is budgeted too.
    over = {
        name: _lines(path)
        for path in sorted(_CORE.rglob("*.py"))
        if (name := path.relative_to(_CORE).as_posix()) not in _OVERSIZED_CORE_MODULES
        and _lines(path) > _CORE_MODULE_BUDGET
    }
    assert over == {}, f"core modules past {_CORE_MODULE_BUDGET} lines: split them"


@pytest.mark.parametrize(("name", "ceiling"), sorted(_OVERSIZED_CORE_MODULES.items()))
def test_oversized_core_modules_only_shrink(name: str, ceiling: int) -> None:
    path = _CORE / name
    assert path.is_file(), f"core/{name} is gone: drop its entry from _OVERSIZED_CORE_MODULES"
    lines = _lines(path)
    assert lines > _CORE_MODULE_BUDGET, (
        f"core/{name} is within budget ({lines} lines): drop its ratchet entry"
    )
    assert lines <= ceiling, f"core/{name} grew from {ceiling} to {lines} lines: split it"
    assert lines == ceiling, f"core/{name} shrank to {lines} lines: lower its ceiling"


# --- one shared client per credential plane (BG §1.1) ------------------------

_CFG = DonkeyConfig(
    llm_proxy_url="https://proxy.example.com/p/",
    llm_proxy_client_id="cid",
    llm_proxy_client_secret="secret",
)


async def test_one_shared_client_per_credential_plane(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCHITECTURE.md, "How the pieces connect": a Donkey owns one data-plane
    and one control-plane client. The LLM client and every adapter share the
    data-plane pair; the registry gets the control-plane one."""
    # Build every adapter without its framework: adapters import it lazily.
    monkeypatch.setattr(donkey_module, "_missing_module", lambda probe: None)
    donkey = Donkey(_CFG)
    data, control = donkey._http, donkey._control_http
    blocking = donkey._sync_http_client()
    assert isinstance(data, DonkeyAsyncClient)
    assert isinstance(control, DonkeyAsyncClient)
    assert data is not control
    assert donkey._sync_http_client() is blocking

    assert donkey.llm._http is data
    assert donkey.llm._sync_http() is blocking
    assert donkey.registry._http is control
    for attr in sorted(ADAPTERS):
        adapter = getattr(donkey, attr)
        assert adapter._http is data, f"{attr} has its own async client"
        assert adapter._sync_http() is blocking, f"{attr} has its own blocking client"

    # The public view sends through the same pool.
    seen: list[httpx.Request] = []
    data._swap_transport(httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)))
    await donkey.http_client().get("https://proxy.example.com/p/models")
    assert [r.url.path for r in seen] == ["/p/models"]
    await donkey.aclose()


# --- config precedence (§2.1 as implemented; the target precedence is #727) ---


def test_config_precedence_code_env_files_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ARCHITECTURE.md, "Configuration": per key, set in code → env var →
    ``.donkey-kit.local.toml`` merged over ``.donkey-kit.toml`` → (only when
    neither exists) the user file → default, with each source recorded."""
    for name in list(os.environ):
        if name.startswith(("DONKEY_", "ANYPOINT_")):
            monkeypatch.delenv(name)
    project, xdg = tmp_path / "project", tmp_path / "xdg"
    project.mkdir()
    xdg.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    (xdg / ".donkey-kit.toml").write_text('[donkey]\napplication_name = "user"\n')

    cfg = DonkeyConfig.from_env()
    assert (cfg.application_name, cfg.source_of("application_name").kind) == ("user", "user")

    (project / ".donkey-kit.toml").write_text(
        '[donkey]\ntimeout_s = 10\nmax_retries = 5\nbusiness_group = "project"\n'
    )
    (project / ".donkey-kit.local.toml").write_text("[donkey]\ntimeout_s = 20\n")
    monkeypatch.setenv("DONKEY_BUSINESS_GROUP", "env")
    cfg = DonkeyConfig.from_env()

    def resolved(name: str) -> tuple[object, str]:
        return getattr(cfg, name), cfg.source_of(name).kind

    assert resolved("timeout_s") == (20.0, "local")
    assert resolved("max_retries") == (5, "project")
    assert resolved("business_group") == ("env", "env")
    # The user file is not read once the working directory has a config file.
    assert resolved("application_name") == (None, "default")
    assert resolved("environment") == ("Sandbox", "default")

    cfg = cfg.with_overrides(business_group="code")
    assert resolved("business_group") == ("code", "explicit")


# --- the transport hook table (BG §1.1, #179) --------------------------------

_HOOKS = ("_on_request", "_on_response", "_on_refusal", "_swap_transport")


def test_transport_hook_table_matches_both_clients() -> None:
    """ARCHITECTURE.md's hook table lists exactly the four lifecycle hooks, and
    the async client and its sync twin both define each one. Their behaviour is
    pinned in test_transport.py (test_default_hooks_are_noop_seams,
    test_on_request_called_once_across_retries,
    test_hooks_fire_once_across_the_401_refresh_path)."""
    doc = (_REPO / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert tuple(re.findall(r"^\s*\| `(_[a-z_]+)` \|", doc, re.MULTILINE)) == _HOOKS
    for cls in (DonkeyAsyncClient, DonkeyClient):
        missing = [hook for hook in _HOOKS if hook not in vars(cls)]
        assert missing == [], f"{cls.__name__} lacks {missing}"


def test_on_refusal_has_no_caller_as_the_hook_table_says() -> None:
    """The table says ``_on_refusal`` fires never: no caller today. When the
    typed-refusal bridge (#724) calls it, update the table's row with it."""
    callers = [
        f"{path.relative_to(_SRC)}:{node.lineno}"
        for path, tree in _sources(_SRC)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_on_refusal"
    ]
    assert callers == []
