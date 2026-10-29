"""``donkey_kit.registry.expose`` against a fake control plane (``_fake_anypoint``)
that replays the sandbox captures (#226, ADR 0015): what each step sends, that
a re-run and a dry run write nothing, and that a step that did not happen is
never reported as done.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from _fake_anypoint import APIM, ENV, GATEWAY, GATEWAY_ID, FakeAnypoint, body
from _fake_exchange import BASE_URL, ORG

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.runtime import Runtime
from donkey_kit.registry import expose as expose_module
from donkey_kit.registry.expose import ExposeReport, expose
from donkey_kit.registry.expose_plan import ExposePlan, plan_exposure
from donkey_kit.registry.publish_plan import plan_publication
from donkey_kit.registry.scan import ScanIssue, ScanResult, scan

MULESOFT = "68ef9520-24e9-4cf2-b2f5-620025690913"
MCP, AGENT = "sku-manager", "pricing-agent"
MCP_UPSTREAM = "https://sku.example.invalid/mcp"
AGENT_UPSTREAM = "https://agents.example.invalid/pricing/"
PUBLIC = "https://shared-omni-gateway.example.invalid"

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

EXPOSE = f"""
[donkey.expose]
target = "{GATEWAY}"

[[donkey.expose.services.{MCP}.policies]]
asset = "{MULESOFT}/mcp-support/1.1.1"
configuration = {{ injectMcpNameHeaders = false }}

[[donkey.expose.services.{AGENT}.policies]]
asset = "{MULESOFT}/credential-injection-api-key/1.0.1"
configuration = {{ authType = "apiKey", overwrite = true, customHeader = "X-DDK-Access-Key" }}
outbound = true
"""

Build = Callable[..., tuple[ExposePlan, ScanResult]]


@pytest.fixture
def build(tmp_path: Path) -> Build:
    def write(service: str = MCP, expose_toml: str = EXPOSE) -> tuple[ExposePlan, ScanResult]:
        (tmp_path / "app.py").write_text(SOURCE, encoding="utf-8")
        (tmp_path / ".donkey-kit.toml").write_text(
            '[donkey.publication]\nversion = "1.0.0"\n\n'
            f'[donkey.mcp_servers.{MCP}]\ntransport = "streamable-http"\n'
            f'endpoint = "{MCP_UPSTREAM}"\n\n'
            f'[donkey.agents.{AGENT}]\nprotocol = "a2a_v1"\nendpoint = "{AGENT_UPSTREAM}"\n'
            + expose_toml,
            encoding="utf-8",
        )
        result = scan(tmp_path)
        publication = plan_publication(result, tmp_path, default_group_id=ORG)
        plan = plan_exposure(publication, tmp_path, service=service)
        assert not plan.issues, plan.issues
        return plan, result

    return write


@pytest.fixture
def anypoint() -> FakeAnypoint:
    return FakeAnypoint()


@pytest.fixture
async def http(anypoint: FakeAnypoint) -> AsyncIterator[httpx.AsyncClient]:
    runtime = Runtime(_cfg(), auth=StaticToken("tok"))
    runtime.control_http.governed_transport.replace_inner(anypoint.transport())
    yield runtime.control_http
    await runtime.aclose()


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(expose_module, "_POLL_S", 0.0)


def _cfg(**overrides: Any) -> DonkeyConfig:
    return DonkeyConfig(org_id=ORG, base_url=BASE_URL, **overrides)


async def _run(
    built: tuple[ExposePlan, ScanResult],
    http: httpx.AsyncClient,
    *,
    dry_run: bool = False,
    cfg: DonkeyConfig | None = None,
) -> ExposeReport:
    plan, result = built
    return await expose(plan, result, http, cfg=cfg or _cfg(), dry_run=dry_run)


def _statuses(report: ExposeReport) -> dict[str, str]:
    return {s.name: s.status for s in report.steps}


def _sent(anypoint: FakeAnypoint, method: str, path: str) -> Any:
    (request,) = [r for r in anypoint.requests if r.method == method and r.url.path == path]
    return json.loads(request.content)


# -- a first run, then a re-run ------------------------------------------------


async def test_an_mcp_server_is_published_created_governed_and_deployed(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    report = await _run(build(), http)

    assert _statuses(report) == {
        "environment": "present",
        "gateway": "present",
        "registry": "created",
        "instance": "created",
        "policy mcp-support": "created",
        "deployment": "created",
    }
    assert report.ok
    assert report.url == f"{PUBLIC}/{MCP}/"
    created = _sent(anypoint, "POST", f"{APIM}/apis")
    assert created == {
        "spec": {"groupId": ORG, "assetId": MCP, "version": "1.0.0"},
        "endpoint": {
            "type": "mcp",
            "uri": MCP_UPSTREAM,
            "proxyUri": f"http://0.0.0.0:8081/{MCP}",
            "isCloudHub": None,
            "deploymentType": "HY",
        },
        "instanceLabel": MCP,
        "technology": "flexGateway",
    }
    (api_id,) = anypoint.apis
    # The body the captured LLM proxies were given (mcp_instance/).
    assert _sent(anypoint, "POST", f"{APIM}/apis/{api_id}/policies") == body(
        "mcp_instance/policy-mcp-support.request.json"
    )
    deployment = _sent(
        anypoint,
        "POST",
        f"/proxies/xapi/v1/organizations/{ORG}/environments/{ENV}/apis/{api_id}/deployments",
    )
    assert deployment == {
        "type": "HY",
        "gatewayVersion": "1.14.4",
        "targetId": GATEWAY_ID,
        "targetName": GATEWAY,
        "environmentId": ENV,
        "environmentName": "Sandbox",
    }


async def test_a_rerun_changes_nothing(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    built = build()
    await _run(built, http)
    writes = anypoint.writes()

    report = await _run(built, http)

    assert set(_statuses(report).values()) == {"present"}
    assert report.ok
    assert anypoint.writes() == writes
    assert len(anypoint.apis) == 1
    assert list(anypoint.exchange.assets[(ORG, MCP)]) == ["1.0.0"]


async def test_an_a2a_agent_gets_its_outbound_policy_on_its_upstreams(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    report = await _run(build(AGENT), http)

    assert report.ok, report.steps
    assert _statuses(report)["policy credential-injection-api-key"] == "created"
    (api_id,) = anypoint.apis
    assert _sent(anypoint, "POST", f"{APIM}/apis")["endpoint"]["type"] == "a2a"
    outbound = _sent(
        anypoint,
        "POST",
        f"/apimanager/xapi/v1/organizations/{ORG}/environments/{ENV}"
        f"/apis/{api_id}/policies/outbound-policies",
    )
    captured = body("a2a_instance/outbound-policy.request.json")
    del captured["configurationData"]["apiKey"]
    assert outbound == {**captured, "upstreamIds": [f"upstream-{api_id}"], "apiVersionId": api_id}
    assert report.url == f"{PUBLIC}/{AGENT}/"


# -- dry run (and preflight) ---------------------------------------------------


async def test_a_dry_run_reads_and_plans_but_writes_nothing(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    report = await _run(build(), http, dry_run=True)

    assert _statuses(report) == {
        "environment": "present",
        "gateway": "present",
        "registry": "planned",
        "instance": "planned",
        "policy mcp-support": "planned",
        "deployment": "planned",
    }
    assert report.ok
    assert report.dry_run
    assert anypoint.writes() == []


async def test_a_dry_run_after_a_run_finds_everything_present(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    built = build()
    await _run(built, http)
    writes = anypoint.writes()

    report = await _run(built, http, dry_run=True)

    assert set(_statuses(report).values()) == {"present"}
    assert anypoint.writes() == writes


async def test_a_dry_run_on_an_existing_instance_plans_only_what_it_lacks(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.add_instance(MCP, MCP)
    anypoint.exchange.publish(ORG, MCP, "1.0.0", build()[0].asset.digest)  # type: ignore[union-attr]

    report = await _run(build(), http, dry_run=True)

    assert _statuses(report) == {
        "environment": "present",
        "gateway": "present",
        "registry": "present",
        "instance": "present",
        "policy mcp-support": "planned",
        "deployment": "planned",
    }
    assert anypoint.writes() == []


async def test_a_dry_run_fails_a_version_published_with_other_content(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.exchange.publish(ORG, MCP, "1.0.0", "sha256:" + "0" * 64)

    report = await _run(build(), http, dry_run=True)

    assert _statuses(report)["registry"] == "failed"
    assert "Bump the version" in (report.steps[2].remediation or "")
    assert [s.status for s in report.steps[3:]] == ["skipped"] * 3
    assert not report.ok


# -- the platform's resources are never created --------------------------------


async def test_a_missing_environment_is_reported_and_nothing_else_is_tried(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    report = await _run(build(), http, cfg=_cfg(environment="Nope"))

    assert report.steps[0].status == "missing"
    assert "Access Management > Environments" in (report.steps[0].remediation or "")
    assert [s.status for s in report.steps[1:]] == ["skipped"] * 5
    assert len(anypoint.requests) == 1


async def test_a_gateway_is_matched_by_its_exact_name(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    # Gateway Manager's name filter matches substrings: this one is answered.
    anypoint.gateways = [f"{GATEWAY}-2"]

    report = await _run(build(), http)

    gateway = report.steps[1]
    assert gateway.status == "missing"
    assert "Runtime Manager > Flex Gateways" in (gateway.remediation or "")
    assert [s.status for s in report.steps[2:]] == ["skipped"] * 4
    assert anypoint.writes() == []


async def test_a_gateway_that_is_not_running_fails_the_run(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.gateway_status = "APPLYING"

    report = await _run(build(), http)

    assert report.steps[1].status == "failed"
    assert "APPLYING" in report.steps[1].detail
    assert anypoint.writes() == []


# -- what is there is never changed --------------------------------------------


@pytest.mark.parametrize(
    ("existing", "note"),
    [
        ({"uri": "https://elsewhere.example.invalid/mcp"}, "upstream https://elsewhere"),
        ({"version": "0.9.0"}, "asset version 0.9.0, not 1.0.0"),
    ],
)
async def test_an_instance_that_differs_is_left_alone_and_the_rest_still_added(
    build: Build,
    anypoint: FakeAnypoint,
    http: httpx.AsyncClient,
    existing: dict[str, str],
    note: str,
) -> None:
    api_id = anypoint.add_instance(MCP, MCP, **existing)

    report = await _run(build(), http)

    statuses = _statuses(report)
    assert statuses["instance"] == "differs"
    assert note in report.steps[3].detail
    assert statuses["policy mcp-support"] == "created"
    assert statuses["deployment"] == "created"
    assert not report.ok
    assert f"POST {APIM}/apis" not in anypoint.writes()
    assert anypoint.apis[api_id]["assetVersion"] == existing.get("version", "1.0.0")


async def test_a_policy_at_another_version_is_not_reconfigured(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    api_id = anypoint.add_instance(MCP, MCP, target=GATEWAY)
    anypoint.add_policy(api_id, MULESOFT, "mcp-support", "1.0.0")

    report = await _run(build(), http)

    assert _statuses(report)["policy mcp-support"] == "differs"
    assert "mcp-support 1.0.0 is applied, not 1.1.1" in report.steps[4].detail
    assert f"POST {APIM}/apis/{api_id}/policies" not in anypoint.writes()
    assert not report.ok


async def test_an_instance_on_another_gateway_is_not_moved(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.gateways = [GATEWAY, "other-gateway"]
    api_id = anypoint.add_instance(MCP, MCP, target="other-gateway")

    report = await _run(build(), http)

    deployment = report.steps[-1]
    assert deployment.status == "differs"
    assert "deployed to other-gateway, not shared-omni-gateway" in deployment.detail
    assert not [w for w in anypoint.writes() if w.endswith("/deployments")]
    assert anypoint.apis[api_id]["deployment"]["targetName"] == "other-gateway"


async def test_a_label_taken_by_another_asset_fails_the_instance(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.add_instance(MCP, "someone-else")

    report = await _run(build(), http)

    instance = report.steps[3]
    assert instance.status == "failed"
    assert f"{ORG}:someone-else" in instance.detail
    assert [s.status for s in report.steps[4:]] == ["skipped"] * 2
    assert f"POST {APIM}/apis" not in anypoint.writes()


async def test_more_instances_than_one_read_returns_fails_rather_than_guess(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.refuse["list"] = httpx.Response(200, json={"total": 501, "assets": []})

    report = await _run(build(), http)

    assert report.steps[3].status == "failed"
    assert "501 instances" in report.steps[3].detail
    assert f"POST {APIM}/apis" not in anypoint.writes()


# -- refusals ------------------------------------------------------------------


async def test_a_409_on_create_finds_the_instance_made_meanwhile(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def concurrent(request: httpx.Request, _: Any, sent: dict[str, Any]) -> httpx.Response:
        anypoint.add_instance(sent["instanceLabel"], MCP)
        return httpx.Response(409, json=body("mcp_instance/create.409.json"))

    monkeypatch.setattr(anypoint, "_create", concurrent)

    report = await _run(build(), http)

    assert _statuses(report)["instance"] == "present"
    assert report.ok, report.steps
    assert len(anypoint.apis) == 1


async def test_a_create_without_read_exchange_names_that_scope(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.refuse["create"] = httpx.Response(
        400,
        json={
            "name": "BadRequestError",
            "message": "Error while trying to retrieve asset. Reason: Forbidden",
        },
    )

    report = await _run(build(), http)

    instance = report.steps[3]
    assert instance.status == "failed"
    assert "'read:exchange'" in (instance.remediation or "")
    assert [s.status for s in report.steps[4:]] == ["skipped"] * 2


async def test_an_agent_published_as_a2a_v1_is_told_to_bump_its_version(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.refuse["create"] = httpx.Response(
        400, json=body("a2a_instance/create.a2a-v1-protocol.400.json")
    )

    report = await _run(build(AGENT), http)

    instance = report.steps[3]
    assert instance.status == "failed"
    assert "must have an A2A protocol" in instance.detail
    assert "Bump the version in [donkey.publication]" in (instance.remediation or "")
    assert [s.status for s in report.steps[4:]] == ["skipped"] * 2


@pytest.mark.parametrize(
    ("call", "step", "scope"),
    [
        ("create", "instance", "manage:api_configuration"),
        ("inbound", "policy mcp-support", "manage:api_policies"),
        ("deploy", "deployment", "manage:api_proxies"),
        ("gateways", "gateway", "read:servers"),
        ("environments", "environment", "view:environment"),
    ],
)
async def test_a_403_fails_its_step_with_the_scope_it_needs(
    build: Build,
    anypoint: FakeAnypoint,
    http: httpx.AsyncClient,
    call: str,
    step: str,
    scope: str,
) -> None:
    anypoint.refuse[call] = httpx.Response(403, json={"message": "Forbidden"})

    report = await _run(build(), http)

    (failed,) = [s for s in report.steps if s.status == "failed"]
    assert failed.name == step
    assert f"'{scope}'" in (failed.remediation or "")
    later = report.steps[[s.name for s in report.steps].index(step) + 1 :]
    assert {s.status for s in later} <= {"skipped"}
    assert not report.ok


async def test_a_failed_policy_leaves_the_instance_undeployed(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.refuse["inbound"] = httpx.Response(
        400, json=body("a2a_instance/policy-inbound.400.json")
    )

    report = await _run(build(), http)

    assert _statuses(report)["policy mcp-support"] == "failed"
    assert "can not be applied as inbound" in report.steps[4].detail
    assert report.steps[5].status == "skipped"
    assert not [w for w in anypoint.writes() if w.endswith("/deployments")]


async def test_a_deployment_the_gateway_does_not_apply_in_time_fails(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(expose_module, "_DEPLOY_TIMEOUT_S", 0.0)
    anypoint.statuses = ["applying"]

    report = await _run(build(), http)

    deployment = report.steps[-1]
    assert deployment.status == "failed"
    assert "still applying" in deployment.detail
    assert "Re-run donkey expose" in (deployment.remediation or "")
    assert not report.ok


async def test_a_deployment_is_polled_until_applied(
    build: Build, anypoint: FakeAnypoint, http: httpx.AsyncClient
) -> None:
    anypoint.statuses = ["applying", "applying", "applied"]

    report = await _run(build(), http)

    assert report.steps[-1].status == "created"
    assert sum(r.url.path.endswith("/status") for r in anypoint.requests) == 3


# -- the report ----------------------------------------------------------------


async def test_the_report_is_json_ready(build: Build, http: httpx.AsyncClient) -> None:
    report = await _run(build(), http, dry_run=True)

    data = report.to_dict()

    assert data["ok"] is True
    assert data["dry_run"] is True
    assert data["url"] == f"{PUBLIC}/{MCP}/"
    assert data["steps"][0] == {
        "name": "environment",
        "status": "present",
        "detail": f"Sandbox ({ENV})",
        "remediation": None,
    }
    json.dumps(data)


async def test_a_plan_with_issues_is_not_run(build: Build, http: httpx.AsyncClient) -> None:
    plan, result = build()
    issue = ScanIssue("missing-target", "donkey.expose", ".donkey-kit.toml", 0, "name the gateway")
    broken = ExposePlan(plan.asset, issues=(issue,))

    with pytest.raises(ValueError, match="fix them first"):
        await expose(broken, result, http, cfg=_cfg())
