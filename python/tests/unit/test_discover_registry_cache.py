"""``donkey.tools`` over the real ``ExchangeRegistry``, whose lookups are cached too (#1419).

``test_discover.py`` drives ``Discovery`` with a cache-less fake registry. Here the
registry is the real one on an ``httpx.MockTransport``, replaying the bodies
captured from a sandbox: the GraphQL search (tests/fixtures/anypoint/exchange_search/,
docs/verified-apis.md §7), the governed-state reads (mcp_explain/, §6) and the
gateway reads (mcp_resolve/, §6). A policy removed between two discoveries is
what ``refresh()`` and ``warm()`` must see, through the registry's cache (BG §2.7).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.registry.criteria import GovernanceCriteria
from donkey_kit.registry.exchange import ExchangeRegistry
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.discovery import Discovery
from donkey_kit.tools.session import ToolSet

ANYPOINT = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"
ORG = "00000000-0000-4000-8000-4256cc8e6f68"
ENV = "00000000-0000-4000-8000-5c4fd1a49fc3"
GATEWAY = "00000000-0000-4000-8000-d4b0d8dad526"
INSTANCE = 21261048
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG, telemetry=False)
_APIM = f"/apimanager/api/v1/organizations/{ORG}/environments/{ENV}"
_GRAPHQL = "/graph/api/v1/graphql"
_POLICIES = f"{_APIM}/apis/{INSTANCE}/policies"


def _body(path: str) -> Any:
    return json.loads((ANYPOINT / path).read_text(encoding="utf-8"))


class _Anypoint:
    """Answers the search POST and each GET from ``bodies`` (path → body), recording paths."""

    def __init__(self) -> None:
        self.bodies: dict[str, Any] = {
            _GRAPHQL: _body("exchange_search/search.types-mcp.json"),
            f"/accounts/api/organizations/{ORG}/environments": _body(
                "mcp_resolve/environments.json"
            ),
            f"{_APIM}/apis": _body("mcp_explain/apis.by-asset.governed.json"),
            f"{_APIM}/apis/{INSTANCE}": _body("mcp_explain/api.describe.governed.json"),
            _POLICIES: _body("mcp_explain/policies.governed.json"),
            f"/gatewaymanager/api/v1/organizations/{ORG}/environments/{ENV}/gateways/{GATEWAY}": (
                _body("mcp_resolve/gateway.json")
            ),
        }
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.method == ("POST" if path == _GRAPHQL else "GET")
        self.paths.append(path)
        return httpx.Response(200, json=self.bodies[path])

    def remove_the_policies(self) -> None:
        """The platform team removes every policy from the one deployed instance."""
        self.bodies[_POLICIES] = _body("mcp_explain/policies.none.json")


def _discovery(fake: _Anypoint) -> tuple[Discovery, ExchangeRegistry]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    registry = ExchangeRegistry(_CFG, cast(DonkeyAsyncClient, http))
    return Discovery(registry, _CFG, auth=ClientIdAuth("cid", "secret")), registry


async def _governed(d: Discovery) -> ToolSet:
    return await d.discover(domain=None, tags=None, governed=True, auth=None)


async def test_the_first_discover_reads_exchange_and_api_manager() -> None:
    fake = _Anypoint()
    d, _ = _discovery(fake)

    (server,) = (await _governed(d)).servers

    assert server.endpoint_url == "https://shared-omni-gateway.example.invalid/ddk-verify1298-g/"
    assert server.governance is not None and server.governance.governed
    assert fake.paths.count(_GRAPHQL) == fake.paths.count(_POLICIES) == 1


async def test_refresh_rereads_the_governance_verdict_from_api_manager() -> None:
    fake = _Anypoint()
    d, _ = _discovery(fake)
    assert len((await _governed(d)).servers) == 1
    fake.remove_the_policies()

    assert len((await _governed(d)).servers) == 1  # cached, as documented
    d.refresh()
    after = await _governed(d)

    assert after.servers == ()
    assert fake.paths.count(_GRAPHQL) == fake.paths.count(_POLICIES) == 2


async def test_refresh_also_drops_the_registry_lookups_discovery_made() -> None:
    fake = _Anypoint()
    d, registry = _discovery(fake)
    await _governed(d)
    fake.remove_the_policies()

    d.refresh()
    (ref,) = await registry.search(asset_types=["mcp"], limit=200)
    report = await registry.explain(ref, criteria=GovernanceCriteria())

    assert not report.governed
    assert fake.paths.count(_GRAPHQL) == fake.paths.count(_POLICIES) == 2


async def test_warm_reads_through_the_registry_cache() -> None:
    fake = _Anypoint()
    d, _ = _discovery(fake)
    assert len((await _governed(d)).servers) == 1
    fake.remove_the_policies()

    assert await d.warm(domain=None, tags=None, governed=True) == 0
    after = await _governed(d)

    assert after.servers == ()
    assert fake.paths.count(_GRAPHQL) == fake.paths.count(_POLICIES) == 2
