"""The root CLI help advertises the supported SDK surface, not the refused
provisioning control plane from the build plan's do-not-build list (#458)."""

from __future__ import annotations

from typer.testing import CliRunner

from donkey_kit.cli import app


def test_root_help_does_not_market_provisioning_control_plane() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for supported_surface in ("governed model access", "refusals", "budgets", "telemetry"):
        assert supported_surface in result.output
    assert "provisioning-as-code" not in result.output


def test_every_command_summary_is_one_line() -> None:
    """The root help shows each command's first docstring paragraph; a hard line
    break there renders as a broken row in the command list (#1751)."""
    import typer.main

    group = typer.main.get_command(app)
    for name, command in group.commands.items():  # type: ignore[attr-defined]
        summary = (command.help or "").strip().split("\n\n")[0]
        assert "\n" not in summary, f"`donkey {name}` help summary spans lines: {summary!r}"
