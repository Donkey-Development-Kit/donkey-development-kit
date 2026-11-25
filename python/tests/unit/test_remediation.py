"""Remediation for a missing declaration, safe config reads and credential-free
messages, and ``donkey scaffold --set`` (#1750, milestone 28).

A missing-declaration message names the exact table and key and, when the
table is absent, the ``donkey scaffold`` command that adds it. Other failures
get no scaffold advice. A config file that cannot be read is a ConfigError
(exit 2, no traceback), and no value that may hold a credential is echoed.
"""

from __future__ import annotations

import json
import shlex
import textwrap
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from donkey_kit.cli import app
from donkey_kit.registry.expose_plan import plan_exposure
from donkey_kit.registry.publish_plan import plan_publication
from donkey_kit.registry.remedy import scaffold_command, set_argument, table_name
from donkey_kit.registry.scaffold import InvalidSet, build_scaffold
from donkey_kit.registry.scan import scan

runner = CliRunner()
TOML = ".donkey-kit.toml"
# Built at run time so no secret scanner mistakes it for a real credential.
SENTINEL = "".join(["zq", "sentinel", "7731"])

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""


@donkey.agent(name="support-triage", skills=["triage"])
async def handle(task):
    """Triage a support ticket and draft a reply for a human to send."""
'''


def _repo(root: Path, toml: str | None = None) -> Path:
    (root / "app.py").write_text(SOURCE, encoding="utf-8")
    if toml is not None:
        (root / TOML).write_text(textwrap.dedent(toml), encoding="utf-8")
    return root


def _details(issues: object) -> dict[str, str]:
    return {i.kind: i.detail for i in issues}  # type: ignore[attr-defined]


def _out(result: Result) -> str:
    return result.stdout + result.stderr


# --- the remedy wording ---------------------------------------------------


def test_scaffold_command_is_executable_and_quotes_the_root() -> None:
    assert scaffold_command() == "donkey scaffold"
    assert scaffold_command(".") == "donkey scaffold"
    command = scaffold_command(
        "my repo", expose=True, sets=[set_argument(("donkey", "expose"), "target", "GATEWAY")]
    )
    assert command == "donkey scaffold --expose --set expose.target=GATEWAY 'my repo'"
    assert shlex.split(command)[-1] == "my repo"
    assert table_name("donkey", "agents", "a.b") == 'donkey.agents."a.b"'


def test_publish_names_the_key_and_one_command_for_an_absent_table(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    details = _details(plan_publication(scan(root), root).issues)

    command = (
        "donkey scaffold --set publication.version=1.0.0 --set publication.group_id=GROUP_ID "
        f"{shlex.quote(str(root))}"
    )
    assert "set version in [donkey.publication]" in details["missing-version"]
    assert "set group_id in [donkey.publication]" in details["missing-group-id"]
    # Both keys in one command: a second run leaves an existing table alone.
    assert f"`{command}` adds the table with it" in details["missing-version"]
    assert f"`{command}` adds the table with it" in details["missing-group-id"]


def test_publish_gives_no_scaffold_advice_once_the_table_exists(tmp_path: Path) -> None:
    root = _repo(tmp_path, "[donkey.publication]\n")

    details = _details(plan_publication(scan(root), root).issues)

    assert "set version in [donkey.publication]" in details["missing-version"]
    assert "scaffold" not in details["missing-version"]
    assert "scaffold" not in details["missing-group-id"]


def test_publish_omits_the_set_for_a_key_already_satisfied(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    plan = plan_publication(scan(root), root, default_group_id="org-1")

    (issue,) = plan.issues
    assert issue.kind == "missing-version"
    assert "--set publication.version=1.0.0 " in issue.detail
    assert "group_id" not in issue.detail.split("`")[1]


def test_expose_combines_every_missing_expose_table_in_one_command(tmp_path: Path) -> None:
    root = _repo(tmp_path, '[donkey.publication]\nversion = "1.0.0"\n')

    plan = plan_exposure(
        plan_publication(scan(root), root, default_group_id="org-1"), root, service="sku-manager"
    )

    details = _details(plan.issues)
    command = (
        "donkey scaffold --expose --set expose.target=GATEWAY "
        f"--set expose.services.sku-manager.upstream=URL {shlex.quote(str(root))}"
    )
    assert f"set target in [donkey.expose] (`{command}`" in details["missing-target"]
    assert "set upstream in [donkey.expose.services.sku-manager]" in details["missing-upstream"]
    assert "endpoint in [donkey.mcp_servers.sku-manager]" in details["missing-upstream"]
    assert f"`{command}` adds it" in details["missing-upstream"]


def test_expose_gives_no_scaffold_advice_for_declared_tables(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        """\
        [donkey.publication]
        version = "1.0.0"
        [donkey.expose]
        [donkey.expose.services.sku-manager]
        label = "bad label"
        """,
    )

    plan = plan_exposure(
        plan_publication(scan(root), root, default_group_id="org-1"), root, service="sku-manager"
    )

    details = _details(plan.issues)
    assert set(details) == {"missing-target", "missing-upstream", "invalid-expose"}
    assert "set label in [donkey.expose.services.sku-manager]" in details["invalid-expose"]
    assert not any("scaffold" in d for d in details.values())


def test_a_non_a2a_agent_is_told_which_table_and_keys_make_it_exposable(tmp_path: Path) -> None:
    root = _repo(tmp_path, '[donkey.publication]\nversion = "1.0.0"\n')

    plan = plan_exposure(
        plan_publication(scan(root), root, default_group_id="org-1"),
        root,
        service="support-triage",
        target="gw",
    )

    details = _details(plan.issues)
    assert (
        'set protocol = "a2a_v1" and its endpoint in [donkey.agents.support-triage]'
        in (details["not-exposable"])
    )
    # The agent is not exposable yet, so scaffolding its expose table would not help.
    assert "scaffold" not in details["missing-upstream"]


def test_non_declaration_failures_get_no_scaffold_advice(tmp_path: Path) -> None:
    root = _repo(tmp_path, '[donkey.publication]\nversion = "one"\n')

    details = _details(plan_publication(scan(root), root, default_group_id="org-1").issues)

    assert details and not any("scaffold" in d for d in details.values())


def test_scan_names_the_table_and_key_a_server_lacks(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = runner.invoke(app, ["scan", str(root)])

    assert result.exit_code == 0, _out(result)
    assert (
        "transport undeclared (set transport in [donkey.mcp_servers.sku-manager]; "
        f"`donkey scaffold {shlex.quote(str(root))}` adds the table if {TOML} lacks it)"
    ) in result.stdout


def test_scan_names_the_endpoint_an_http_server_lacks(tmp_path: Path) -> None:
    root = _repo(tmp_path, '[donkey.mcp_servers.sku-manager]\ntransport = "sse"\n')

    result = runner.invoke(app, ["scan", str(root)])

    assert "sse endpoint undeclared (set endpoint in [donkey.mcp_servers.sku-manager])" in (
        result.stdout
    )


def test_an_a2a_agent_without_an_endpoint_is_told_where_to_set_it(tmp_path: Path) -> None:
    root = _repo(tmp_path, '[donkey.agents.support-triage]\nprotocol = "a2a_v1"\n')

    (issue,) = scan(root).issues

    assert "set endpoint in [donkey.agents.support-triage]" in issue.detail
    assert "scaffold" not in issue.detail


# --- unreadable config: ConfigError, exit 2, no bytes shown ----------------


_UNREADABLE = [
    pytest.param(b'[donkey]\norg_id = "' + SENTINEL.encode() + b'\xff\xfe"\n', id="not-utf8"),
    pytest.param(b"[donkey\n" + SENTINEL.encode() + b"\n", id="not-toml"),
]


@pytest.mark.parametrize("content", _UNREADABLE)
@pytest.mark.parametrize(
    "command",
    [
        ["publish", "--dry-run", "--json"],
        ["verify", "--json"],
        ["preflight", "--json"],
    ],
)
def test_an_unreadable_config_exits_2_without_a_traceback(
    tmp_path: Path, content: bytes, command: list[str]
) -> None:
    root = _repo(tmp_path)
    (root / TOML).write_bytes(content)

    result = runner.invoke(app, [*command, str(root)])

    assert result.exit_code == 2, _out(result)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in _out(result)
    assert f"Malformed {root / TOML}" in result.stderr
    assert SENTINEL not in _out(result)
    assert result.stdout == ""


# --- credentials in URLs are refused and never echoed ----------------------


_CREDENTIAL_URLS = [
    pytest.param(f"https://svc:{SENTINEL}@sku.example.com/mcp", id="userinfo"),
    pytest.param(f"https://sku.example.com/mcp?api_key={SENTINEL}", id="query"),
    pytest.param(f"https://sku.example.com/mcp#token={SENTINEL}", id="fragment"),
    pytest.param(f"https://sku.example.com:{SENTINEL}/mcp", id="malformed-port"),
]


@pytest.mark.parametrize("url", _CREDENTIAL_URLS)
def test_an_endpoint_credential_is_never_shown(tmp_path: Path, url: str) -> None:
    root = _repo(
        tmp_path,
        f"""\
        [donkey.mcp_servers.sku-manager]
        transport = "streamable-http"
        endpoint = "{url}"
        """,
    )

    outputs = [
        runner.invoke(app, ["scan", "--json", str(root)]),
        runner.invoke(app, ["scan", str(root)]),
        runner.invoke(app, ["publish", "--dry-run", "--json", str(root)]),
        runner.invoke(app, ["scaffold", "--dry-run", "--json", str(root)]),
    ]

    assert all(r.exit_code == 1 for r in outputs), [_out(r) for r in outputs]
    assert '"invalid-endpoint"' in outputs[0].stdout
    for result in outputs:
        assert SENTINEL not in _out(result)


@pytest.mark.parametrize("url", _CREDENTIAL_URLS)
def test_a_provider_url_credential_is_never_shown(tmp_path: Path, url: str) -> None:
    root = _repo(
        tmp_path,
        f"""\
        [donkey.publication]
        version = "1.0.0"
        provider = {{ organization = "Acme", url = "{url}" }}
        """,
    )

    plan = plan_publication(scan(root), root, default_group_id="org-1")
    result = runner.invoke(app, ["publish", "--dry-run", "--json", str(root)])

    (issue,) = [i for i in plan.issues if "provider" in i.detail]
    assert "remove it, since the url is published" in issue.detail or "parse" in issue.detail
    assert SENTINEL not in issue.detail
    assert result.exit_code == 1
    assert SENTINEL not in _out(result)


# --- donkey scaffold --set --------------------------------------------------


def _scaffold(root: Path, *args: str) -> Result:
    return runner.invoke(app, ["scaffold", *args, str(root)])


def test_set_fills_placeholders_as_active_keys(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = _scaffold(
        root,
        "--expose",
        "--set",
        "publication.version=1.0.0",
        "--set",
        "donkey.agents.support-triage.endpoint=https://triage.example.com/",
        "--set",
        "agents.support-triage.protocol=a2a_v1",
        "--set",
        "mcp_servers.sku-manager.transport=stdio",
        "--set",
        "expose.target=shared-gateway",
    )

    assert result.exit_code == 0, _out(result)
    text = (root / TOML).read_text(encoding="utf-8")
    # Placeholder order is kept, whatever order the --set flags came in.
    assert '[donkey.agents.support-triage]\nprotocol = "a2a_v1"\nendpoint = "https' in text
    assert '[donkey.publication]\nversion = "1.0.0"\n# group_id = ' in text
    assert '[donkey.expose]\ntarget = "shared-gateway"\n' in text
    # Setting the protocol to a2a_v1 makes the agent exposable, so it gets a table.
    assert "[donkey.expose.services.support-triage]" in text
    assert "# endpoint = " in text  # the stdio server's endpoint stays a placeholder
    assert "readiness count" not in result.stderr
    # Readiness is assessed again after the write: the version now counts.
    assert "  publish: ready" in result.stderr
    assert scan(root).issues == ()


def test_set_keeps_the_json_shape_and_reports_the_filled_values(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    plain = json.loads(_scaffold(root, "--dry-run", "--json").stdout)

    result = _scaffold(root, "--dry-run", "--json", "--set", "publication.version=2.1.0")

    assert result.exit_code == 0, _out(result)
    document = json.loads(result.stdout)
    assert set(document) == set(plain)
    (publication,) = [c for c in document["candidates"] if c["kind"] == "publication"]
    assert publication["values"] == {"version": "2.1.0"}
    assert [p["key"] for p in publication["unresolved"]] == ["group_id"]
    assert document["unresolved"] == plain["unresolved"] - 1
    assert not (root / TOML).exists()


def test_a_dry_run_says_the_set_values_count_once_written(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    result = _scaffold(root, "--dry-run", "--set", "publication.version=1.0.0")

    assert result.exit_code == 0, _out(result)
    assert 'version = "1.0.0"' in result.stdout
    assert "the --set values count in readiness once they are written" in result.stderr


@pytest.mark.parametrize(
    ("toml", "args", "message"),
    [
        (None, ["--set", "publication.version"], "--set takes TABLE.KEY=VALUE"),
        (None, ["--set", f"{SENTINEL}"], "--set takes TABLE.KEY=VALUE"),
        (None, ["--set", f"publication={SENTINEL}"], "--set takes TABLE.KEY=VALUE"),
        (None, ["--set", f"publication..version={SENTINEL}"], "--set takes TABLE.KEY=VALUE"),
        (
            None,
            ["--set", f"agents.support-triage.api_key={SENTINEL}"],
            "api_key looks like a secret",
        ),
        (
            None,
            ["--set", "publication.version=1.0.0", "--set", f"publication.version={SENTINEL}"],
            "gives version in [donkey.publication] twice",
        ),
        (
            '[donkey.publication]\nversion = "1.0.0"\n',
            ["--set", f"publication.group_id={SENTINEL}"],
            f"[donkey.publication] is already in {TOML}: edit it there",
        ),
        (None, ["--set", f"expose.target={SENTINEL}"], "is an expose table: pass --expose"),
        (
            None,
            ["--set", f"agents.nobody.protocol={SENTINEL}"],
            "scaffold does not propose [donkey.agents.nobody]",
        ),
        (
            None,
            ["--set", f"publication.assets.sku-manager.asset_id={SENTINEL}"],
            "scaffold fills every key of [donkey.publication.assets.sku-manager] itself",
        ),
        (
            None,
            ["--set", f"publication.label={SENTINEL}"],
            "label is not a key scaffold leaves open in [donkey.publication]; "
            "those are: version, group_id",
        ),
        (
            None,
            ["--set", f"publication.version=v{SENTINEL}"],
            "--set publication.version: version must be a semantic version",
        ),
        (
            None,
            ["--set", "publication.group_id= "],
            "--set publication.group_id: group_id must not be empty",
        ),
        (
            None,
            ["--set", f"mcp_servers.sku-manager.transport={SENTINEL}"],
            "transport must be one of stdio, streamable-http, sse",
        ),
        (
            None,
            ["--set", f"agents.support-triage.protocol={SENTINEL}"],
            "protocol must be one of a2a_v1, other",
        ),
        (
            None,
            ["--set", f"agents.support-triage.endpoint=ftp://{SENTINEL}.example.com/"],
            "--set agents.support-triage.endpoint: endpoint must be an http or https URL",
        ),
        (
            None,
            ["--set", f"agents.support-triage.endpoint=https://u:{SENTINEL}@x.example.com/"],
            "endpoint carries a user name or password",
        ),
        (
            None,
            [
                "--expose",
                "--set",
                f"expose.services.sku-manager.upstream=https://x.example.com/?token={SENTINEL}",
            ],
            "upstream has a query parameter 'token' that looks like a credential",
        ),
        (
            None,
            [
                "--set",
                "mcp_servers.sku-manager.transport=stdio",
                "--set",
                f"mcp_servers.sku-manager.endpoint=https://{SENTINEL}.example.com/",
            ],
            "a stdio server is started as a process and has no endpoint",
        ),
        (
            None,
            ["--set", "agents.support-triage.protocol=a2a_v1"],
            "an a2a_v1 agent needs an endpoint: add --set agents.support-triage.endpoint=URL",
        ),
    ],
)
def test_a_refused_set_exits_2_writes_nothing_and_never_echoes_the_value(
    tmp_path: Path, toml: str | None, args: list[str], message: str
) -> None:
    root = _repo(tmp_path, toml)
    before = (root / TOML).read_bytes() if toml is not None else None

    for extra in ([], ["--dry-run"], ["--json"]):
        result = _scaffold(root, *extra, *args)

        assert result.exit_code == 2, _out(result)
        assert message in result.stderr
        assert result.stdout == ""
        assert SENTINEL not in _out(result)
        assert ((root / TOML).read_bytes() if toml is not None else None) == before
        assert toml is not None or not (root / TOML).exists()


def test_build_scaffold_raises_invalid_set(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    with pytest.raises(InvalidSet, match="semantic version"):
        build_scaffold(root, sets=["publication.version=latest"])
