"""``donkey verify`` CLI wiring (#1352): it plans the repository's assets as
``donkey publish`` does and runs ``Publication.verify()`` on each MCP server.

``Publication.verify()`` itself is tested against the Exchange captures in
``test_publication_verify.py``; here the ``Donkey`` is a stand-in whose
published and live tools are set per test.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from donkey_kit.cli import app
from donkey_kit.cli import verify as verify_command
from donkey_kit.core.errors import RegistryError
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet

runner = CliRunner()
ORG = "00000000-0000-4000-8000-306f0858eed3"
REF = f"{ORG}/sku-manager/1.0.0"

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""
'''

AGENT = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.agent(name="triage")
async def triage(question: str) -> str:
    """Triage a support question."""
'''

_TOOL = {
    "name": "lookup_sku",
    "description": "Look up a SKU's price and stock level by its code.",
    "inputSchema": {"type": "object", "properties": {"sku": {"type": "string"}}},
}
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])


@dataclass
class _Donkey:
    """``registry.published_tools`` and ``tools._resolve``, recording the refs read."""

    published: list[dict[str, Any]] = field(default_factory=lambda: [_TOOL])
    refused: bool = False
    read: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.registry = SimpleNamespace(published_tools=self._published_tools)
        self.tools = SimpleNamespace(_resolve=self._resolve)

    async def _published_tools(self, ref: str) -> list[dict[str, Any]]:
        self.read.append(ref)
        if self.refused:
            raise RegistryError(f"Exchange has no asset version {ref}.")
        return self.published

    async def _resolve(self, ref: str) -> ToolSet:
        handle = McpServerHandle(
            ref=AssetRef.parse(ref),
            endpoint_url="https://gw.example.test/sku/",
            governance=_GOVERNED,
        )
        return ToolSet([handle], _auth=ClientIdAuth("cid", "secret"))

    async def __aenter__(self) -> _Donkey:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("ANYPOINT_ORG_ID", ORG)
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "client")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "secret")
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / ".donkey-kit.toml").write_text(
        '[donkey.publication]\nversion = "1.0.0"\n', encoding="utf-8"
    )
    return tmp_path


def _serve(monkeypatch: pytest.MonkeyPatch, live: list[dict[str, Any]], **kw: Any) -> _Donkey:
    donkey = _Donkey(**kw)
    monkeypatch.setattr(verify_command, "_donkey", lambda cfg: donkey)

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        return tuple(live)

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    return donkey


def test_verify_is_a_visible_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert "verify" in result.output


def test_a_matching_server_exits_zero(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    donkey = _serve(monkeypatch, [_TOOL])

    result = runner.invoke(app, ["verify", str(repo)])

    assert result.exit_code == 0, result.output
    assert donkey.read == [REF]
    assert f"match   mcp    {ORG}:sku-manager:1.0.0" in result.output
    assert "verified 1 MCP server: all match" in result.output


def test_an_added_live_tool_is_drift_and_exits_one(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, [_TOOL, {**_TOOL, "name": "delete_sku"}])

    result = runner.invoke(app, ["verify", str(repo)])

    assert result.exit_code == 1, result.output
    assert "drift   mcp" in result.output
    assert "tool delete_sku is live but was not published" in result.output
    assert "verified 1 MCP server: 1 did not" in result.output


def test_json_carries_each_outcome(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, [{**_TOOL, "description": "Something else."}])

    result = runner.invoke(app, ["--json", "verify", str(repo)])

    assert result.exit_code == 1, result.output
    (asset,) = json.loads(result.output)["assets"]
    assert asset["status"] == "drift"
    assert asset["differences"] == ["tool lookup_sku: the description changed"]


def test_a_failed_read_is_reported_and_exits_one(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, [_TOOL], refused=True)

    result = runner.invoke(app, ["--json", "verify", str(repo)])

    assert result.exit_code == 1, result.output
    (asset,) = json.loads(result.output)["assets"]
    assert asset["status"] == "error"
    assert "no asset version" in asset["detail"]


def test_an_agent_is_skipped_not_failed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / "agent.py").write_text(AGENT, encoding="utf-8")
    donkey = _serve(monkeypatch, [_TOOL])

    result = runner.invoke(app, ["--json", "verify", str(repo)])

    assert result.exit_code == 0, result.output
    statuses = {a["type"]: a["status"] for a in json.loads(result.output)["assets"]}
    assert statuses == {"mcp": "match", "agent": "skipped"}
    assert donkey.read == [REF]


def test_a_declaration_issue_reads_nothing(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo / ".donkey-kit.toml").write_text("[donkey.publication]\n", encoding="utf-8")
    donkey = _serve(monkeypatch, [_TOOL])

    result = runner.invoke(app, ["verify", str(repo)])

    assert result.exit_code == 1, result.output
    assert "nothing was verified" in result.output
    assert donkey.read == []


def test_missing_control_plane_credentials_exit_two(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANYPOINT_CLIENT_ID")
    monkeypatch.delenv("ANYPOINT_CLIENT_SECRET")
    donkey = _serve(monkeypatch, [_TOOL])

    result = runner.invoke(app, ["verify", str(repo)])

    assert result.exit_code == 2, result.output
    assert donkey.read == []


def test_nothing_at_the_path_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(app, ["verify", str(tmp_path / "missing")])

    assert result.exit_code == 2
