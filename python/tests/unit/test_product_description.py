"""Product descriptions stay aligned with the provisioning boundary (#459)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_README = _PYTHON_ROOT.parent / "README.md"


def _opening_description(readme: Path) -> str:
    return readme.read_text().split("# Donkey Development Kit", maxsplit=1)[1].split(
        "> **Project status", maxsplit=1
    )[0]


@pytest.mark.parametrize(
    "readme",
    [
        pytest.param(_PYTHON_ROOT / "README.md", id="package-readme"),
        pytest.param(
            _REPOSITORY_README,
            id="repository-readme",
            marks=pytest.mark.skipif(
                _PYTHON_ROOT.name != "python" or not _REPOSITORY_README.is_file(),
                reason="repository README is not included in the sdist",
            ),
        ),
    ],
)
def test_readme_headline_does_not_promise_provisioning_as_code(readme: Path) -> None:
    description = " ".join(_opening_description(readme).split())
    assert "governed model and tool access" in description
    assert "governed tool access, and provisioning-as-code" not in description


def test_package_description_does_not_promise_provisioning_as_code() -> None:
    metadata = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())
    description = metadata["project"]["description"]
    assert "governed model and tool access" in description
    assert "governed tools, provisioning-as-code" not in description
