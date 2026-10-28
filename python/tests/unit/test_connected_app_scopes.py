"""A missing connected-app scope, as each registry read meets it (#23).

Each refusal is the one captured live from an app without the scope
(tests/fixtures/anypoint/missing_scope/), served over the reads
``resolve_mcp()``, ``explain()`` and ``search()`` make on the bodies captured
for those (mcp_resolve/, mcp_explain/).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.registry.criteria import GovernanceCriteria
from donkey_kit.registry.exchange import ExchangeRegistry

ANYPOINT = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"
ORG = "00000000-0000-4000-8000-4256cc8e6f68"
ENV = "00000000-0000-4000-8000-5c4fd1a49fc3"
GATEWAY = "00000000-0000-4000-8000-d4b0d8dad526"
REF = f"{ORG}/ddk-demo-sku-lookup/1.1.0"
RESOLVED, GOVERNED, UNGOVERNED = 21260926, 21261048, 21261049
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG)
_APIM = f"/apimanager/api/v1/organizations/{ORG}/environments/{ENV}"
_ENVIRONMENTS = f"/accounts/api/organizations/{ORG}/environments"
_GATEWAY = f"/gatewaymanager/api/v1/organizations/{ORG}/environments/{ENV}/gateways/{GATEWAY}"
_EXCHANGE = f"/exchange/api/v2/assets/{REF}"
_GOVERNANCE = f"/governance/xapi/api/v1/stats/organization/targets/{REF}"
_NONE = GovernanceCriteria(
    require_api_instance=False, require_deployed=False, require_any_policy=False
)


def _missing(call: str) -> dict[str, Any]:
    captured = json.loads((ANYPOINT / "missing_scope" / "responses.json").read_text("utf-8"))
    return dict(captured[call])


def _fixture(folder: str, name: str) -> str:
    return (ANYPOINT / folder / name).read_text(encoding="utf-8")


def _routes() -> dict[str, tuple[int, str]]:
    return {
        _ENVIRONMENTS: (200, _fixture("mcp_resolve", "environments.json")),
        f"{_APIM}/apis": (200, _fixture("mcp_resolve", "apis.by-asset.json")),
        f"{_APIM}/apis/{RESOLVED}": (200, _fixture("mcp_resolve", "api.describe.json")),
        _GATEWAY: (200, _fixture("mcp_resolve", "gateway.json")),
        _EXCHANGE: (200, _fixture("mcp_explain", "asset.version.json")),
        _GOVERNANCE: (200, _fixture("mcp_explain", "governance.reports.none.json")),
    }


def _registry(routes: dict[str, tuple[int, str]]) -> ExchangeRegistry:
    def answer(request: httpx.Request) -> httpx.Response:
        status, body = routes[request.url.path]
        return httpx.Response(status, content=body.encode())

    http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    return ExchangeRegistry(_CFG, cast(DonkeyAsyncClient, http))


def _refusing(path: str, call: str, **routes: tuple[int, str]) -> ExchangeRegistry:
    captured = _missing(call)
    return _registry({**_routes(), **routes, path: (captured["status"], captured["body"])})


@pytest.mark.parametrize(
    ("path", "call", "status"),
    [
        (_ENVIRONMENTS, "environments", 401),
        (f"{_APIM}/apis", "api_list", 403),
        (f"{_APIM}/apis/{RESOLVED}", "api_describe", 403),
        (_GATEWAY, "gateway", 403),
    ],
)
async def test_a_resolve_read_without_its_scope_names_it(path: str, call: str, status: int) -> None:
    scope = _missing(call)["scope"]

    with pytest.raises(AuthError, match=f"HTTP {status}") as err:
        await _refusing(path, call).resolve_mcp(REF)

    assert f"'{scope}' on business group {ORG}" in err.value.remediation
    assert "https://docs.donkey-kit.dev/reference/scopes" in err.value.remediation
    assert err.value.response is not None


async def test_a_policy_read_without_its_scope_names_it() -> None:
    explain = {
        f"{_APIM}/apis": (200, _fixture("mcp_explain", "apis.by-asset.governed.json")),
        f"{_APIM}/apis/{UNGOVERNED}/policies": (
            200,
            _fixture("mcp_explain", "policies.none.json"),
        ),
    }
    registry = _refusing(f"{_APIM}/apis/{GOVERNED}/policies", "api_policies", **explain)

    with pytest.raises(AuthError, match="HTTP 403") as err:
        await registry.explain(REF, criteria=GovernanceCriteria())

    assert "'read:api_policies'" in err.value.remediation


async def test_an_exchange_read_without_its_scope_is_unknown_and_names_it() -> None:
    criteria = replace(_NONE, require_tags=["a"])

    report = await _refusing(_EXCHANGE, "exchange_version").explain(REF, criteria=criteria)

    assert report.checks[0].passed is None
    assert "HTTP 403" in report.checks[0].detail
    assert "'read:exchange'" in report.checks[0].detail


async def test_a_governance_read_without_its_scope_is_unknown_and_names_it() -> None:
    criteria = replace(_NONE, require_governance_pass=True)

    report = await _refusing(_GOVERNANCE, "governance").explain(REF, criteria=criteria)

    assert report.checks[-1].passed is None
    assert "'read:exchange'" in report.checks[-1].detail


async def test_a_search_without_read_exchange_finds_nothing_rather_than_failing() -> None:
    """Exchange answers 200 with no assets: there is no refusal to name a scope in."""
    registry = _refusing("/graph/api/v1/graphql", "exchange_search")

    assert await registry.search(query="sku") == []
