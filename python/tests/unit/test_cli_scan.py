"""``donkey scan`` CLI wiring (#217): a summary or canonical JSON, exit 1 when
scan reports an issue so CI fails before publish, and exit 2 on a missing path.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from donkey_kit.cli import app

runner = CliRunner()

CLEAN = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""


@donkey.agent(name="support-triage")
def handle(task):
    """Triage a support ticket and draft a reply for a human to send."""
'''


def _repo(tmp_path: Path, source: str = CLEAN) -> Path:
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    return tmp_path


def test_summary_and_exit_0_when_clean(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scan", str(_repo(tmp_path))])

    assert result.exit_code == 0, result.output
    assert "agent  support-triage  (app.py:12)" in result.output
    assert "mcp    sku-manager  (1 tool: lookup_sku)" in result.output
    assert "found 1 agent, 1 tool, 1 MCP server" in result.output


def test_json_is_the_canonical_model(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scan", str(_repo(tmp_path)), "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [s["id"] for s in data["mcp_servers"]] == ["sku-manager"]
    assert data["issues"] == []


def test_the_global_json_flag_is_honoured(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--json", "scan", str(_repo(tmp_path))])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["agents"][0]["name"] == "support-triage"


def test_issues_exit_1_and_name_the_unattributed(tmp_path: Path) -> None:
    source = CLEAN + '\n\n@donkey.tool\ndef issue_refund():\n    """issue_refund"""\n'

    result = runner.invoke(app, ["scan", str(_repo(tmp_path, source))])

    assert result.exit_code == 1
    assert "unattributed  issue_refund" in result.output
    assert "tautological  tool issue_refund" in result.output


def test_a_missing_path_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scan", str(tmp_path / "absent")])

    assert result.exit_code == 2
    assert "nothing to scan" in result.output


def test_summary_shows_how_each_server_is_reached(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = runner.invoke(app, ["scan", str(root)])
    assert "transport undeclared (set [donkey.mcp_servers.sku-manager] in .donkey-kit.toml)" in (
        result.output
    )

    (root / ".donkey-kit.toml").write_text(
        '[donkey.mcp_servers.sku-manager]\ntransport = "streamable-http"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["scan", str(root)])
    assert result.exit_code == 0, result.output
    assert "streamable-http endpoint undeclared" in result.output

    (root / ".donkey-kit.toml").write_text(
        '[donkey.mcp_servers.sku-manager]\ntransport = "streamable-http"\n'
        'endpoint = "https://sku.acme.com/mcp"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["scan", str(root)])
    assert "(1 tool: lookup_sku)  streamable-http https://sku.acme.com/mcp" in result.output


def test_an_invalid_declaration_fails_the_run(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".donkey-kit.toml").write_text(
        '[donkey.mcp_servers.sku-manager]\ntransport = "websocket"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["scan", str(root)])

    assert result.exit_code == 1
    assert "invalid-transport" in result.output
