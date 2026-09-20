"""``donkey init`` (#201): writes a commented ``.donkey-kit.toml`` from the
resolved config, names EVERY missing required field at once (reusing the §2.1
all-at-once ``DonkeyConfig.validated`` report), never writes a secret into the
committed file, and is idempotent — a bare re-run does not clobber an existing
file (``--force`` regenerates).

Framework-free: needs only ``[dev]`` (typer). Runs in the base-only CI job.
"""

from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from donkey_kit.provisioning.cli import app

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - 3.10 backfill
    import tomli as tomllib

runner = CliRunner()

# The three LLM-proxy secrets + the control-plane secret that must NEVER be
# written into the committed .donkey-kit.toml (they belong in env or the
# gitignored .donkey-kit.local.toml).
_SECRET_VALUES = {
    "ANYPOINT_CLIENT_SECRET": "cp-super-secret",
    "DONKEY_LLM_PROXY_CLIENT_SECRET": "proxy-super-secret",
    "DONKEY_LLM_PROXY_KEY": "bearer-super-secret",
}

_REQUIRED_ENV = (
    "ANYPOINT_CLIENT_ID",
    "ANYPOINT_CLIENT_SECRET",
    "ANYPOINT_ORG_ID",
    "DONKEY_LLM_PROXY_URL",
    "DONKEY_LLM_PROXY_CLIENT_ID",
    "DONKEY_LLM_PROXY_CLIENT_SECRET",
    "DONKEY_LLM_PROXY_KEY",
    "DONKEY_APP_NAME",
    "DONKEY_BUSINESS_GROUP",
)


def _combined(result: object) -> str:
    text = getattr(result, "stdout", "") or ""
    try:
        text += result.stderr  # type: ignore[attr-defined]
    except (ValueError, AttributeError):
        pass
    return text


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    """A pristine cwd with none of the donkey env vars set, so each test opts
    into exactly the values it wants."""
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]
    for var in _REQUIRED_ENV:
        monkeypatch.delenv(var, raising=False)


def test_init_writes_commented_toml_with_resolved_values(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: object
) -> None:
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cp-client-id")
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org-123")
    monkeypatch.setenv("ANYPOINT_ENV", "Production")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.internal")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "proxy-client-id")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, _combined(result)

    from pathlib import Path

    written = Path(str(tmp_path)) / ".donkey-kit.toml"
    assert written.is_file()
    text = written.read_text()
    # It is a *commented* file, not a bare dump.
    assert text.lstrip().startswith("#")
    # It parses as valid TOML with a [donkey] table carrying the resolved values.
    table = tomllib.loads(text)["donkey"]
    assert table["client_id"] == "cp-client-id"
    assert table["org_id"] == "org-123"
    assert table["environment"] == "Production"
    assert table["llm_proxy_url"] == "https://gw.example.internal"
    assert table["llm_proxy_client_id"] == "proxy-client-id"


def test_init_never_writes_secrets(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: object
) -> None:
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cp-client-id")
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org-123")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://gw.example.internal")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "proxy-client-id")
    for var, val in _SECRET_VALUES.items():
        monkeypatch.setenv(var, val)

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, _combined(result)

    from pathlib import Path

    text = (Path(str(tmp_path)) / ".donkey-kit.toml").read_text()
    # No secret VALUE ever lands in the committed file.
    for val in _SECRET_VALUES.values():
        assert val not in text
    # And the secret KEYS are not active keys in the parsed table.
    table = tomllib.loads(text).get("donkey", {})
    assert "client_secret" not in table
    assert "llm_proxy_client_secret" not in table
    assert "llm_proxy_key" not in table


def test_init_lists_all_missing_required_fields_at_once(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    # Nothing configured: init still bootstraps a file (exit 0) but names every
    # missing required field, not just the first (§2.1 all-at-once report).
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, _combined(result)

    out = _combined(result)
    # Control-plane AND llm required fields both surfaced in one run.
    assert "ANYPOINT_CLIENT_ID" in out
    assert "ANYPOINT_CLIENT_SECRET" in out
    assert "ANYPOINT_ORG_ID" in out
    assert "DONKEY_LLM_PROXY_URL" in out
    assert "DONKEY_LLM_PROXY_CLIENT_ID" in out
    assert "DONKEY_LLM_PROXY_CLIENT_SECRET" in out


def test_init_is_idempotent_and_does_not_clobber(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: object
) -> None:
    from pathlib import Path

    existing = Path(str(tmp_path)) / ".donkey-kit.toml"
    existing.write_text('[donkey]\nclient_id = "hand-edited"\n')

    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "from-env")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, _combined(result)
    # The hand-edited file is preserved untouched.
    assert existing.read_text() == '[donkey]\nclient_id = "hand-edited"\n'
    assert "exists" in _combined(result).lower()


def test_init_force_regenerates(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: object
) -> None:
    from pathlib import Path

    existing = Path(str(tmp_path)) / ".donkey-kit.toml"
    existing.write_text('[donkey]\nclient_id = "hand-edited"\n')

    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "from-env")

    result = runner.invoke(app, ["init", "--force"])
    assert result.exit_code == 0, _combined(result)
    table = tomllib.loads(existing.read_text())["donkey"]
    assert table["client_id"] == "from-env"


def test_init_writes_to_custom_config_path(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, tmp_path: object
) -> None:
    from pathlib import Path

    target = Path(str(tmp_path)) / "nested" / "custom.toml"
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cp-client-id")

    result = runner.invoke(app, ["--config", str(target), "init"])
    assert result.exit_code == 0, _combined(result)
    assert target.is_file()
    assert tomllib.loads(target.read_text())["donkey"]["client_id"] == "cp-client-id"


def test_init_json_output_is_machine_readable(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    import json

    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cp-client-id")

    result = runner.invoke(app, ["--json", "init"])
    assert result.exit_code == 0, _combined(result)
    payload = json.loads(result.stdout)
    assert payload["path"].endswith(".donkey-kit.toml")
    assert "missing" in payload  # the all-at-once list, machine-readable
