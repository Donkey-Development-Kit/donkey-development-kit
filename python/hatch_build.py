"""Wheel build hook: package the simulator fixtures, minus their READMEs (#821).

A static ``force-include`` entry ships a directory whole, and hatch's
``exclude`` does not apply to force-included paths, so each fixture file is
force-included here instead. The fixture READMEs are capture provenance for
reviewers, not runtime data, and stay out of the published (immutable) wheel.
``fixtures.lock`` keeps its static entry in pyproject.toml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# source directory (relative to python/) -> its path inside the wheel
_FIXTURE_DIRS = {
    "tests/fixtures/rejections": "donkey_kit/simulator/_fixtures/rejections",
    "tests/fixtures/anypoint/llm_proxy": "donkey_kit/simulator/_fixtures/anypoint/llm_proxy",
    "tests/fixtures/anypoint/semantic_routing": (
        "donkey_kit/simulator/_fixtures/anypoint/semantic_routing"
    ),
}


class FixturesBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    """Force-include every simulator fixture file except READMEs."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:  # noqa: ARG002
        """Add each fixture file, READMEs excepted, to the wheel's force-include map."""
        root = Path(self.root)
        for src, dest in _FIXTURE_DIRS.items():
            for path in sorted((root / src).rglob("*")):
                if path.is_file() and path.name != "README.md":
                    rel = path.relative_to(root / src).as_posix()
                    build_data["force_include"][str(path)] = f"{dest}/{rel}"
