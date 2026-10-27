"""``ExchangeRegistry.search()`` against the GraphQL bodies captured from a
sandbox (#53, tests/fixtures/anypoint/exchange_search/).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError, RegistryError
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.registry import exchange
from donkey_kit.registry.exchange import ExchangeRegistry
from donkey_kit.registry.models import AssetRef

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "exchange_search"
ORG = "00000000-0000-4000-8000-4256cc8e6f68"
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG)
_GRAPHQL = _CFG.control_plane_url.rstrip("/") + "/graph/api/v1/graphql"


def _body(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


class _Exchange:
    """Answers each GraphQL POST with ``answer(search)`` and records each ``$query``."""

    def __init__(self, answer: Callable[[dict[str, Any]], httpx.Response]) -> None:
        self.answer = answer
        self.variables: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == _GRAPHQL
        payload = json.loads(request.content)
        assert payload["query"] == exchange._SEARCH_QUERY
        self.variables.append(payload["variables"]["query"])
        return self.answer(payload["variables"]["query"])


def _replay(name: str, status: int = 200) -> _Exchange:
    return _Exchange(lambda _: httpx.Response(status, json=_body(name)))


def _registry(fake: _Exchange, cfg: DonkeyConfig = _CFG) -> ExchangeRegistry:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return ExchangeRegistry(cfg, cast(DonkeyAsyncClient, http))


async def test_asset_types_are_an_exchange_filter() -> None:
    fake = _replay("search.types-mcp.json")

    (ref,) = await _registry(fake).search(asset_types=["mcp"])

    assert fake.variables == [
        {"organizationIds": [ORG], "types": ["mcp"], "limit": 50, "offset": 0}
    ]
    assert ref == AssetRef(
        group_id=ORG,
        asset_id="ddk-demo-sku-lookup",
        version="1.1.0",
        name="SKU Lookup MCP Server",
        type="mcp",
        description=ref.description,
    )
    assert ref.description and ref.description.startswith("Looks up a product by its SKU")


async def test_api_is_every_exchange_api_type() -> None:
    fake = _replay("search.api-types.json")

    assert await _registry(fake).search(asset_types=["api"]) == []
    assert fake.variables[0]["types"] == ["rest-api", "http-api", "soap-api"]


async def test_a_plain_query_is_exchanges_search_term() -> None:
    fake = _replay("search.term.json")

    refs = await _registry(fake).search(query="sku", limit=10)

    assert fake.variables == [
        {"organizationIds": [ORG], "searchTerm": "sku", "limit": 10, "offset": 0}
    ]
    assert [r.asset_id for r in refs] == ["ddk-demo-sku-lookup"]


async def test_tags_are_exchange_tags() -> None:
    fake = _replay("search.tags.json")

    (ref,) = await _registry(fake).search(asset_types=["agent"], tags=["CrewAI"])

    assert fake.variables[0]["tags"] == [{"value": "CrewAI"}]
    assert ref.asset_id == "ddk-crewai-agent"
    assert ref.tags == ("ddk", "a2a", "crewai", "demo")


async def test_a_tag_exchange_derives_does_not_match() -> None:
    """``v0.3`` matches every agent in Exchange, but only as a keyed, derived tag."""
    fake = _replay("search.tags-derived.json")

    assert await _registry(fake).search(asset_types=["agent"], tags=["v0.3"]) == []


async def test_a_description_word_is_matched_by_exchange() -> None:
    fake = _replay("search.description-term.json")

    refs = await _registry(fake).search(query="keycloak", asset_types=["agent"], limit=3)

    assert fake.variables[0]["searchTerm"] == "keycloak"
    assert all("Keycloak" in (r.description or "") for r in refs)


async def test_a_glob_is_matched_here_over_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exchange does not match inside words, so a glob fetches pages and matches here."""
    monkeypatch.setattr(exchange, "_PAGE_MAX", 5)
    pages = {0: "search.page1.json", 5: "search.page2.json"}
    fake = _Exchange(lambda v: httpx.Response(200, json=_body(pages[v["offset"]])))

    refs = await _registry(fake).search(query="*AGENT*", asset_types=["mcp", "agent"])

    assert [v["offset"] for v in fake.variables] == [0, 5]
    assert all("searchTerm" not in v and v["limit"] == 5 for v in fake.variables)
    assert len(refs) == 8
    assert {r.type for r in refs} == {"agent"}
    assert all("ddk" in r.tags for r in refs)


async def test_a_glob_matches_the_name_and_description() -> None:
    fake = _replay("search.types-mcp.json")

    assert [r.asset_id for r in await _registry(fake).search(query="sku lookup*")]
    assert [r.asset_id for r in await _registry(fake).search(query="*by its sku*")]
    assert await _registry(fake).search(query="*ku-loo*")
    assert await _registry(fake).search(query="*agent*") == []


async def test_limit_stops_the_search() -> None:
    fake = _replay("search.page1.json")

    refs = await _registry(fake).search(asset_types=["mcp", "agent"], limit=5)

    assert len(refs) == 5
    assert len(fake.variables) == 1


async def test_a_large_limit_pages_at_exchanges_maximum() -> None:
    fake = _replay("search.page2.json")

    refs = await _registry(fake).search(limit=1000)

    assert fake.variables[0]["limit"] == 250
    assert len(refs) == 4


async def test_an_exchange_refusal_is_a_registry_error() -> None:
    fake = _replay("search.unknown-field.400.json", status=400)

    with pytest.raises(RegistryError, match="HTTP 400.*SearchAsset"):
        await _registry(fake).search()


async def test_a_graphql_error_is_a_registry_error() -> None:
    fake = _Exchange(
        lambda _: httpx.Response(
            200, json={"errors": [{"message": "Unknown argument"}], "data": {"assets": None}}
        )
    )

    with pytest.raises(RegistryError, match="Unknown argument"):
        await _registry(fake).search()


async def test_results_are_cached_until_refresh() -> None:
    fake = _replay("search.types-mcp.json")
    registry = _registry(fake)

    first = await registry.search(asset_types=["mcp"])
    assert await registry.search(asset_types=["mcp"]) == first
    assert len(fake.variables) == 1

    registry.refresh()
    await registry.search(asset_types=["mcp"])
    assert len(fake.variables) == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"domain": "hr"},
        {"governed": True},
        {"environment": "Production"},
        {"asset_types": ["a2a-agent"]},
    ],
    ids=["domain", "governed", "environment", "a2a-agent"],
)
async def test_unverified_filters_stay_blocked(kwargs: dict[str, Any]) -> None:
    fake = _replay("search.types-mcp.json")

    with pytest.raises(NotImplementedError, match="blocked on verification"):
        await _registry(fake).search(**kwargs)
    assert fake.variables == []


async def test_limit_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        await _registry(_replay("search.types-mcp.json")).search(limit=0)


async def test_search_needs_the_control_plane_settings() -> None:
    fake = _replay("search.types-mcp.json")
    cfg = DonkeyConfig(client_id="client", client_secret="secret")

    with pytest.raises(ConfigError):
        await _registry(fake, cfg).search()
    assert fake.variables == []
