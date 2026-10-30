"""The ``donkey publish --dry-run`` plan (#218, BG §2.5): one asset per MCP
server and agent, coordinates from ``[donkey.publication]``, a per-asset digest
that ignores source locations, and every declaration problem as an issue.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from donkey_kit.registry.publish_plan import PLAN_FORMAT, plan_publication
from donkey_kit.registry.scan import scan

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""


@donkey.agent(name="support-triage")
def handle(task):
    """Triage a support ticket and draft a reply for a human to send."""
'''

TABLE = '[donkey.publication]\ngroup_id = "acme"\nversion = "1.3.0"\n'


def _repo(tmp_path: Path, toml: str | None = TABLE, source: str = SOURCE) -> Path:
    (tmp_path / "app.py").write_text(source, encoding="utf-8")
    if toml is not None:
        (tmp_path / ".donkey-kit.toml").write_text(toml, encoding="utf-8")
    return tmp_path


def _plan(root: Path, default_group_id: str | None = None):  # type: ignore[no-untyped-def]
    return plan_publication(scan(root), root, default_group_id=default_group_id)


def test_one_asset_per_mcp_server_and_agent(tmp_path: Path) -> None:
    plan = _plan(_repo(tmp_path))

    assert plan.issues == ()
    assert [(a.type, a.group_id, a.asset_id, a.version) for a in plan.assets] == [
        ("mcp", "acme", "sku-manager", "1.3.0"),
        ("agent", "acme", "support-triage", "1.3.0"),
    ]
    mcp, agent = plan.assets
    assert mcp.tools == ("lookup_sku",)
    assert mcp.description is None
    assert agent.description.startswith("Triage a support ticket")  # type: ignore[union-attr]
    assert all(a.digest.startswith("sha256:") for a in plan.assets)


def test_per_asset_overrides(tmp_path: Path) -> None:
    toml = TABLE + (
        '\n[donkey.publication.assets.sku-manager]\nasset_id = "sku-mcp"\n'
        'name = "SKU Manager"\ndescription = "Prices and stock."\nversion = "2.0.0-rc.1"\n'
    )

    mcp = _plan(_repo(tmp_path, toml)).assets[0]

    assert (mcp.asset_id, mcp.name, mcp.description, mcp.version) == (
        "sku-mcp",
        "SKU Manager",
        "Prices and stock.",
        "2.0.0-rc.1",
    )


def test_the_local_file_overrides_key_by_key(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".donkey-kit.local.toml").write_text(
        '[donkey.publication]\nversion = "1.4.0"\n', encoding="utf-8"
    )

    assets = _plan(root).assets

    assert {(a.group_id, a.version) for a in assets} == {("acme", "1.4.0")}


def test_group_id_defaults_to_the_org_id(tmp_path: Path) -> None:
    plan = _plan(_repo(tmp_path, '[donkey.publication]\nversion = "1.0.0"\n'), "org-123")

    assert plan.issues == ()
    assert {a.group_id for a in plan.assets} == {"org-123"}


def test_no_declaration_reports_missing_group_and_version(tmp_path: Path) -> None:
    plan = _plan(_repo(tmp_path, None))

    assert [i.kind for i in plan.issues] == ["missing-group-id", "missing-version"]
    assert {(a.group_id, a.version) for a in plan.assets} == {(None, None)}


@pytest.mark.parametrize("version", ["1.3", "v1.3.0", "01.2.3", 1])
def test_a_non_semver_version_is_an_issue(tmp_path: Path, version: object) -> None:
    toml = f'[donkey.publication]\ngroup_id = "acme"\nversion = {json.dumps(version)}\n'

    plan = _plan(_repo(tmp_path, toml))

    assert [i.kind for i in plan.issues] == ["invalid-version"]
    assert plan.issues[0].path == ".donkey-kit.toml"


def test_unknown_keys_and_services_are_issues(tmp_path: Path) -> None:
    toml = TABLE + (
        'groupid = "typo"\n\n[donkey.publication.assets.sku-manager]\nassetid = "x"\n'
        '\n[donkey.publication.assets.nobody]\nname = "Nobody"\n'
    )

    issues = _plan(_repo(tmp_path, toml)).issues

    assert sorted((i.kind, i.subject) for i in issues) == [
        ("invalid-publication", "donkey.publication"),
        ("invalid-publication", "publication asset sku-manager"),
        ("unknown-service", "publication asset nobody"),
    ]


def test_scan_issues_carry_into_the_plan(tmp_path: Path) -> None:
    source = SOURCE + '\n\n@donkey.tool(server="sku-manager")\ndef ping():\n    """ping"""\n'

    plan = _plan(_repo(tmp_path, source=source))

    assert [i.kind for i in plan.issues] == ["tautological"]


def test_digest_ignores_location_and_version_but_not_content(tmp_path: Path) -> None:
    before = _plan(_repo(tmp_path)).assets
    moved = _plan(_repo(tmp_path, TABLE.replace("1.3.0", "9.9.9"), "\n\n# moved\n" + SOURCE)).assets
    changed = _plan(
        _repo(tmp_path, source=SOURCE.replace("price and stock", "stock and price"))
    ).assets

    assert [a.digest for a in moved] == [a.digest for a in before]
    assert changed[0].digest != before[0].digest
    assert changed[1].digest == before[1].digest


def test_json_is_canonical(tmp_path: Path) -> None:
    plan = _plan(_repo(tmp_path))

    data = json.loads(plan.to_json())

    assert plan.to_json() == json.dumps(data, sort_keys=True, indent=2) + "\n"
    assert data["plan_format"] == PLAN_FORMAT
    assert [a["asset_id"] for a in data["assets"]] == ["sku-manager", "support-triage"]


DECLARED = (
    TABLE + 'protocol_version = "2025-06-18"\n'
    'provider = { organization = "Acme", url = "https://acme.example.invalid" }\n\n'
    "[donkey.publication.assets.sku-manager]\n"
    'protocol_version = "2025-11-25"\ndocumentation = "docs/sku.md"\n'
)


def test_protocol_version_provider_and_documentation(tmp_path: Path) -> None:
    root = _repo(tmp_path, DECLARED)
    (root / "docs").mkdir()
    (root / "docs" / "sku.md").write_text("Ask it about stock.\n", encoding="utf-8")

    plan = _plan(root)

    assert plan.issues == ()
    mcp, agent = plan.assets
    assert mcp.protocol_version == "2025-11-25"
    assert mcp.provider is not None and mcp.provider.organization == "Acme"
    # Both describe an MCP server's mcp-metadata, so the table's never reach an agent.
    assert (agent.protocol_version, agent.provider) == (None, None)
    assert (mcp.protocol, agent.protocol) == (None, "other")
    assert (mcp.documentation, mcp.documentation_text) == ("docs/sku.md", "Ask it about stock.\n")
    assert agent.documentation is None
    data = plan.to_dict()["assets"][0]
    assert data["provider"] == {"organization": "Acme", "url": "https://acme.example.invalid"}
    assert data["documentation"] == "docs/sku.md"
    assert "documentation_text" not in data


def test_undeclared_metadata_keeps_the_digest_and_declared_metadata_changes_it(
    tmp_path: Path,
) -> None:
    before = _plan(_repo(tmp_path)).assets[0]
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "sku.md").write_text("v1\n", encoding="utf-8")
    declared = _plan(_repo(tmp_path, DECLARED)).assets[0]
    (tmp_path / "docs" / "sku.md").write_text("v2\n", encoding="utf-8")
    edited = _plan(_repo(tmp_path, DECLARED)).assets[0]

    assert before.protocol_version is None and before.provider is None
    assert len({before.digest, declared.digest, edited.digest}) == 3


@pytest.mark.parametrize(
    ("toml", "detail"),
    [
        (TABLE + 'protocol_version = "2099-01-01"\n', "protocol_version must be one of"),
        (TABLE + 'provider = "Acme"\n', "provider must be a table"),
        (TABLE + 'provider = { organization = "Acme" }\n', "provider must be a table"),
        (
            TABLE + 'provider = { organization = "Acme", url = "ftp://acme.invalid" }\n',
            "provider must be a table",
        ),
        (
            TABLE + '[donkey.publication.assets.sku-manager]\ndocumentation = "../outside.md"\n',
            "must be a path inside the scanned root",
        ),
        (
            TABLE + '[donkey.publication.assets.sku-manager]\ndocumentation = "missing.md"\n',
            "'missing.md' cannot be read",
        ),
    ],
)
def test_malformed_metadata_is_an_issue(tmp_path: Path, toml: str, detail: str) -> None:
    (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()

    plan = _plan(_repo(root, toml))

    (issue,) = plan.issues
    assert issue.kind == "invalid-publication"
    assert detail in issue.detail


@pytest.mark.parametrize(
    "key",
    [
        'protocol_version = "2025-06-18"',
        'provider = { organization = "Acme", url = "https://acme.example.invalid" }',
    ],
)
def test_mcp_metadata_on_an_agent_is_an_issue(tmp_path: Path, key: str) -> None:
    plan = _plan(_repo(tmp_path, TABLE + f"[donkey.publication.assets.support-triage]\n{key}\n"))

    (issue,) = plan.issues
    assert issue.kind == "invalid-publication"
    assert "describes an MCP server" in issue.detail


def test_an_agent_plans_its_declared_protocol_and_endpoint(tmp_path: Path) -> None:
    toml = TABLE + (
        "[donkey.agents.support-triage]\n"
        'protocol = "a2a_v1"\nendpoint = "https://agents.example.invalid/sku/"\n'
    )
    before = _plan(_repo(tmp_path)).assets[1]

    agent = _plan(_repo(tmp_path, toml)).assets[1]

    assert (agent.protocol, agent.endpoint) == ("a2a_v1", "https://agents.example.invalid/sku/")
    assert agent.digest != before.digest
