"""``ExchangeRegistry.resolve_mcp()`` against the API Manager and Gateway Manager
bodies captured from a sandbox (#1297, tests/fixtures/anypoint/mcp_resolve/).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError, ConfigError, RegistryError
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.registry.exchange import ExchangeRegistry
from donkey_kit.registry.models import AssetRef, McpServerHandle

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_resolve"
ORG = "00000000-0000-4000-8000-4256cc8e6f68"
ENV = "00000000-0000-4000-8000-5c4fd1a49fc3"
GATEWAY = "00000000-0000-4000-8000-d4b0d8dad526"
REF = f"{ORG}/ddk-demo-sku-lookup/1.1.0"
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG)
_APIM = f"/apimanager/api/v1/organizations/{ORG}/environments/{ENV}"
_ROUTES = {
    f"/accounts/api/organizations/{ORG}/environments": "environments.json",
    f"{_APIM}/apis": "apis.by-asset.json",
    f"{_APIM}/apis/21260926": "api.describe.json",
    f"/gatewaymanager/api/v1/organizations/{ORG}/environments/{ENV}/gateways/{GATEWAY}": (
        "gateway.json"
    ),
}


def _body(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _Anypoint:
    """Answers each GET from ``bodies`` (path → body) and records each request."""

    def __init__(self, **overrides: Any) -> None:
        self.bodies: dict[str, Any] = {path: _body(name) for path, name in _ROUTES.items()}
        self.bodies.update(overrides)
        self.status: dict[str, int] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url).startswith(_CFG.control_plane_url.rstrip("/"))
        self.requests.append(request)
        path = request.url.path
        return httpx.Response(self.status.get(path, 200), json=self.bodies[path])

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def _registry(fake: _Anypoint, cfg: DonkeyConfig = _CFG) -> ExchangeRegistry:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return ExchangeRegistry(cfg, cast(DonkeyAsyncClient, http))


async def test_an_mcp_asset_resolves_to_its_gateway_path() -> None:
    fake = _Anypoint()

    handle = await _registry(fake).resolve_mcp(REF)

    assert handle == McpServerHandle(
        ref=AssetRef.parse(REF),
        endpoint_url="https://shared-omni-gateway.example.invalid/ddk-verify1297-b/",
    )
    assert handle.transport == "streamable_http"
    assert handle.governance is None
    assert fake.paths == list(_ROUTES)


async def test_the_instance_read_filters_on_the_asset_version() -> None:
    fake = _Anypoint()

    await _registry(fake).resolve_mcp(AssetRef.parse(REF))

    assert dict(fake.requests[1].url.params) == {
        "assetId": "ddk-demo-sku-lookup",
        "groupId": ORG,
        "assetVersion": "1.1.0",
    }


async def test_the_environment_is_matched_by_name_ignoring_case() -> None:
    fake = _Anypoint()
    cfg = DonkeyConfig(
        client_id="client", client_secret="secret", org_id=ORG, environment="sandbox"
    )

    await _registry(fake, cfg).resolve_mcp(REF)

    assert ENV in fake.paths[1]


async def test_an_unknown_environment_names_the_ones_there_are() -> None:
    cfg = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG, environment="Prod")

    with pytest.raises(RegistryError, match="'Prod' is not in .*'Design', 'Sandbox'"):
        await _registry(_Anypoint(), cfg).resolve_mcp(REF)


async def test_an_undeployed_instance_is_skipped() -> None:
    """Captured with two instances of the version: one never deployed, one deployed."""
    listing = _body("apis.by-asset.undeployed-and-deployed.json")
    deployed = next(
        api["id"]
        for api in listing["assets"][0]["apis"]
        if (api["deployment"] or {}).get("expectedStatus") == "deployed"
    )
    describe = _body("api.describe.json")
    fake = _Anypoint(**{f"{_APIM}/apis": listing, f"{_APIM}/apis/{deployed}": describe})

    await _registry(fake).resolve_mcp(REF)

    assert fake.paths[2] == f"{_APIM}/apis/{deployed}"


async def test_no_deployed_instance_is_a_registry_error() -> None:
    fake = _Anypoint(**{f"{_APIM}/apis": _body("apis.by-asset.none.json")})

    with pytest.raises(RegistryError, match="no MCP instance deployed .* 'Sandbox'"):
        await _registry(fake).resolve_mcp(REF)


async def test_several_deployed_instances_are_a_registry_error() -> None:
    listing = _body("apis.by-asset.json")
    second = copy.deepcopy(listing["assets"][0]["apis"][0])
    second.update(id=1, instanceLabel="another-label")
    listing["assets"][0]["apis"].append(second)
    fake = _Anypoint(**{f"{_APIM}/apis": listing})

    with pytest.raises(RegistryError, match="2 deployed MCP instances .*another-label"):
        await _registry(fake).resolve_mcp(REF)


async def test_a_non_mcp_instance_does_not_resolve() -> None:
    listing = _body("apis.by-asset.json")
    listing["assets"][0]["apis"][0]["endpointType"] = "llm"
    fake = _Anypoint(**{f"{_APIM}/apis": listing})

    with pytest.raises(RegistryError, match="no MCP instance"):
        await _registry(fake).resolve_mcp(REF)


async def test_a_refused_read_is_an_auth_error_naming_the_scope() -> None:
    fake = _Anypoint(**{f"{_APIM}/apis": {"message": "Forbidden"}})
    fake.status[f"{_APIM}/apis"] = 403

    with pytest.raises(AuthError, match="HTTP 403") as err:
        await _registry(fake).resolve_mcp(REF)

    assert f"'read:api_configuration' on business group {ORG}" in err.value.remediation


async def test_a_failed_read_other_than_401_or_403_is_a_registry_error() -> None:
    fake = _Anypoint(**{f"{_APIM}/apis": {"message": "boom"}})
    fake.status[f"{_APIM}/apis"] = 500

    with pytest.raises(RegistryError, match="HTTP 500"):
        await _registry(fake).resolve_mcp(REF)


async def test_resolution_is_cached_until_refresh() -> None:
    fake = _Anypoint()
    registry = _registry(fake)

    first = await registry.resolve_mcp(REF)
    assert await registry.resolve_mcp(REF) == first
    assert len(fake.requests) == 4

    registry.refresh()
    await registry.resolve_mcp(REF)
    assert len(fake.requests) == 8


async def test_a_bad_ref_is_refused_before_any_read() -> None:
    fake = _Anypoint()

    with pytest.raises(ValueError, match="group_id/asset_id/version"):
        await _registry(fake).resolve_mcp("not-a-ref")
    assert fake.requests == []


async def test_resolution_needs_the_control_plane_settings() -> None:
    fake = _Anypoint()

    with pytest.raises(ConfigError):
        await _registry(fake, DonkeyConfig(client_id="client", client_secret="secret")).resolve_mcp(
            REF
        )
    assert fake.requests == []
