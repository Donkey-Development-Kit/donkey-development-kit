"""A plain install carries no pydantic, and ``import donkey_kit`` needs none (#730).

pydantic was a base dependency only for the deleted provisioning spec loader.
These tests pin both halves: the declared base dependencies, and the import
itself, run in a fresh interpreter where importing pydantic (or the ``[cli]``
extra's typer, or pyyaml) fails. The import half walks every library module
(everything but ``donkey_kit.cli``), not just the package root.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[2]

_BLOCKED = ("pydantic", "pydantic_core", "typer", "yaml")


def _base_dependency_names() -> set[str]:
    metadata = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())
    names = set()
    for requirement in metadata["project"]["dependencies"]:
        name = requirement.split(";")[0]
        for sep in ("<", ">", "=", "!", "~", "[", " "):
            name = name.split(sep)[0]
        names.add(name.strip().lower())
    return names


def test_base_dependencies_have_no_pydantic() -> None:
    names = _base_dependency_names()
    assert "httpx" in names  # guards the parse itself
    assert "pydantic" not in names
    assert "pyyaml" not in names


def test_import_donkey_kit_works_without_pydantic() -> None:
    script = textwrap.dedent(
        f"""
        import importlib.abc
        import sys

        BLOCKED = {_BLOCKED!r}

        class Block(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ModuleNotFoundError(f"blocked for the test: {{name}}", name=name)
                return None

        sys.meta_path.insert(0, Block())
        for name in list(sys.modules):
            if name.split(".")[0] in BLOCKED:
                del sys.modules[name]

        import importlib
        import pkgutil

        import donkey_kit
        import donkey_kit.experimental
        from donkey_kit import Donkey, DonkeyConfig

        Donkey(DonkeyConfig(llm_proxy_url="https://proxy", telemetry=False))

        # Every library module, not just the root: none may need a blocked
        # package. donkey_kit.cli is the one that needs typer (the [cli] extra);
        # a module whose own optional extra is missing is skipped.
        for info in pkgutil.walk_packages(donkey_kit.__path__, "donkey_kit."):
            if info.name.split(".")[:2] == ["donkey_kit", "cli"]:
                continue
            try:
                importlib.import_module(info.name)
            except ImportError as exc:
                # A curated install-prompt ImportError chains the real one.
                for err in (exc, exc.__cause__):
                    missing = (getattr(err, "name", None) or "").split(".")[0]
                    assert missing not in BLOCKED, (info.name, repr(exc))
        loaded = sorted(n for n in sys.modules if n.split(".")[0] in BLOCKED)
        assert not loaded, loaded
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
