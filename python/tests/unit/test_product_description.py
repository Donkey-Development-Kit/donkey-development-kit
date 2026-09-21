"""Product descriptions stay aligned with the provisioning boundary (#459)."""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from donkey_kit.provisioning.cli import app

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib


def test_user_facing_descriptions_do_not_promise_provisioning_as_code() -> None:
    python_root = Path(__file__).resolve().parents[2]

    assert "provisioning-as-code" not in (python_root.parent / "README.md").read_text()
    assert "provisioning-as-code" not in (python_root / "README.md").read_text()

    metadata = tomllib.loads((python_root / "pyproject.toml").read_text())
    assert "provisioning-as-code" not in metadata["project"]["description"]

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "provisioning-as-code" not in result.output
    assert "governed model and tool access" in result.output
