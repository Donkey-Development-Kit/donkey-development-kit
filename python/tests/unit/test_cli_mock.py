"""``donkey mock`` (BG §1.4): flag wiring and the missing-``[local]``-extra
guidance.

Needs only ``[dev]`` (typer): ``serve`` is monkeypatched so neither uvicorn nor
starlette is required, and importing ``donkey_kit.simulator.server`` pulls in
no web framework at module top. So this runs in the base-only job too.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from donkey_kit.provisioning.cli import app

runner = CliRunner()


def _combined(result: object) -> str:
    """click merges stderr into output on <8.2 and separates it on >=8.2; read
    both so the assertion holds across the ``floors, never ceilings`` range."""
    text = getattr(result, "stdout", "") or ""
    try:
        text += result.stderr  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        pass  # old click: stderr already folded into stdout
    return text


def test_mock_missing_local_extra_prints_pip_install_and_exits_1(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(**_: object) -> None:
        raise ImportError("No module named 'uvicorn'")

    monkeypatch.setattr("donkey_kit.simulator.server.serve", _raise)
    result = runner.invoke(app, ["mock"])

    assert result.exit_code == 1  # install prompt, NOT the exit-3 verification block
    out = _combined(result)
    assert 'pip install "donkey-kit[local]"' in out
    assert "blocked on verification" not in out  # this is not a verification-discipline gate


def test_mock_wires_host_and_port_through_to_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _spy(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("donkey_kit.simulator.server.serve", _spy)
    result = runner.invoke(app, ["mock", "--host", "0.0.0.0", "--port", "9999"])

    assert result.exit_code == 0
    # No --scenario: config is None so the no-scenario boot path is byte-identical.
    assert captured == {"host": "0.0.0.0", "port": 9999, "config": None}


def test_mock_defaults_bind_localhost_8080(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "donkey_kit.simulator.server.serve", lambda **kw: captured.update(kw)
    )
    result = runner.invoke(app, ["mock"])

    assert result.exit_code == 0
    assert captured == {"host": "127.0.0.1", "port": 8080, "config": None}


def test_mock_parses_scenarios_into_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeatable --scenario flags are parsed into the SimulatorConfig handed to
    serve() (#188)."""
    from donkey_kit.simulator.app import SimulatorConfig
    from donkey_kit.simulator.scenarios import (
        BudgetScenario,
        InjectionScenario,
        PiiBlockScenario,
    )

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "donkey_kit.simulator.server.serve", lambda **kw: captured.update(kw)
    )
    result = runner.invoke(
        app,
        [
            "mock",
            "--scenario",
            "pii_block:every=5",
            "--scenario",
            "budget:limit=20000,window=60s",
            "--scenario",
            "injection:on-pattern=ignore previous",
        ],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert isinstance(config, SimulatorConfig)
    kinds = {type(s) for s in config.scenarios}
    assert kinds == {PiiBlockScenario, BudgetScenario, InjectionScenario}


def test_mock_invalid_scenario_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed --scenario is a usage error (exit 2), not a stack trace."""
    monkeypatch.setattr(
        "donkey_kit.simulator.server.serve", lambda **kw: None
    )
    result = runner.invoke(app, ["mock", "--scenario", "nonsense:foo=1"])

    assert result.exit_code == 2
    assert "Invalid --scenario" in _combined(result)
