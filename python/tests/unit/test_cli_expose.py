"""``donkey expose`` and ``donkey preflight`` CLI wiring (#226, #1279) against
the fake control plane (``_fake_anypoint``): the step lines, the exit codes,
and that preflight and ``--dry-run`` send no write call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _fake_anypoint import GATEWAY, FakeAnypoint
from _fake_exchange import ORG
from _json_shapes import EXPOSE_KEYS, EXPOSE_STEP_KEYS, assert_shape
from typer.testing import CliRunner

from donkey_kit.cli import app
from donkey_kit.cli import expose as expose_command
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.runtime import Runtime
from donkey_kit.registry import expose as expose_module

runner = CliRunner()
MULESOFT = "68ef9520-24e9-4cf2-b2f5-620025690913"
URL = "https://shared-omni-gateway.example.invalid/sku-manager/"

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""
'''

CONFIG = f"""\
[donkey.publication]
version = "1.0.0"

[donkey.mcp_servers.sku-manager]
transport = "streamable-http"
endpoint = "https://sku.example.invalid/mcp"

[donkey.expose]
target = "{GATEWAY}"

[[donkey.expose.services.sku-manager.policies]]
asset = "{MULESOFT}/mcp-support/1.1.1"
configuration = {{ injectMcpNameHeaders = false }}
"""


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ("ANYPOINT_ORG_ID", "ANYPOINT_ENV", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / ".donkey-kit.toml").write_text(CONFIG, encoding="utf-8")
    return tmp_path


@pytest.fixture
def anypoint(repo: Path, monkeypatch: pytest.MonkeyPatch) -> FakeAnypoint:
    """The fake control plane behind the command's client, with the
    control-plane credentials set."""
    monkeypatch.setenv("ANYPOINT_ORG_ID", ORG)
    monkeypatch.setenv("ANYPOINT_CLIENT_ID", "cid")
    monkeypatch.setenv("ANYPOINT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(expose_module, "_POLL_S", 0.0)
    fake = FakeAnypoint()

    def runtime(cfg: DonkeyConfig) -> Runtime:
        rt = Runtime(cfg, auth=StaticToken("tok"))
        rt.control_http.governed_transport.replace_inner(fake.transport())
        return rt

    monkeypatch.setattr(expose_command, "_runtime", runtime)
    return fake


def test_expose_then_rerun(repo: Path, anypoint: FakeAnypoint) -> None:
    first = runner.invoke(app, ["expose", "--path", str(repo)])
    writes = anypoint.writes()
    again = runner.invoke(app, ["expose", "--path", str(repo)])

    assert first.exit_code == 0, first.output
    assert "✓ instance            created" in first.output
    assert "✓ policy mcp-support  created  inbound mcp-support 1.1.1" in first.output
    assert f"MCP server sku-manager is exposed at {URL}" in first.output
    assert again.exit_code == 0, again.output
    assert "created" not in again.output
    assert "✓ deployment          present  applied on shared-omni-gateway" in again.output
    assert anypoint.writes() == writes


@pytest.mark.parametrize("command", [["preflight"], ["expose", "--dry-run"]])
def test_preflight_and_dry_run_write_nothing(
    repo: Path, anypoint: FakeAnypoint, command: list[str]
) -> None:
    result = runner.invoke(app, [*command, "sku-manager", "--path", str(repo)])

    assert result.exit_code == 0, result.output
    assert "· instance            planned  would create sku-manager on shared-omni-gateway" in (
        result.output
    )
    assert "would expose MCP server sku-manager (dry run: nothing was changed)" in result.output
    assert anypoint.writes() == []


def test_preflight_names_a_missing_gateway_and_where_to_create_it(
    repo: Path, anypoint: FakeAnypoint
) -> None:
    result = runner.invoke(app, ["preflight", "--path", str(repo), "--target", "absent-gateway"])

    assert result.exit_code == 1
    assert "✗ gateway" in result.output
    assert "no gateway named 'absent-gateway'" in result.output
    assert "Runtime Manager > Flex Gateways" in result.output
    assert "sku-manager is not exposed" in result.stderr
    assert anypoint.writes() == []


def test_preflight_names_a_missing_environment(repo: Path, anypoint: FakeAnypoint) -> None:
    result = runner.invoke(app, ["preflight", "--path", str(repo), "--env", "Nope"])

    assert result.exit_code == 1
    assert "no environment 'Nope'" in result.output
    assert "Access Management > Environments" in result.output
    assert anypoint.writes() == []


def test_a_missing_scope_is_named(repo: Path, anypoint: FakeAnypoint) -> None:
    import httpx

    anypoint.refuse["deploy"] = httpx.Response(403, json={"message": "Forbidden"})

    result = runner.invoke(app, ["expose", "--path", str(repo)])

    assert result.exit_code == 1
    assert "✗ deployment" in result.output
    assert "'manage:api_proxies'" in result.output


def test_json_report(repo: Path, anypoint: FakeAnypoint) -> None:
    result = runner.invoke(app, ["--json", "preflight", "--path", str(repo)])

    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert_shape(report, EXPOSE_KEYS, "steps", EXPOSE_STEP_KEYS)
    assert report["issues"] == []
    assert report["ok"] is True
    assert report["dry_run"] is True
    assert report["url"] == URL
    assert [s["name"] for s in report["steps"]] == [
        "environment",
        "gateway",
        "registry",
        "instance",
        "policy mcp-support",
        "deployment",
    ]


def test_declaration_issues_exit_1_and_send_nothing(repo: Path, anypoint: FakeAnypoint) -> None:
    (repo / ".donkey-kit.toml").write_text(CONFIG.replace(f'target = "{GATEWAY}"\n', ""))

    result = runner.invoke(app, ["expose", "--path", str(repo)])

    assert result.exit_code == 1
    assert "missing-target  donkey.expose" in result.stderr
    assert "nothing was sent" in result.stderr
    assert anypoint.requests == []


@pytest.mark.parametrize(
    ("command", "dry_run"),
    [(["preflight"], True), (["expose"], False), (["expose", "--dry-run"], True)],
)
def test_json_with_declaration_issues_prints_the_report(
    repo: Path, anypoint: FakeAnypoint, command: list[str], dry_run: bool
) -> None:
    """#1427: a document on stdout, as well as the issues on stderr."""
    (repo / ".donkey-kit.toml").write_text(CONFIG.replace(f'target = "{GATEWAY}"\n', ""))

    result = runner.invoke(app, ["--json", *command, "--path", str(repo)])

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert_shape(report, EXPOSE_KEYS, "steps", EXPOSE_STEP_KEYS)
    assert (report["ok"], report["dry_run"], report["url"], report["steps"]) == (
        False,
        dry_run,
        None,
        [],
    )
    assert [i["kind"] for i in report["issues"]] == ["missing-target"]
    assert "missing-target  donkey.expose" in result.stderr
    assert anypoint.requests == []


def test_no_control_plane_credentials_exit_2(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANYPOINT_CLIENT_ID", "ANYPOINT_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANYPOINT_ORG_ID", ORG)

    result = runner.invoke(app, ["preflight", "--path", str(repo)])

    assert result.exit_code == 2


def test_nothing_at_the_path_exits_2(repo: Path) -> None:
    result = runner.invoke(app, ["preflight", "--path", str(repo / "absent")])

    assert result.exit_code == 2
    assert "nothing to scan" in result.output


def test_both_commands_are_in_the_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "expose" in result.output
    assert "preflight" in result.output
