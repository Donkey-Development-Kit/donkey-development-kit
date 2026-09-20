"""``donkey test`` (#201): a thin front end to ``pytest --donkey-conformance``.

It is deliberately *not* a re-implementation of the conformance runner — it
shells out to pytest, forwards the customer's ``--agent MODULE:FACTORY`` plus
any trailing pytest args, and propagates pytest's exit code as its own. A
missing pytest (the ``[test]`` extra not installed) is a typed install prompt
with exit 1, never a stack trace and never a "blocked on verification".

Framework-free (Surface 1): monkeypatches ``subprocess.run`` so no real pytest
runs. Base-only-safe.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from donkey_kit.provisioning import cli
from donkey_kit.provisioning.cli import app

runner = CliRunner()


def _combined(result: object) -> str:
    text = getattr(result, "stdout", "") or ""
    try:
        text += result.stderr  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        pass
    return text


class _FakeCompleted:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


@pytest.fixture
def captured_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the argv passed to subprocess.run and return a controllable
    exit code, without ever launching pytest."""
    captured: dict[str, Any] = {"argv": None, "returncode": 0}

    def fake_run(argv: list[str], *args: Any, **kwargs: Any) -> _FakeCompleted:
        captured["argv"] = argv
        return _FakeCompleted(captured["returncode"])

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    return captured


def test_test_invokes_pytest_with_conformance_flag(captured_run: dict[str, Any]) -> None:
    result = runner.invoke(app, ["test"])
    assert result.exit_code == 0, _combined(result)

    argv = captured_run["argv"]
    assert argv is not None
    # It shells out to *this* interpreter's pytest, and passes the plugin flag.
    assert argv[:3] == [__import__("sys").executable, "-m", "pytest"]
    assert "--donkey-conformance" in argv


def test_test_forwards_agent_and_trailing_pytest_args(captured_run: dict[str, Any]) -> None:
    result = runner.invoke(
        app,
        ["test", "--agent", "my.pkg:make_agent", "-k", "governance", "-x"],
    )
    assert result.exit_code == 0, _combined(result)

    argv = captured_run["argv"]
    assert "--donkey-conformance" in argv
    # The agent factory is forwarded to the plugin.
    assert "--agent" in argv
    assert "my.pkg:make_agent" in argv
    # Trailing pytest args pass straight through.
    assert "-k" in argv and "governance" in argv
    assert "-x" in argv


def test_test_propagates_nonzero_exit_code(captured_run: dict[str, Any]) -> None:
    captured_run["returncode"] = 5  # pytest: no tests collected
    result = runner.invoke(app, ["test"])
    assert result.exit_code == 5, _combined(result)


def test_test_propagates_failure_exit_code(captured_run: dict[str, Any]) -> None:
    captured_run["returncode"] = 1  # pytest: tests failed
    result = runner.invoke(app, ["test"])
    assert result.exit_code == 1, _combined(result)


def test_test_missing_pytest_is_typed_install_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Simulate the [test] extra not being installed.
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pytest":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(cli.importlib.util, "find_spec", fake_find_spec)

    # subprocess.run must never be reached in this path.
    def exploding_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess.run should not run when pytest is absent")

    monkeypatch.setattr(cli.subprocess, "run", exploding_run)

    result = runner.invoke(app, ["test"])
    assert result.exit_code == 1
    out = _combined(result)
    assert "pip install" in out
    assert "test" in out  # names the [test] extra
    # An install prompt, not a verification block.
    assert "blocked on verification" not in out
