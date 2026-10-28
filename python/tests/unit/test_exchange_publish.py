"""``donkey_kit.registry.exchange_publish`` against a fake Exchange that replays
the bodies captured from a sandbox (#218, tests/fixtures/anypoint/exchange_publish/).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from _fake_exchange import BASE_URL, ORG, FakeExchange

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError, DonkeyError, RegistryError
from donkey_kit.core.runtime import Runtime
from donkey_kit.registry.exchange_page import PageSource
from donkey_kit.registry.exchange_publish import (
    PublishOutcome,
    agent_card,
    agent_metadata,
    latest_version,
    mcp_metadata,
    publish_plan,
)
from donkey_kit.registry.publish_plan import PublishPlan, plan_publication
from donkey_kit.registry.scan import ScanResult, scan

ASSET = "sku-manager"
AGENT = "pricing-agent"
AGENT_ENDPOINT = "https://agents.example.invalid/pricing/"

SOURCE = '''\
from donkey_kit import Donkey

donkey = Donkey.from_env()


@donkey.tool(server="sku-manager")
def lookup_sku(sku: str, limit: int, filters: "Filters") -> dict:
    """{description}"""


@donkey.agent(name="pricing-agent", skills=["quote", "discount"])
def pricing() -> None:
    """Answers pricing questions for the store."""
'''

DESCRIPTION = "Look up a SKU's price and stock level by its code."

Repo = Callable[..., tuple[PublishPlan, ScanResult]]


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    def write(
        version: str = "1.0.0",
        description: str = DESCRIPTION,
        mcp_server: str = 'transport = "streamable-http"\nendpoint = "https://sku.example.invalid/mcp"\n',
        publication: str = "",
        agent: str = f'protocol = "a2a_v1"\nendpoint = "{AGENT_ENDPOINT}"\n',
    ) -> tuple[PublishPlan, ScanResult]:
        (tmp_path / "app.py").write_text(SOURCE.format(description=description), encoding="utf-8")
        (tmp_path / ".donkey-kit.toml").write_text(
            f'[donkey.publication]\nversion = "{version}"\n{publication}\n'
            f"[donkey.mcp_servers.{ASSET}]\n{mcp_server}\n"
            f"[donkey.agents.{AGENT}]\n{agent}",
            encoding="utf-8",
        )
        result = scan(tmp_path)
        return plan_publication(result, tmp_path, default_group_id=ORG), result

    return write


@pytest.fixture
def exchange() -> FakeExchange:
    return FakeExchange()


@pytest.fixture
async def http(exchange: FakeExchange) -> AsyncIterator[httpx.AsyncClient]:
    runtime = Runtime(DonkeyConfig(org_id=ORG, base_url=BASE_URL), auth=StaticToken("tok"))
    runtime.control_http.governed_transport.replace_inner(exchange.transport())
    yield runtime.control_http
    await runtime.aclose()


async def _publish(
    http: httpx.AsyncClient,
    built: tuple[PublishPlan, ScanResult],
    *,
    if_changed: bool = True,
    source: PageSource | None = None,
) -> dict[str, PublishOutcome]:
    plan, result = built
    outcomes = await publish_plan(
        plan, result, http, base_url=BASE_URL, org_id=ORG, if_changed=if_changed, source=source
    )
    return {o.asset.service_id: o for o in outcomes}


def _publishes(exchange: FakeExchange, asset: str = ASSET) -> list[httpx.Request]:
    return [
        r
        for r in exchange.requests
        if "/organizations/" in r.url.path and f"/{asset}/" in r.url.path
    ]


async def test_first_publish_sends_the_digest_metadata_and_endpoint(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    built = repo()

    outcomes = await _publish(http, built)

    assert outcomes[ASSET].status == "published"
    published = exchange.assets[(ORG, ASSET)]["1.0.0"]
    assert published.digest == built[0].assets[1].digest
    assert published.metadata == mcp_metadata(built[0].assets[1], built[1])
    assert exchange.instances[(ORG, ASSET, "v1.0")] == ["https://sku.example.invalid/mcp"]
    (request,) = _publishes(exchange)
    assert request.url.path == f"/exchange/api/v2/organizations/{ORG}/assets/{ORG}/{ASSET}/1.0.0"


async def test_the_bearer_goes_to_anypoint_and_never_to_the_pom_link(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())
    await _publish(http, repo())  # reads the POM back

    pom = [r for r in exchange.requests if r.url.host == "exchange-files.example.invalid"]
    anypoint = [r for r in exchange.requests if r.url.host == "anypoint.mulesoft.com"]
    assert pom and anypoint
    assert all("authorization" not in r.headers for r in pom)
    assert all(r.headers["authorization"] == "Bearer tok" for r in anypoint)


def test_mcp_metadata_has_the_plugins_shape(repo: Repo) -> None:
    plan, result = repo()

    metadata = mcp_metadata(plan.assets[1], result)

    assert metadata == {
        "transport": {"kind": "streamableHttp", "path": "/mcp"},
        "tools": [
            {
                "name": "lookup_sku",
                "description": DESCRIPTION,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "limit": {"type": "integer"},
                        "filters": {},
                    },
                },
            }
        ],
        "capabilities": {"tools": True, "resources": False, "prompts": False, "logging": False},
        "provenance": {"id": f"urn:gav:{ORG}:{ASSET}:1.0.0"},
    }


def test_mcp_metadata_carries_the_declared_protocol_version_and_provider(repo: Repo) -> None:
    plan, result = repo(
        publication='protocol_version = "2025-06-18"\n'
        'provider = { organization = "Acme", url = "https://acme.example.invalid" }\n'
    )

    metadata = mcp_metadata(plan.assets[1], result)

    assert metadata["protocolVersion"] == "2025-06-18"
    assert metadata["provider"] == {"organization": "Acme", "url": "https://acme.example.invalid"}


def test_mcp_metadata_leaves_out_what_is_undeclared(repo: Repo) -> None:
    plan, result = repo()

    metadata = mcp_metadata(plan.assets[1], result)

    assert "protocolVersion" not in metadata and "provider" not in metadata


@pytest.mark.parametrize(
    ("declared", "transport"),
    [
        ("", {"kind": "streamableHttp"}),
        ('transport = "stdio"\n', {"kind": "stdio"}),
        (
            'transport = "sse"\nendpoint = "https://sku.example.invalid/sse"\n',
            {"kind": "sse", "ssePath": "/sse"},
        ),
    ],
)
def test_mcp_metadata_transport(repo: Repo, declared: str, transport: dict[str, str]) -> None:
    plan, result = repo(mcp_server=declared)

    assert mcp_metadata(plan.assets[1], result)["transport"] == transport


async def test_a_rerun_with_nothing_changed_publishes_nothing(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())

    outcomes = await _publish(http, repo())

    assert outcomes[ASSET].status == "unchanged"
    assert len(_publishes(exchange)) == 1


async def test_a_version_bump_alone_publishes_nothing_unless_always(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())

    unchanged = await _publish(http, repo(version="1.0.1"))
    always = await _publish(http, repo(version="1.0.1"), if_changed=False)

    assert unchanged[ASSET].status == "unchanged"
    assert unchanged[ASSET].detail == "the latest version, 1.0.0, has this digest"
    assert always[ASSET].status == "published"
    assert list(exchange.assets[(ORG, ASSET)]) == ["1.0.0", "1.0.1"]


async def test_a_new_version_with_new_content_is_published_and_keeps_one_endpoint(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())

    outcomes = await _publish(http, repo(version="1.0.1", description="Look up a SKU's stock."))

    assert outcomes[ASSET].status == "published"
    assert exchange.instances[(ORG, ASSET, "v1.0")] == ["https://sku.example.invalid/mcp"]


@pytest.mark.parametrize("version", ["1.0.0", "0.9.0"])
async def test_a_published_version_with_other_content_fails_before_sending(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient, version: str
) -> None:
    exchange.publish(ORG, ASSET, "0.9.0", "sha256:" + "0" * 64)
    await _publish(http, repo())

    with pytest.raises(RegistryError, match=f"{ASSET}:{version} is already published") as err:
        await _publish(http, repo(version=version, description="Look up a SKU's stock."))

    assert "Bump the version" in err.value.remediation
    assert len(_publishes(exchange)) == 1


async def test_a_latest_version_without_a_digest_is_published_over(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    exchange.publish(ORG, ASSET, "0.9.0", None)

    outcomes = await _publish(http, repo())

    assert outcomes[ASSET].status == "published"


@pytest.mark.parametrize(
    ("same_digest", "published"), [(True, True), (False, False)], ids=["landed", "conflict"]
)
async def test_a_409_is_fine_only_when_the_version_has_this_digest(
    repo: Repo,
    exchange: FakeExchange,
    http: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    same_digest: bool,
    published: bool,
) -> None:
    built = repo()
    digest = built[0].assets[1].digest if same_digest else "sha256:" + "0" * 64
    handle = exchange.handle

    def racing(request: httpx.Request) -> httpx.Response:
        # The version appears between the read and the publish, as when an
        # earlier attempt of this request landed before a retry.
        if "/organizations/" in request.url.path and f"/{ASSET}/" in request.url.path:
            exchange.publish(ORG, ASSET, "1.0.0", digest)
        return handle(request)

    monkeypatch.setattr(exchange, "handle", racing)

    if published:
        assert (await _publish(http, built))[ASSET].status == "published"
    else:
        with pytest.raises(RegistryError, match="already published"):
            await _publish(http, built)


def _named(name: str, description: str | None = None) -> str:
    table = f'[donkey.publication.assets.{ASSET}]\nname = "{name}"\n'
    return table + (f'description = "{description}"\n' if description else "")


def _detail_patches(exchange: FakeExchange) -> list[httpx.Request]:
    return [r for r in exchange.requests if r.method == "PATCH" and "/api/v2/" in r.url.path]


async def test_a_first_publish_names_the_asset_without_a_patch(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    outcomes = await _publish(http, repo(publication=_named("SKU Lookup", "Looks up SKUs.")))

    assert outcomes[ASSET].detail == "published 1.0.0"
    assert exchange.details[(ORG, ASSET)] == {"name": "SKU Lookup", "description": "Looks up SKUs."}
    assert _detail_patches(exchange) == []


async def test_a_new_version_applies_a_changed_name_and_description(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())

    outcomes = await _publish(
        http, repo(version="1.1.0", publication=_named("SKU Lookup", "Looks up SKUs."))
    )

    assert outcomes[ASSET].status == "published"
    assert outcomes[ASSET].detail == ("published 1.1.0; updated the asset's name and description")
    assert exchange.details[(ORG, ASSET)] == {"name": "SKU Lookup", "description": "Looks up SKUs."}
    (patch,) = _detail_patches(exchange)
    assert patch.url.path == f"/exchange/api/v2/assets/{ORG}/{ASSET}"


async def test_only_what_changed_is_patched(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo(publication=_named("SKU Lookup", "Looks up SKUs.")))

    outcomes = await _publish(
        http, repo(version="1.1.0", publication=_named("SKU Lookup", "Finds SKUs."))
    )

    assert outcomes[ASSET].detail == "published 1.1.0; updated the asset's description"
    (patch,) = _detail_patches(exchange)
    assert json.loads(patch.content) == {"description": "Finds SKUs."}


async def test_an_unchanged_name_and_an_undeclared_description_are_left_alone(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    exchange.publish(ORG, ASSET, "0.9.0", None, name="SKU Lookup", description="Set by hand.")

    outcomes = await _publish(http, repo(publication=_named("SKU Lookup")))

    assert outcomes[ASSET].detail == "published 1.0.0"
    assert exchange.details[(ORG, ASSET)]["description"] == "Set by hand."
    assert _detail_patches(exchange) == []


async def test_a_refused_name_update_is_a_registry_error(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())
    exchange.fail_details = True

    with pytest.raises(RegistryError, match="updating the name and description of") as err:
        await _publish(http, repo(version="1.1.0", publication=_named("SKU Lookup")))

    assert "re-run donkey publish" in err.value.remediation


async def test_a_new_version_gets_its_page(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    source = PageSource(repository_url="https://github.example.invalid/acme/sku", commit="a" * 40)

    await _publish(http, repo(), source=source)

    page = exchange.pages[(ORG, ASSET, "1.0.0")]
    assert page.startswith(f"# {ASSET}\n")
    assert "### lookup_sku" in page
    assert "https://github.example.invalid/acme/sku/commit/" + "a" * 40 in page
    drafts = [r for r in exchange.requests if f"/{ASSET}/1.0.0/portal/draft" in r.url.path]
    assert [r.method for r in drafts] == ["GET", "POST", "PUT"]


async def test_an_unchanged_asset_keeps_its_page(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())
    before = len(exchange.requests)

    await _publish(http, repo())

    assert not any("/portal/" in r.url.path for r in exchange.requests[before:])


async def test_a_page_that_exists_already_is_rewritten(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    exchange.publish(ORG, ASSET, "0.9.0", None)
    exchange.pages[(ORG, ASSET, "1.0.0")] = "stale"
    built = repo()

    await _publish(http, built)

    assert exchange.pages[(ORG, ASSET, "1.0.0")].startswith(f"# {ASSET}\n")


async def test_a_refused_page_is_a_registry_error(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    exchange.fail_pages = True

    with pytest.raises(RegistryError, match="writing the page of"):
        await _publish(http, repo())


async def test_an_a2a_agent_is_published_with_its_metadata_card_and_endpoint(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    plan, result = repo()

    outcomes = await _publish(http, (plan, result))

    assert outcomes[AGENT].status == "published"
    planned = plan.assets[0]
    published = exchange.assets[(ORG, AGENT)]["1.0.0"]
    assert published.type == "agent"
    assert published.digest == planned.digest
    assert published.metadata == agent_metadata(planned)
    assert published.files["files.a2a-v1-card.json"] == agent_card(planned, result)
    assert set(published.files) == {"files.agent-metadata.json", "files.a2a-v1-card.json"}
    assert (published.fields["properties.protocol"], published.fields["properties.platform"]) == (
        "a2a",
        "other",
    )
    assert exchange.instances[(ORG, AGENT, "v1.0")] == [AGENT_ENDPOINT]
    page = exchange.pages[(ORG, AGENT, "1.0.0")]
    assert "## Skills" in page and "- quote" in page and "**Protocol:** a2a_v1" in page


async def test_an_undeclared_agent_is_published_as_other_without_a_card(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo(agent=""))

    published = exchange.assets[(ORG, AGENT)]["1.0.0"]
    assert set(published.files) == {"files.agent-metadata.json"}
    assert published.metadata["protocol"] == "other"
    assert published.fields["properties.protocol"] == "other"
    assert (ORG, AGENT, "v1.0") not in exchange.instances


async def test_a_rerun_publishes_no_agent_and_new_agent_content_needs_a_bump(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    await _publish(http, repo())

    rerun = await _publish(http, repo())
    assert rerun[AGENT].status == "unchanged"
    with pytest.raises(RegistryError, match=f"{AGENT}:1.0.0 is already published") as err:
        await _publish(http, repo(agent='protocol = "other"\n'))

    assert "Bump the version" in err.value.remediation
    assert len(_publishes(exchange, AGENT)) == 1


def test_agent_metadata_has_the_plugins_shape(repo: Repo) -> None:
    plan, _ = repo()

    assert agent_metadata(plan.assets[0]) == {
        "protocol": "a2a",
        "platform": "other",
        "kind": "agent",
        "name": AGENT,
        "description": "Answers pricing questions for the store.",
        "connections": [],
        "provenance": {"id": f"urn:gav:{ORG}:{AGENT}:1.0.0"},
    }


def test_the_published_card_is_the_card_donkey_serve_serves(repo: Repo) -> None:
    pytest.importorskip("a2a")
    from starlette.testclient import TestClient

    from donkey_kit.a2a import build_app

    plan, result = repo()
    namespace: dict[str, object] = {}
    exec(SOURCE.format(description=DESCRIPTION), namespace)  # the scanned module, run
    app = build_app(namespace["pricing"], url=AGENT_ENDPOINT, version="1.0.0")  # type: ignore[arg-type]

    with TestClient(app) as client:  # type: ignore[arg-type]
        served = client.get("/.well-known/agent-card.json").json()

    assert agent_card(plan.assets[0], result) == served


def test_exchange_stores_the_metadata_and_card_as_built(tmp_path: Path) -> None:
    # The repo the agent.* fixtures were published from (#1260), scrubbed the same way.
    from _fake_exchange import fixture

    (tmp_path / "app.py").write_text(
        "from donkey_kit import Donkey\n\ndonkey = Donkey.from_env()\n\n\n"
        '@donkey.agent(name="ddk-verify-1260-fixture", skills=["triage", "draft-reply"])\n'
        "def handle(task):\n"
        '    """Triage support tickets into queues; throwaway asset for #1260, deleted right'
        ' after."""\n',
        encoding="utf-8",
    )
    (tmp_path / ".donkey-kit.toml").write_text(
        '[donkey.publication]\nversion = "1.0.0"\n\n'
        "[donkey.agents.ddk-verify-1260-fixture]\n"
        'protocol = "a2a_v1"\nendpoint = "https://agents.example.invalid/support/"\n',
        encoding="utf-8",
    )
    result = scan(tmp_path)
    (asset,) = plan_publication(result, tmp_path, default_group_id=ORG).assets

    assert agent_metadata(asset) == json.loads(fixture("agent.agent-metadata.stored.json"))
    assert agent_card(asset, result) == json.loads(fixture("agent.a2a-v1-card.stored.json"))


async def test_an_agents_digest_is_read_back_from_its_pom(
    exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    exchange.publish(ORG, AGENT, "1.0.0", "sha256:" + "3" * 64, kind="agent")

    latest = await latest_version(http, ORG, AGENT, base_url=BASE_URL)

    assert latest is not None and latest.digest == "sha256:" + "3" * 64


def test_an_a2a_card_needs_an_endpoint(repo: Repo) -> None:
    from dataclasses import replace

    plan, result = repo()

    with pytest.raises(ValueError, match="no endpoint"):
        agent_card(replace(plan.assets[0], endpoint=None), result)


async def test_a_plan_with_issues_is_refused(
    tmp_path: Path, exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    (tmp_path / "app.py").write_text(SOURCE.format(description=DESCRIPTION), encoding="utf-8")
    result = scan(tmp_path)
    plan = plan_publication(result, tmp_path)

    with pytest.raises(ValueError, match="plan with issues"):
        await publish_plan(plan, result, http, base_url=BASE_URL, org_id=ORG)

    assert exchange.requests == []


@pytest.mark.parametrize(
    ("status", "error", "remediation"),
    [
        (401, AuthError, "'manage:exchange'"),
        (403, AuthError, "'manage:exchange'"),
        (400, RegistryError, "Fix what Exchange reported"),
    ],
)
async def test_a_refused_publish_names_the_scope_or_the_fix(
    repo: Repo,
    exchange: FakeExchange,
    http: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    error: type[DonkeyError],
    remediation: str,
) -> None:
    handle = exchange.handle

    def refusing(request: httpx.Request) -> httpx.Response:
        if "/organizations/" in request.url.path:
            return httpx.Response(status, json={"message": "no"})
        return handle(request)

    monkeypatch.setattr(exchange, "handle", refusing)

    with pytest.raises(error, match=f"HTTP {status}") as err:
        await _publish(http, repo())

    assert remediation in err.value.remediation


async def test_an_endpoint_refused_with_404_names_manage_exchange(
    repo: Repo, exchange: FakeExchange, http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What a connected app with only edit:exchange got live (#23): the version is
    published, then the external-instance POST answers 404."""
    handle = exchange.handle

    def refusing(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/instances/external"):
            return httpx.Response(404, json={"message": "The requested instance does not exist."})
        return handle(request)

    monkeypatch.setattr(exchange, "handle", refusing)

    with pytest.raises(RegistryError, match="registering the endpoint") as err:
        await _publish(http, repo())

    assert "'manage:exchange'" in err.value.remediation


async def test_a_graphql_error_other_than_not_found_is_raised(
    exchange: FakeExchange, http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = {"errors": [{"message": "boom", "extensions": {"status": 500}}], "data": None}
    monkeypatch.setattr(exchange, "handle", lambda _r: httpx.Response(200, json=body))

    with pytest.raises(RegistryError, match="GraphQL query failed: boom"):
        await latest_version(http, ORG, ASSET, base_url=BASE_URL)


async def test_latest_version_reads_the_digest_back(
    exchange: FakeExchange, http: httpx.AsyncClient
) -> None:
    assert await latest_version(http, ORG, ASSET, base_url=BASE_URL) is None
    exchange.publish(ORG, ASSET, "1.0.0", "sha256:" + "1" * 64)
    exchange.publish(ORG, ASSET, "1.1.0", "sha256:" + "2" * 64)

    latest = await latest_version(http, ORG, ASSET, base_url=BASE_URL)

    assert latest is not None
    assert (latest.version, latest.digest, latest.versions) == (
        "1.1.0",
        "sha256:" + "2" * 64,
        ("1.1.0", "1.0.0"),
    )
    assert (latest.name, latest.description) == (ASSET, "")


def test_the_captured_bodies_are_the_shapes_the_publisher_reads() -> None:
    from _fake_exchange import fixture

    latest = json.loads(fixture("graphql.latest.json"))["data"]["asset"]
    poms = [f for f in latest["files"] if f["packaging"] == "pom" and not f["classifier"]]
    assert len(poms) == 1
    renamed = json.loads(fixture("graphql.renamed.json"))["data"]["asset"]
    assert latest["name"] != renamed["name"] and latest["description"] != renamed["description"]
    absent = json.loads(fixture("graphql.absent.json"))
    assert absent["data"] is None
    assert absent["errors"][0]["extensions"]["status"] == 404
    assert "<ddkDigest>sha256:" in fixture("pom.xml")
    assert json.loads(fixture("publish.409.json"))["code"] == "ASSET_PRE_CONDITIONS_FAILED"
    agent = json.loads(fixture("agent.graphql.latest.json"))["data"]["asset"]
    classifiers = {(f["classifier"], f["packaging"]) for f in agent["files"]}
    assert {("agent-metadata", "json"), ("a2a-v1-card", "json"), (None, "pom")} <= classifiers
    assert json.loads(fixture("agent.publish.201.json"))["type"] == "agent"
    pom = fixture("agent.pom.xml")
    assert "<type>agent</type>" in pom and "<protocol>a2a</protocol>" in pom
    assert "<ddkDigest>sha256:" in pom
