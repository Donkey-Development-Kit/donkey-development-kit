"""The root CLI help advertises the supported SDK surface, not the refused
provisioning control plane from the build plan's do-not-build list (#458)."""

from __future__ import annotations

from typer.testing import CliRunner

from donkey_kit.provisioning.cli import app


def test_root_help_does_not_market_provisioning_control_plane() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for supported_surface in ("governed model access", "refusals", "budgets", "telemetry"):
        assert supported_surface in result.output
    assert "provisioning-as-code" not in result.output
