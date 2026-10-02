"""One table names the env var for every config field (#720).

``from_env()``, ``missing_fields()`` and the error messages all read
``config._ENV_VARS``, so a field's env var can't be spelled two ways.
"""

from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path

import pytest

from donkey_kit.core import config as config_module
from donkey_kit.core.config import DonkeyConfig

# ``cost`` is set per dimension (DONKEY_COST_TEAM, ...), not by one env var.
_NOT_ENV = {"cost"}


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def test_the_table_covers_every_field() -> None:
    public = {f.name for f in fields(DonkeyConfig) if not f.name.startswith("_")}
    assert set(config_module._ENV_VARS) == public - _NOT_ENV
    assert len(set(config_module._ENV_VARS.values())) == len(config_module._ENV_VARS)


@pytest.mark.usefixtures("clean_env")
def test_from_env_reads_every_string_field_from_its_table_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    free_text = {
        key
        for key in config_module._ENV_VARS
        if key not in config_module._CHECKED_KEYS and not key.endswith("_header")
    }
    for key in free_text:
        monkeypatch.setenv(config_module._ENV_VARS[key], f"value-of-{key}")
    cfg = DonkeyConfig.from_env()
    for key in free_text:
        assert getattr(cfg, key) == f"value-of-{key}"
        assert cfg.source_of(key).kind == "env"


def test_missing_fields_name_the_table_env_var() -> None:
    missing = DonkeyConfig().missing_fields(need="control_plane")
    assert missing == [
        f"{key} (env {config_module._ENV_VARS[key]})"
        for key in ("client_id", "client_secret", "org_id")
    ]
