"""``donkey dev`` (BG §2.9.3, #1284): target loading, flag wiring, and the usage
and install errors. ``uvicorn.run`` is monkeypatched, so nothing binds a port."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

pytest.importorskip("a2a", reason="needs the [a2a] extra")
pytest.importorskip("uvicorn", reason="needs the [local] extra")

from donkey_kit.cli import app

runner = CliRunner()

_AGENT = '''
from donkey_kit import A2ATask, Donkey

@Donkey.agent(name="echo")
async def handle(task: A2ATask) -> str:
    """Echoes."""
    return task.input

def plain(task):
    return task.input
'''


def _combined(result: object) -> str:
    text = getattr(result, "stdout", "") or ""
    try:
        text += result.stderr  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        pass
    return text


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    (tmp_path / "dev_agent_1284.py").write_text(_AGENT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, "dev_agent_1284", raising=False)
    captured: dict[str, Any] = {}

    def _run(app_: Any, **kwargs: Any) -> None:
        captured["app"] = app_
        captured.update(kwargs)

    monkeypatch.setattr("uvicorn.run", _run)
    return captured


def test_dev_serves_the_target_behind_the_ingress(project: dict[str, Any]) -> None:
    result = runner.invoke(app, ["dev", "dev_agent_1284:handle", "--port", "9123"])
    assert result.exit_code == 0, _combined(result)
    assert project["host"] == "127.0.0.1"
    assert project["port"] == 9123
    assert "SIMULATED gateway ingress" in result.stdout
    assert "x-donkey-simulator: true" in result.stdout


def test_an_unmarked_handler_is_a_usage_error(project: dict[str, Any]) -> None:
    result = runner.invoke(app, ["dev", "dev_agent_1284:plain"])
    assert result.exit_code == 2
    assert "@donkey.agent" in _combined(result)
    assert "app" not in project


@pytest.mark.parametrize(
    "target", ["dev_agent_1284", "no_such_module_1284:x", "dev_agent_1284:nope"]
)
def test_an_unloadable_target_is_a_usage_error(project: dict[str, Any], target: str) -> None:
    result = runner.invoke(app, ["dev", target])
    assert result.exit_code == 2
    assert "Cannot load" in _combined(result)


def test_budget_is_refused_before_loading(project: dict[str, Any]) -> None:
    result = runner.invoke(
        app, ["dev", "dev_agent_1284:handle", "--scenario", "budget:limit=10,window=60s"]
    )
    assert result.exit_code == 2
    assert "model tokens" in _combined(result)


def test_a_missing_extra_is_an_install_prompt(
    project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _missing(*_: object, **__: object) -> None:
        raise ImportError("donkey.serve() needs the 'a2a' extra")

    monkeypatch.setattr("donkey_kit.a2a.build_app", _missing)
    result = runner.invoke(app, ["dev", "dev_agent_1284:handle"])
    assert result.exit_code == 1
    assert 'pip install "donkey-kit[a2a,local]"' in _combined(result)
