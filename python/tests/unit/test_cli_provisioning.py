"""Smoke tests for the hidden provisioning CLI surface (#461).

The provisioning control plane is cut for Phase 1, so these commands stay
hidden and verification-gated. The tests pin the honest §0.3 contract: local
spec validation works, while commands needing an unverified platform API exit
3 with a clear ``blocked on verification`` message.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from donkey_kit.provisioning.cli import app

runner = CliRunner()

_VALID_SPEC = """\
apiVersion: donkey/v1
kind: DonkeySpec
metadata:
  name: orders-agent
  environment: Sandbox
mcpBridges:
  - name: orders
    gateway: flex-sandbox
"""


def _write_spec(tmp_path: Path, content: str = _VALID_SPEC) -> Path:
    path = tmp_path / "donkey.yaml"
    path.write_text(content)
    return path


def test_validate_accepts_a_valid_spec(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path)

    result = runner.invoke(app, ["validate", "--file", str(spec)])

    assert result.exit_code == 0, result.output
    assert "OK: orders-agent" in result.output
    assert "1 MCP bridge(s)" in result.output
    assert "env Sandbox" in result.output


@pytest.mark.parametrize(
    "content",
    [
        "metadata: [",
        "apiVersion: donkey/v1\nkind: DonkeySpec\n",
    ],
)
def test_validate_rejects_invalid_specs(content: str, tmp_path: Path) -> None:
    spec = _write_spec(tmp_path, content)

    result = runner.invoke(app, ["validate", "--file", str(spec)])

    assert result.exit_code == 2
    assert f"Invalid spec {spec}:" in result.output
    assert "blocked on verification" not in result.output


def test_provisioning_commands_stay_hidden() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    for command in (
        "validate",
        "plan",
        "apply",
        "drift",
        "lint",
        "generate",
        "status",
        "publish",
        "verify",
    ):
        assert command not in result.output


@pytest.mark.parametrize(
    "command",
    ["plan", "apply", "drift", "lint", "generate"],
)
def test_spec_commands_are_honestly_blocked(
    command: str,
    tmp_path: Path,
) -> None:
    spec = _write_spec(tmp_path)

    result = runner.invoke(app, [command, "--file", str(spec)])

    assert result.exit_code == 3
    assert "blocked on verification:" in result.output
    assert "See docs/verified-apis.md" in result.output


@pytest.mark.parametrize("command", ["status", "publish", "verify"])
def test_platform_commands_are_honestly_blocked(command: str) -> None:
    result = runner.invoke(app, [command])

    assert result.exit_code == 3
    assert "blocked on verification:" in result.output
    assert "See docs/verified-apis.md" in result.output
