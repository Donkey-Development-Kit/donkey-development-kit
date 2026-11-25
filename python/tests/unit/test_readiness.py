"""Offline readiness per workflow (#1749): scan validity, publication readiness
and per-service exposure readiness, reported separately by ``donkey scaffold``.

Every fixture is written to ``tmp_path`` and never imported.
"""

from __future__ import annotations

import json
import socket
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from donkey_kit.cli import app
from donkey_kit.registry.readiness import (
    BLOCKED,
    INCOMPLETE,
    INVALID,
    NOT_EXPOSABLE,
    READY,
    Missing,
    WorkflowReadiness,
)
from donkey_kit.registry.scaffold import UnknownService, build_scaffold

runner = CliRunner()
TOML = ".donkey-kit.toml"

SOURCE = """\
    import readiness_side_effect_marker  # must never be imported
    raise SystemExit("readiness must never import this module")

    from donkey_kit import Donkey

    donkey = Donkey.from_env()


    @donkey.tool(server="sku-manager")
    def lookup_sku(sku: str) -> dict:
        \"\"\"Look up a SKU's price and stock level by its code.\"\"\"


    @donkey.agent(name="support-triage", skills=["triage"])
    async def handle(task):
        \"\"\"Triage a support ticket and draft a reply for a human to send.\"\"\"
"""

# Everything both workflows need, for both services.
COMPLETE = """\
    [donkey.agents.support-triage]
    protocol = "a2a_v1"
    endpoint = "https://triage.example.test/"

    [donkey.mcp_servers.sku-manager]
    transport = "streamable-http"
    endpoint = "https://sku.example.test/mcp"

    [donkey.publication]
    group_id = "acme"
    version = "1.0.0"

    [donkey.expose]
    target = "shared-omni-gateway"
"""


def _repo(root: Path, project: str | None = None, *, local: str | None = None) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "main.py").write_text(textwrap.dedent(SOURCE), encoding="utf-8")
    if project is not None:
        (root / TOML).write_text(textwrap.dedent(project), encoding="utf-8")
    if local is not None:
        (root / ".donkey-kit.local.toml").write_text(textwrap.dedent(local), encoding="utf-8")
    return root


def _keys(entry: WorkflowReadiness, *, required: bool = True) -> list[tuple[str, str]]:
    return [(m.table, m.key) for m in entry.missing if m.required is required]


def test_a_fresh_project_names_every_missing_value_by_table_and_key(tmp_path: Path) -> None:
    readiness = build_scaffold(_repo(tmp_path)).readiness

    assert readiness.scan_valid
    publication = readiness.publication
    assert publication is not None
    assert publication.status == INCOMPLETE
    assert _keys(publication) == [("donkey.publication", "version")]
    # Published regardless, so reported as notes, not as blockers.
    assert _keys(publication, required=False) == [
        ("donkey.agents.support-triage", "protocol"),
        ("donkey.mcp_servers.sku-manager", "transport"),
        ("donkey.publication", "group_id"),
    ]
    group = next(m for m in publication.missing if m.key == "group_id")
    assert "org_id" in group.detail and "not checked" in group.detail

    by_service = {e.service: e for e in readiness.exposure}
    assert set(by_service) == {"sku-manager", "support-triage"}
    assert by_service["sku-manager"].status == INCOMPLETE
    assert _keys(by_service["sku-manager"]) == [
        ("donkey.expose", "target"),
        ("donkey.expose.services.sku-manager", "upstream"),
    ]
    # An agent with no declared protocol is not "not exposable": it is missing one.
    assert by_service["support-triage"].status == INCOMPLETE
    assert ("donkey.agents.support-triage", "protocol") in _keys(by_service["support-triage"])


def test_complete_declarations_are_ready_and_say_live_is_not_checked(tmp_path: Path) -> None:
    result = build_scaffold(_repo(tmp_path, COMPLETE))
    readiness = result.readiness

    assert result.issues == ()
    assert readiness.publication is not None
    assert readiness.publication.status == READY
    assert readiness.publication.missing == ()
    assert [(e.service, e.status) for e in readiness.exposure] == [
        ("sku-manager", READY),
        ("support-triage", READY),
    ]
    data = result.to_dict()["readiness"]
    assert data["live"] == "not-checked"
    assert data["scan"] == "valid"


def test_mixed_services_have_independent_states(tmp_path: Path) -> None:
    """An agent published as protocol other is not exposable, which neither fails
    publication nor the MCP server's exposure."""
    project = COMPLETE.replace('protocol = "a2a_v1"', 'protocol = "other"')
    readiness = build_scaffold(_repo(tmp_path, project)).readiness

    assert readiness.publication is not None
    assert readiness.publication.status == READY
    triage = next(e for e in readiness.exposure if e.service == "support-triage")
    assert triage.status == NOT_EXPOSABLE
    assert triage.missing == ()
    assert [p.kind for p in triage.problems] == ["not-exposable"]
    assert next(e for e in readiness.exposure if e.service == "sku-manager").status == READY


def test_an_uncommented_placeholder_counts_as_missing(tmp_path: Path) -> None:
    project = COMPLETE.replace('group_id = "acme"', 'group_id = "<org id>"').replace(
        'version = "1.0.0"', 'version = "<a semantic version, such as 1.0.0>"'
    )
    project = project.replace(
        'target = "shared-omni-gateway"', 'target = "<the name of the gateway to deploy to>"'
    )
    readiness = build_scaffold(_repo(tmp_path, project)).readiness

    publication = readiness.publication
    assert publication is not None
    # A placeholder is incomplete, not invalid: its invalid-version issue is folded in.
    assert publication.status == INCOMPLETE
    assert publication.problems == ()
    assert _keys(publication) == [
        ("donkey.publication", "group_id"),
        ("donkey.publication", "version"),
    ]
    assert all("placeholder" in m.detail for m in publication.missing)
    sku = next(e for e in readiness.exposure if e.service == "sku-manager")
    assert sku.status == INCOMPLETE
    assert _keys(sku) == [("donkey.expose", "target")]


def test_a_wrong_declaration_is_invalid_with_its_problem(tmp_path: Path) -> None:
    project = COMPLETE.replace('version = "1.0.0"', 'version = "1.0.0"\nflavour = "x"')
    readiness = build_scaffold(_repo(tmp_path, project)).readiness

    publication = readiness.publication
    assert publication is not None
    assert publication.status == INVALID
    assert [p.kind for p in publication.problems] == ["invalid-publication"]
    assert "flavour" in publication.problems[0].detail


def test_a_scan_issue_blocks_both_workflows(tmp_path: Path) -> None:
    project = COMPLETE + '\n[donkey.mcp_servers.ghost]\ntransport = "stdio"\n'
    result = build_scaffold(_repo(tmp_path, project))
    readiness = result.readiness

    assert [i.kind for i in result.issues] == ["unknown-server"]
    assert not readiness.scan_valid
    assert readiness.publication is not None
    assert readiness.publication.status == BLOCKED
    assert {e.status for e in readiness.exposure} == {BLOCKED}
    assert result.to_dict()["readiness"]["scan"] == "invalid"


def test_a_rejected_value_is_reported_as_invalid_not_undeclared(tmp_path: Path) -> None:
    # #1802: a declared but rejected value is left None in the model; readiness
    # must not call it "not declared" or tell the developer to set it.
    project = (
        COMPLETE.replace('protocol = "a2a_v1"', 'protocol = "a2a"')
        .replace('transport = "streamable-http"', 'transport = "carrier-pigeon"')
        .replace("https://sku.example.test/mcp", "https://sku.example.test/mcp?api_key=SENTINEL")
        .replace("https://triage.example.test/", "https://u:SENTINEL@triage.example.test/")
    )
    result = build_scaffold(_repo(tmp_path, project))
    readiness = result.readiness

    assert {i.kind for i in result.issues} == {
        "invalid-protocol",
        "invalid-transport",
        "invalid-endpoint",
    }
    publication = readiness.publication
    assert publication is not None
    assert publication.status == BLOCKED
    assert _keys(publication, required=False) == []

    by_service = {e.service: e for e in readiness.exposure}
    triage = by_service["support-triage"]
    assert triage.status == BLOCKED
    details = {(m.table, m.key): m.detail for m in triage.missing}
    assert details[("donkey.agents.support-triage", "protocol")].startswith(
        "invalid (see the invalid-protocol issue)"
    )
    assert details[("donkey.expose.services.support-triage", "upstream")].endswith(
        "fix the invalid endpoint in [donkey.agents.support-triage]"
    )
    rendered = json.dumps(result.to_dict())
    assert "not declared" not in rendered
    assert "set endpoint" not in rendered
    assert "SENTINEL" not in rendered


def test_an_mcp_servers_rejected_endpoint_says_fix_it(tmp_path: Path) -> None:
    project = COMPLETE.replace(
        "https://sku.example.test/mcp", "https://sku.example.test/mcp?api_key=SENTINEL"
    )
    readiness = build_scaffold(_repo(tmp_path, project)).readiness

    sku = next(e for e in readiness.exposure if e.service == "sku-manager")
    upstream = next(m for m in sku.missing if m.key == "upstream")
    assert upstream.detail == (
        "the URL the gateway reaches it on, or fix the invalid endpoint in "
        "[donkey.mcp_servers.sku-manager]"
    )


def test_malformed_project_file_is_an_invalid_scan(tmp_path: Path) -> None:
    readiness = build_scaffold(_repo(tmp_path, "[donkey\n")).readiness
    assert not readiness.scan_valid
    assert readiness.publication is not None
    assert readiness.publication.status == BLOCKED


def test_no_service_means_nothing_to_publish(tmp_path: Path) -> None:
    (tmp_path / "empty.py").write_text("x = 1\n", encoding="utf-8")
    readiness = build_scaffold(tmp_path).readiness
    assert readiness.scan_valid
    assert readiness.publication is None
    assert readiness.exposure == ()


def test_the_local_overlay_is_never_read(tmp_path: Path) -> None:
    """Readiness is the committed file's, as the scaffold is: a version in the
    overlay neither completes publication nor appears in the output."""
    sentinel = "-".join(["9", "9", "9"]).replace("-", ".")
    project = COMPLETE.replace('version = "1.0.0"\n', "")
    root = _repo(tmp_path, project, local=f'[donkey.publication]\nversion = "{sentinel}"\n')
    result = build_scaffold(root)

    assert result.readiness.publication is not None
    assert _keys(result.readiness.publication) == [("donkey.publication", "version")]
    assert sentinel not in result.to_json()


def test_readiness_is_the_same_after_the_tables_are_written(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = build_scaffold(root, expose=True)
    assert runner.invoke(app, ["scaffold", str(root), "--expose"]).exit_code == 0
    after = build_scaffold(root, expose=True)
    assert after.candidates == ()
    assert after.readiness == before.readiness


def test_service_selection_limits_tables_and_exposure(tmp_path: Path) -> None:
    result = build_scaffold(_repo(tmp_path), expose=True, services=["sku-manager"])

    assert [c.name for c in result.candidates] == [
        "donkey.mcp_servers.sku-manager",
        "donkey.publication",
        "donkey.publication.assets.sku-manager",
        "donkey.expose",
        "donkey.expose.services.sku-manager",
    ]
    assert [e.service for e in result.readiness.exposure] == ["sku-manager"]
    # Publication stays project-wide: one publish registers every service.
    assert result.readiness.publication is not None
    assert ("donkey.agents.support-triage", "protocol") in _keys(
        result.readiness.publication, required=False
    )


def test_an_unknown_service_is_bad_input(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(UnknownService, match="'nope'.*found sku-manager, support-triage"):
        build_scaffold(root, services=["nope"])
    res = runner.invoke(app, ["scaffold", str(root), "--service", "nope"])
    assert res.exit_code == 2
    assert "found sku-manager, support-triage" in res.stderr
    assert not (root / TOML).exists()


def test_readiness_stays_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("readiness must stay offline and config-free")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr("donkey_kit.core.config.DonkeyConfig.from_env", refuse)
    monkeypatch.setenv("ANYPOINT_ORG_ID", "from-the-environment")
    result = build_scaffold(_repo(tmp_path, COMPLETE.replace('group_id = "acme"\n', "")))
    assert result.readiness.publication is not None
    # The org_id fallback is reported, not resolved from the environment.
    assert result.readiness.publication.status == READY
    assert _keys(result.readiness.publication, required=False) == [
        ("donkey.publication", "group_id")
    ]
    assert "from-the-environment" not in result.to_json()


def test_cli_text_reports_each_workflow_and_never_claims_live(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    res = runner.invoke(app, ["scaffold", str(root), "--dry-run"])

    assert res.exit_code == 0  # unresolved requirements report, never fail
    err = res.stderr
    assert "readiness, offline from the committed file" in err
    assert "live credentials and reachability not checked" in err
    assert "  scan: valid" in err
    assert "  publish: incomplete" in err
    assert "missing  [donkey.publication] version" in err
    assert "note  [donkey.publication] group_id" in err
    assert "  expose sku-manager: incomplete; publish is not ready" in err
    assert "missing  [donkey.expose.services.sku-manager] upstream" in err
    assert "verified" not in err.lower()
    # stdout is still the TOML alone.
    assert "readiness" not in res.stdout


def test_cli_json_carries_the_readiness(tmp_path: Path) -> None:
    root = _repo(tmp_path, COMPLETE.replace('protocol = "a2a_v1"', 'protocol = "other"'))
    res = runner.invoke(app, ["scaffold", str(root), "--dry-run", "--json"])

    assert res.exit_code == 0
    readiness = json.loads(res.stdout)["readiness"]
    assert readiness["scan"] == "valid"
    assert readiness["live"] == "not-checked"
    assert readiness["publication"] == {"status": READY, "missing": [], "problems": []}
    assert [(e["service"], e["status"]) for e in readiness["exposure"]] == [
        ("sku-manager", READY),
        ("support-triage", NOT_EXPOSABLE),
    ]


def test_missing_serializes_table_key_and_requirement() -> None:
    assert Missing("donkey.publication", "version", "a semver").to_dict() == {
        "table": "donkey.publication",
        "key": "version",
        "detail": "a semver",
        "required": True,
    }
