"""The CLI's command set after the legacy quarantine (#730, ADR 0008 in docs/adr/).

Four commands are visible: ``init``, ``doctor``, ``mock`` and ``test``. Three
hidden ones (``status``, ``publish``, ``verify``, BG §2.5) are
verification-blocked and exit 3 with a ``blocked on verification`` message
(§0.3). The refused provisioning control plane's commands were removed, so
typer rejects them as unknown (exit 2), not as blocked (exit 3).
"""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from donkey_kit.cli import app

runner = CliRunner()


def test_visible_commands_are_init_doctor_mock_test() -> None:
    commands = typer.main.get_command(app).commands
    visible = {name for name, command in commands.items() if not command.hidden}

    assert visible == {"init", "doctor", "mock", "test"}


def test_hidden_commands_are_only_the_blocked_platform_ones() -> None:
    commands = typer.main.get_command(app).commands
    hidden = {name for name, command in commands.items() if command.hidden}

    assert hidden == {"status", "publish", "verify"}


@pytest.mark.parametrize("command", ["status", "publish", "verify"])
def test_platform_commands_are_honestly_blocked(command: str) -> None:
    result = runner.invoke(app, [command])

    assert result.exit_code == 3
    assert "blocked on verification:" in result.output
    assert "See docs/verified-apis.md" in result.output


def test_publish_if_changed_is_blocked_too() -> None:
    result = runner.invoke(app, ["publish", "--if-changed"])

    assert result.exit_code == 3
    assert "blocked on verification:" in result.output


@pytest.mark.parametrize("command", ["plan", "apply", "drift", "lint", "generate", "validate"])
def test_removed_control_plane_commands_are_unknown(command: str) -> None:
    result = runner.invoke(app, [command])

    assert result.exit_code == 2
    assert "blocked on verification" not in result.output
