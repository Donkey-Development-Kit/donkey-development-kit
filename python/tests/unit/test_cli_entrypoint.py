"""The ``donkey`` entry point and global flags (#811): ``--config``/``--env``
apply to ``init`` only and every other command rejects them, the console script
runs ``main()`` so a ``DonkeyError`` exits 1 without a traceback, and importing
the CLI without typer raises a curated ``ImportError`` instead of ``SystemExit``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from donkey_kit.provisioning import cli
from donkey_kit.provisioning.cli import app

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

_PYTHON_ROOT = Path(__file__).resolve().parents[2]

runner = CliRunner()


def _combined(result: object) -> str:
    text = getattr(result, "stdout", "") or ""
    try:
        text += result.stderr  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        pass
    return text


@pytest.fixture
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for var in ("ANYPOINT_ENV", "ANYPOINT_REGION", "ANYPOINT_CLIENT_ID"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param(["--config", "./cfg.toml"], id="config"),
        pytest.param(["--env", "Sandbox"], id="env"),
    ],
)
def test_doctor_rejects_init_only_flags(isolated: Path, flags: list[str]) -> None:
    def _must_not_run(*_a: object, **_k: object) -> object:
        raise AssertionError("doctor ran against a configuration other than the one requested")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("donkey_kit.provisioning.doctor.run_diagnostics", _must_not_run)
        result = runner.invoke(app, [*flags, "doctor"])

    assert result.exit_code == 2, _combined(result)
    assert f"{flags[0]} only apply to `donkey init`" in _combined(result)
    assert "ANYPOINT_ENV" in _combined(result)


def test_init_honours_env_flag(isolated: Path) -> None:
    result = runner.invoke(app, ["--env", "Production", "init"])

    assert result.exit_code == 0, _combined(result)
    table = tomllib.loads((isolated / ".donkey-kit.toml").read_text())["donkey"]
    assert table["environment"] == "Production"


def test_console_script_targets_main() -> None:
    metadata = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["scripts"]["donkey"] == "donkey_kit.provisioning.cli:main"


def test_main_reports_donkey_error_without_traceback(
    isolated: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An unknown region is a ConfigError raised while resolving config.
    monkeypatch.setenv("ANYPOINT_REGION", "mars")
    monkeypatch.setattr(sys, "argv", ["donkey", "init"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "region is 'mars'" in err
    assert "Traceback" not in err


def test_import_without_typer_raises_curated_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "typer", None)  # makes `import typer` fail
    monkeypatch.delitem(sys.modules, "donkey_kit.provisioning.cli")

    with pytest.raises(ImportError, match=r'pip install "donkey-kit\[cli\]"'):
        importlib.import_module("donkey_kit.provisioning.cli")
