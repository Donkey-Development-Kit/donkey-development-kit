"""``donkey publish`` CLI wiring (#218): ``--dry-run`` prints the planned
Exchange assets offline and exits 1 on an issue; without it the command stays
blocked on verification (exit 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from donkey_kit.cli import app

runner = CliRunner()

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""
'''


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANYPOINT_ORG_ID", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / ".donkey-kit.toml").write_text(
        '[donkey.publication]\nversion = "1.0.0"\n', encoding="utf-8"
    )
    return tmp_path


def test_dry_run_prints_the_plan(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org-123")

    result = runner.invoke(app, ["publish", "--dry-run", str(repo)])

    assert result.exit_code == 0, result.output
    assert "mcp    org-123:sku-manager:1.0.0  sku-manager  sha256:" in result.output
    assert "would publish 1 asset (dry run: nothing was sent)" in result.output


def test_dry_run_json(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org-123")

    result = runner.invoke(app, ["--json", "publish", "--dry-run", str(repo)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["assets"][0]["group_id"] == "org-123"


def test_the_org_id_in_the_scanned_roots_config_is_the_default_group(
    repo: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    (repo / ".donkey-kit.toml").write_text(
        '[donkey]\norg_id = "from-file"\n\n[donkey.publication]\nversion = "1.0.0"\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["--json", "publish", "--dry-run", str(repo)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["assets"][0]["group_id"] == "from-file"


def test_dry_run_issues_exit_1(repo: Path) -> None:
    result = runner.invoke(app, ["publish", "--dry-run", str(repo)])

    assert result.exit_code == 1
    assert "<group_id>:sku-manager:1.0.0" in result.output
    assert "missing-group-id  donkey.publication" in result.output


def test_dry_run_on_a_missing_path_exits_2(repo: Path) -> None:
    result = runner.invoke(app, ["publish", "--dry-run", str(repo / "absent")])

    assert result.exit_code == 2
    assert "nothing to scan" in result.output


@pytest.mark.parametrize("flags", [[], ["--if-changed"], ["--always"]])
def test_publish_without_dry_run_stays_blocked(repo: Path, flags: list[str]) -> None:
    result = runner.invoke(app, ["publish", *flags, str(repo)])

    assert result.exit_code == 3
    assert "blocked on verification:" in result.output
