"""``donkey publish`` CLI wiring (#218): ``--dry-run`` prints the planned
Exchange assets offline and exits 1 on an issue; without it the command
publishes to a fake Exchange (``_fake_exchange``) that replays sandbox captures.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _fake_exchange import ORG, FakeExchange
from typer.testing import CliRunner

from donkey_kit.cli import app
from donkey_kit.cli import publish as publish_command
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.runtime import Runtime

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


@pytest.fixture
def exchange(repo: Path, monkeypatch: pytest.MonkeyPatch) -> FakeExchange:
    """A fake Exchange behind the command's control-plane client, with the
    control-plane credentials set."""
    monkeypatch.setenv("ANYPOINT_ORG_ID", ORG)
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cid")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "secret")
    fake = FakeExchange()

    def runtime(cfg: DonkeyConfig) -> Runtime:
        rt = Runtime(cfg, auth=StaticToken("tok"))
        rt.control_http.governed_transport.replace_inner(fake.transport())
        return rt

    monkeypatch.setattr(publish_command, "_runtime", runtime)
    return fake


def test_publish_then_rerun_publishes_once(repo: Path, exchange: FakeExchange) -> None:
    first = runner.invoke(app, ["publish", str(repo)])
    again = runner.invoke(app, ["publish", str(repo)])

    assert first.exit_code == 0, first.output
    assert f"published mcp    {ORG}:sku-manager:1.0.0  published 1.0.0" in first.output
    assert "published 1 asset" in first.output
    assert again.exit_code == 0, again.output
    assert "unchanged mcp" in again.output
    assert "published 0 assets" in again.output
    assert list(exchange.assets[(ORG, "sku-manager")]) == ["1.0.0"]


def test_publish_json_reports_each_assets_outcome(repo: Path, exchange: FakeExchange) -> None:
    runner.invoke(app, ["publish", str(repo)])

    result = runner.invoke(app, ["--json", "publish", str(repo)])

    assert result.exit_code == 0, result.output
    (asset,) = json.loads(result.stdout)["assets"]
    assert (asset["asset_id"], asset["status"], asset["detail"]) == (
        "sku-manager",
        "unchanged",
        "1.0.0 is already published",
    )


def test_new_content_under_a_published_version_exits_1(repo: Path, exchange: FakeExchange) -> None:
    runner.invoke(app, ["publish", str(repo)])
    (repo / "app.py").write_text(
        SOURCE.replace("price and stock level", "price, stock level and bin"), encoding="utf-8"
    )

    result = runner.invoke(app, ["publish", str(repo)])

    assert result.exit_code == 1
    assert "sku-manager:1.0.0 is already published with other content" in result.output
    assert "Bump the version" in result.output


def test_a_plan_with_issues_publishes_nothing(repo: Path, exchange: FakeExchange) -> None:
    (repo / ".donkey-kit.toml").write_text("[donkey.publication]\n", encoding="utf-8")

    result = runner.invoke(app, ["publish", str(repo)])

    assert result.exit_code == 1
    assert "missing-version" in result.output
    assert "nothing was published" in result.output
    assert exchange.requests == []


def test_publish_json_with_issues_prints_the_plan(repo: Path, exchange: FakeExchange) -> None:
    (repo / ".donkey-kit.toml").write_text("[donkey.publication]\n", encoding="utf-8")

    result = runner.invoke(app, ["publish", "--json", str(repo)])

    assert result.exit_code == 1
    assert [i["kind"] for i in json.loads(result.stdout)["issues"]] == ["missing-version"]
    assert exchange.requests == []


def test_publish_without_control_plane_credentials_exits_2(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANYPOINT_ORG_ID", "org-123")
    monkeypatch.delenv("ANYPOINT_CLIENT_ID", raising=False)
    monkeypatch.delenv("ANYPOINT_CLIENT_SECRET", raising=False)

    result = runner.invoke(app, ["publish", str(repo)])

    assert result.exit_code == 2
    assert "client_id" in result.output


def test_the_page_source_comes_from_github_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert publish_command._ci_source() is None

    for name, value in {
        "GITHUB_ACTIONS": "true",
        "GITHUB_SERVER_URL": "https://github.example.invalid",
        "GITHUB_REPOSITORY": "acme/sku",
        "GITHUB_SHA": "a" * 40,
        "GITHUB_REF_NAME": "main",
        "GITHUB_RUN_ID": "42",
    }.items():
        monkeypatch.setenv(name, value)

    source = publish_command._ci_source()

    assert source is not None
    assert source.repository_url == "https://github.example.invalid/acme/sku"
    assert (source.commit, source.ref) == ("a" * 40, "main")
    assert source.run_url == "https://github.example.invalid/acme/sku/actions/runs/42"
