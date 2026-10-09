"""The ``donkey`` entry point and global flags (#811, #952): ``--env`` applies
to ``init`` only, while ``--config`` also selects the file for ``doctor``. The console script
runs ``main()`` so a ``DonkeyError`` exits 1 without a traceback, and importing
the CLI without typer raises a curated ``ImportError`` instead of ``SystemExit``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from donkey_kit import cli
from donkey_kit.cli import app

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


def test_doctor_rejects_init_only_env(isolated: Path) -> None:
    def _must_not_run(*_a: object, **_k: object) -> object:
        raise AssertionError("doctor ran against a configuration other than the one requested")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("donkey_kit.cli.doctor.run_diagnostics", _must_not_run)
        result = runner.invoke(app, ["--env", "Sandbox", "doctor"])

    assert result.exit_code == 2, _combined(result)
    assert "--env only apply to `donkey init`" in _combined(result)
    assert "ANYPOINT_ENV" in _combined(result)


def test_doctor_uses_explicit_config_with_env_precedence(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donkey_kit.cli.doctor import ProbeResult
    from donkey_kit.core.config import DonkeyConfig

    config = isolated / "chosen.toml"
    config.write_text('[donkey]\nllm_proxy_url = "https://file.example/proxy/"\n')
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://env.example/proxy/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "secret")
    seen: list[DonkeyConfig] = []

    def _probe(cfg: DonkeyConfig, _model: str) -> ProbeResult:
        seen.append(cfg)
        return ProbeResult(None, None)

    monkeypatch.setattr("donkey_kit.cli.doctor._live_probe", _probe)
    result = runner.invoke(app, ["--config", str(config), "doctor"])

    assert result.exit_code == 0, _combined(result)
    assert len(seen) == 1
    assert seen[0].llm_proxy_url == "https://env.example/proxy/"
    assert str(config) not in _combined(result)  # env wins over the named file


def test_doctor_uses_named_file_when_env_is_absent(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = isolated / "chosen.toml"
    config.write_text('[donkey]\nllm_proxy_url = "https://file.example/proxy/"\n')
    monkeypatch.delenv("DONKEY_LLM_PROXY_URL", raising=False)
    result = runner.invoke(app, ["--config", str(config), "doctor"])

    assert result.exit_code == 1  # missing credentials; no probe is sent
    assert "project file (1/3 llm fields)" in _combined(result)
    assert "file.example" in _combined(result)


def test_other_commands_reject_config(isolated: Path) -> None:
    result = runner.invoke(app, ["--config", "./cfg.toml", "mock"])

    assert result.exit_code == 2
    assert "--config only apply to `donkey init` or `donkey doctor`" in _combined(result)


def test_named_config_preserves_explicit_override_precedence(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donkey_kit.core.config import DonkeyConfig

    config = isolated / "chosen.toml"
    config.write_text('[donkey]\nllm_proxy_url = "https://file.example/proxy/"\n')
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://env.example/proxy/")

    resolved = DonkeyConfig.resolve(path=config, llm_proxy_url="https://explicit.example/proxy/")

    assert resolved.llm_proxy_url == "https://explicit.example/proxy/"


def test_init_honours_env_flag(isolated: Path) -> None:
    result = runner.invoke(app, ["--env", "Production", "init"])

    assert result.exit_code == 0, _combined(result)
    table = tomllib.loads((isolated / ".donkey-kit.toml").read_text())["donkey"]
    assert table["environment"] == "Production"


def test_console_script_targets_main() -> None:
    metadata = tomllib.loads((_PYTHON_ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["scripts"]["donkey"] == "donkey_kit.cli:main"


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
    # Drop the package and every command module, so each re-runs its imports.
    cli_modules = [m for m in sys.modules if m.split(".")[:2] == ["donkey_kit", "cli"]]
    for name in cli_modules:
        monkeypatch.delitem(sys.modules, name)

    with pytest.raises(ImportError, match=r'pip install "donkey-kit\[cli\]"'):
        importlib.import_module("donkey_kit.cli")
