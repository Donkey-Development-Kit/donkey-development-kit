"""``donkey_kit.registry.expose_plan``: which service ``donkey expose`` picks and
how ``[donkey.expose]`` is read, offline (#226, ADR 0015)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from donkey_kit.registry.expose_plan import ExposePlan, PlannedPolicy, plan_exposure
from donkey_kit.registry.models import AssetRef
from donkey_kit.registry.publish_plan import plan_publication
from donkey_kit.registry.scan import scan

ORG = "00000000-0000-4000-8000-306f0858eed3"
MULESOFT = "68ef9520-24e9-4cf2-b2f5-620025690913"

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str) -> dict:
    """Look up a SKU's price and stock level by its code."""


@donkey.agent(name="pricing-agent", skills=["quote"])
def pricing() -> None:
    """Answers pricing questions for the store."""
'''

SERVICES = """
[donkey.mcp_servers.sku-manager]
transport = "streamable-http"
endpoint = "https://sku.example.invalid/mcp"

[donkey.agents.pricing-agent]
protocol = "a2a_v1"
endpoint = "https://agents.example.invalid/pricing/"
"""

Plan = Callable[..., ExposePlan]


@pytest.fixture
def plan(tmp_path: Path) -> Plan:
    def write(
        expose: str = '[donkey.expose]\ntarget = "gw"\n',
        *,
        service: str | None = "sku-manager",
        target: str | None = None,
        services: str = SERVICES,
        local: str | None = None,
        source: str = SOURCE,
    ) -> ExposePlan:
        (tmp_path / "app.py").write_text(source, encoding="utf-8")
        (tmp_path / ".donkey-kit.toml").write_text(
            f'[donkey.publication]\nversion = "1.0.0"\n{services}\n{expose}', encoding="utf-8"
        )
        if local is not None:
            (tmp_path / ".donkey-kit.local.toml").write_text(local, encoding="utf-8")
        result = scan(tmp_path)
        publication = plan_publication(result, tmp_path, default_group_id=ORG)
        return plan_exposure(publication, tmp_path, service=service, target=target)

    return write


def _kinds(plan: ExposePlan) -> list[tuple[str, str]]:
    return [(i.kind, i.subject) for i in plan.issues]


def test_an_mcp_server_defaults_its_label_and_upstream(plan: Plan) -> None:
    planned = plan()

    assert planned.issues == ()
    assert planned.asset is not None and planned.asset.asset_id == "sku-manager"
    assert planned.endpoint_type == "mcp"
    assert planned.label == "sku-manager"
    assert planned.upstream == "https://sku.example.invalid/mcp"
    assert planned.target == "gw"
    assert planned.policies == ()


def test_an_a2a_agent_is_exposed_as_a2a(plan: Plan) -> None:
    planned = plan(service="pricing-agent")

    assert planned.issues == ()
    assert planned.endpoint_type == "a2a"
    assert planned.upstream == "https://agents.example.invalid/pricing/"


def test_the_declaration_sets_label_upstream_and_policies(plan: Plan) -> None:
    planned = plan(
        '[donkey.expose]\ntarget = "gw"\n\n'
        "[donkey.expose.services.pricing-agent]\n"
        'label = "pricing"\nupstream = "https://pricing.internal.example.invalid/"\n\n'
        "[[donkey.expose.services.pricing-agent.policies]]\n"
        f'asset = "{MULESOFT}/credential-injection-api-key/1.0.1"\n'
        'configuration = { authType = "apiKey" }\noutbound = true\n\n'
        "[[donkey.expose.services.pricing-agent.policies]]\n"
        f'asset = "{MULESOFT}/client-id-enforcement/1.3.3"\n',
        service="pricing-agent",
    )

    assert planned.issues == ()
    assert planned.label == "pricing"
    assert planned.upstream == "https://pricing.internal.example.invalid/"
    assert planned.policies == (
        PlannedPolicy(
            AssetRef(MULESOFT, "credential-injection-api-key", "1.0.1"),
            {"authType": "apiKey"},
            outbound=True,
        ),
        PlannedPolicy(AssetRef(MULESOFT, "client-id-enforcement", "1.3.3")),
    )


def test_the_only_service_needs_no_name(plan: Plan) -> None:
    only_mcp = SOURCE.split("\n\n\n@donkey.agent")[0] + "\n"

    planned = plan(service=None, services=SERVICES.split("[donkey.agents")[0], source=only_mcp)

    assert planned.issues == ()
    assert planned.asset is not None and planned.asset.service_id == "sku-manager"


def test_two_services_need_a_name(plan: Plan) -> None:
    planned = plan(service=None)

    assert _kinds(planned) == [("missing-service", "donkey expose")]
    assert "pricing-agent" in planned.issues[0].detail
    assert "sku-manager" in planned.issues[0].detail
    assert planned.asset is None


def test_an_unknown_service_is_an_issue(plan: Plan) -> None:
    planned = plan(service="nope")

    assert _kinds(planned) == [("unknown-service", "nope")]


def test_a_table_for_no_service_is_an_issue(plan: Plan) -> None:
    planned = plan('[donkey.expose]\ntarget = "gw"\n[donkey.expose.services.ghost]\nlabel = "g"\n')

    assert _kinds(planned) == [("unknown-service", "expose service ghost")]


def test_no_target_is_an_issue_and_the_flag_overrides_the_file(plan: Plan) -> None:
    assert _kinds(plan("")) == [("missing-target", "donkey.expose")]
    assert plan("", target="cli-gw").target == "cli-gw"
    assert plan(target="cli-gw").target == "cli-gw"


def test_the_local_file_overrides_the_project_file(plan: Plan) -> None:
    planned = plan(
        local='[donkey.expose]\ntarget = "local-gw"\n'
        '[donkey.expose.services.sku-manager]\nlabel = "sku-local"\n'
    )

    assert planned.issues == ()
    assert planned.target == "local-gw"
    assert planned.label == "sku-local"


def test_an_agent_that_is_not_a2a_is_not_exposable(plan: Plan) -> None:
    planned = plan(
        service="pricing-agent",
        services=SERVICES.replace('protocol = "a2a_v1"\n', ""),
    )

    assert ("not-exposable", "expose service pricing-agent") in _kinds(planned)


def test_a_service_without_an_endpoint_needs_an_upstream(plan: Plan) -> None:
    planned = plan(services=SERVICES.replace('endpoint = "https://sku.example.invalid/mcp"\n', ""))

    assert ("missing-upstream", "expose service sku-manager") in _kinds(planned)


def test_an_outbound_policy_on_an_mcp_server_is_an_issue(plan: Plan) -> None:
    planned = plan(
        '[donkey.expose]\ntarget = "gw"\n'
        "[[donkey.expose.services.sku-manager.policies]]\n"
        f'asset = "{MULESOFT}/credential-injection-api-key/1.0.1"\noutbound = true\n'
    )

    (issue,) = planned.issues
    assert issue.kind == "invalid-expose"
    assert "remove outbound = true" in issue.detail


@pytest.mark.parametrize(
    ("table", "detail"),
    [
        ('[donkey.expose]\ntarget = "gw"\nport = 8081\n', "unknown key 'port'"),
        ("[donkey.expose]\ntarget = 3\n", "target must be a non-empty string"),
        ('[donkey.expose]\ntarget = "gw"\nservices = 1\n', "must be a table"),
        (
            '[donkey.expose]\ntarget = "gw"\n[donkey.expose.services.sku-manager]\nlabel = "a/b"\n',
            "must be letters, digits",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[donkey.expose.services.sku-manager]\nlabel = ""\n',
            "label must be a non-empty string",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[donkey.expose.services.sku-manager]\nprotocol = 1\n',
            "unknown key 'protocol'",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[donkey.expose.services.sku-manager]\n'
            'upstream = "ftp://sku.example.invalid/"\n',
            "upstream",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[donkey.expose.services.sku-manager]\npolicies = 1\n',
            "policies must be an array of tables",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[[donkey.expose.services.sku-manager.policies]]\n'
            'asset = "mcp-support"\n',
            "'<group id>/<asset id>/<version>'",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[[donkey.expose.services.sku-manager.policies]]\n'
            f'asset = "{MULESOFT}/mcp-support/1.1.1"\nconfiguration = 1\n',
            "configuration must be a table",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[[donkey.expose.services.sku-manager.policies]]\n'
            f'asset = "{MULESOFT}/mcp-support/1.1.1"\noutbound = "no"\n',
            "outbound must be true or false",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[[donkey.expose.services.sku-manager.policies]]\n'
            f'asset = "{MULESOFT}/mcp-support/1.1.1"\norder = 1\n',
            "unknown key 'order'",
        ),
        (
            '[donkey.expose]\ntarget = "gw"\n[[donkey.expose.services.sku-manager.policies]]\n'
            f'asset = "{MULESOFT}/mcp-support/1.1.1"\n'
            "[[donkey.expose.services.sku-manager.policies]]\n"
            f'asset = "{MULESOFT}/mcp-support/1.1.2"\n',
            "mcp-support is declared twice",
        ),
    ],
)
def test_an_invalid_declaration_is_an_issue(plan: Plan, table: str, detail: str) -> None:
    planned = plan(table)

    assert any(i.kind == "invalid-expose" and detail in i.detail for i in planned.issues), (
        planned.issues
    )
    assert all(i.path == ".donkey-kit.toml" for i in planned.issues)


def test_publication_issues_are_carried_over(plan: Plan, tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
    (tmp_path / ".donkey-kit.toml").write_text(
        f'[donkey.publication]\n{SERVICES}\n[donkey.expose]\ntarget = "gw"\n', encoding="utf-8"
    )
    result = scan(tmp_path)

    planned = plan_exposure(
        plan_publication(result, tmp_path, default_group_id=ORG), tmp_path, service="sku-manager"
    )

    assert ("missing-version", "donkey.publication") in _kinds(planned)


def test_the_plan_reads_beside_a_scanned_file(plan: Plan, tmp_path: Path) -> None:
    plan()
    result = scan(tmp_path / "app.py")

    planned = plan_exposure(
        plan_publication(result, tmp_path / "app.py", default_group_id=ORG),
        tmp_path / "app.py",
        service="sku-manager",
    )

    assert planned.issues == ()
    assert planned.target == "gw"
