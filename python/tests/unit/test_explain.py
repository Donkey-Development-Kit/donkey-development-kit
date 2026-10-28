"""``ExchangeRegistry.explain()`` against the API Manager, Exchange and API Governance
bodies captured from a sandbox (#1298, tests/fixtures/anypoint/mcp_explain/).
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError, RegistryError
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.registry.criteria import STRICT, Check, GovernanceCriteria
from donkey_kit.registry.exchange import ExchangeRegistry

ANYPOINT = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"
FIXTURES = ANYPOINT / "mcp_explain"
ORG = "00000000-0000-4000-8000-4256cc8e6f68"
ENV = "00000000-0000-4000-8000-5c4fd1a49fc3"
ASSET = "ddk-demo-sku-lookup/1.1.0"
REF = f"{ORG}/{ASSET}"
GOVERNED, UNGOVERNED = 21261048, 21261049
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG)
_APIM = f"/apimanager/api/v1/organizations/{ORG}/environments/{ENV}"
_ENVIRONMENTS = f"/accounts/api/organizations/{ORG}/environments"
_LIST = f"{_APIM}/apis"
_EXCHANGE = f"/exchange/api/v2/assets/{REF}"
_GOVERNANCE = f"/governance/xapi/api/v1/stats/organization/targets/{REF}"
# Every condition off: each test turns on the ones it is about.
_NONE = GovernanceCriteria(
    require_api_instance=False, require_deployed=False, require_any_policy=False
)


def _body(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _routes() -> dict[str, Any]:
    """The governed instance, deployed with ``mcp-support`` and ``client-id-enforcement``."""
    return {
        _ENVIRONMENTS: json.loads(
            (ANYPOINT / "mcp_resolve" / "environments.json").read_text(encoding="utf-8")
        ),
        _LIST: _body("apis.by-asset.governed.json"),
        f"{_APIM}/apis/{GOVERNED}": _body("api.describe.governed.json"),
        f"{_APIM}/apis/{GOVERNED}/policies": _body("policies.governed.json"),
        f"{_APIM}/apis/{UNGOVERNED}/policies": _body("policies.none.json"),
        _EXCHANGE: _body("asset.version.json"),
        _GOVERNANCE: _body("governance.reports.none.json"),
    }


class _Anypoint:
    """Answers each GET from ``bodies`` (path → body) and records each request."""

    def __init__(self, **overrides: Any) -> None:
        self.bodies: dict[str, Any] = _routes()
        self.bodies.update(overrides)
        self.status: dict[str, int] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        self.requests.append(request)
        path = request.url.path
        return httpx.Response(self.status.get(path, 200), json=self.bodies[path])

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def _registry(fake: _Anypoint, cfg: DonkeyConfig = _CFG) -> ExchangeRegistry:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return ExchangeRegistry(cfg, cast(DonkeyAsyncClient, http))


def _ungoverned() -> _Anypoint:
    """The instance captured deployed with no policy."""
    return _Anypoint(**{_LIST: _body("apis.by-asset.ungoverned.json")})


def _report(summary: str) -> dict[str, Any]:
    body = copy.deepcopy(_body("governance.reports.conformant.json"))
    body["reports"][0]["summaryStatus"] = summary
    return cast(dict[str, Any], body)


async def test_a_governed_instance_passes_the_default_criteria() -> None:
    report = await _registry(_Anypoint()).explain(REF, criteria=GovernanceCriteria())

    assert report.governed is True
    assert report.checks == [
        Check("api_instance_exists", True, "instances ddk-verify1298-g in environment 'Sandbox'"),
        Check("deployed", True, "deployed: ddk-verify1298-g"),
        Check("any_policy", True, "a policy on each"),
    ]


async def test_a_deployed_instance_without_policies_is_not_governed() -> None:
    report = await _registry(_ungoverned()).explain(REF, criteria=GovernanceCriteria())

    assert report.governed is False
    assert report.reasons_failed() == ["any_policy: ddk-verify1298-u has no policy"]


async def test_an_undeployed_instance_is_not_governed() -> None:
    fake = _Anypoint(**{_LIST: _body("apis.by-asset.undeployed.json")})

    report = await _registry(fake).explain(REF, criteria=GovernanceCriteria())

    assert report.reasons_failed() == [
        "deployed: no instance is deployed to a gateway in environment 'Sandbox'",
        "any_policy: no deployed instance to check",
    ]
    assert not any("/policies" in path for path in fake.paths)


async def test_no_instance_fails_every_instance_check() -> None:
    fake = _Anypoint(**{_LIST: _body("apis.by-asset.none.json")})

    report = await _registry(fake).explain(REF, criteria=GovernanceCriteria())

    assert [c.passed for c in report.checks] == [False, False, False]
    assert report.checks[0].detail == "no API Manager instance in environment 'Sandbox'"


async def test_the_instance_read_filters_on_the_asset_version() -> None:
    fake = _Anypoint()

    await _registry(fake).explain(REF, criteria=GovernanceCriteria())

    assert dict(fake.requests[1].url.params) == {
        "assetId": "ddk-demo-sku-lookup",
        "groupId": ORG,
        "assetVersion": "1.1.0",
    }


async def test_required_policies_name_what_is_missing() -> None:
    criteria = GovernanceCriteria(
        required_policies=["client-id-enforcement", "rate-limiting"], require_any_policy=False
    )

    report = await _registry(_Anypoint()).explain(REF, criteria=criteria)

    assert report.checks[-1] == Check(
        "required_policies", False, "ddk-verify1298-g is missing rate-limiting"
    )


async def test_required_policies_pass_when_each_instance_has_them() -> None:
    criteria = GovernanceCriteria(required_policies=["client-id-enforcement", "mcp-support"])

    report = await _registry(_Anypoint()).explain(REF, criteria=criteria)

    assert report.governed is True
    assert report.checks[-1] == Check(
        "required_policies", True, "client-id-enforcement, mcp-support on each"
    )


async def test_a_forbidden_policy_fails() -> None:
    criteria = GovernanceCriteria(forbidden_policies=["mcp-support"])

    report = await _registry(_Anypoint()).explain(REF, criteria=criteria)

    assert report.reasons_failed() == ["forbidden_policies: ddk-verify1298-g has mcp-support"]


async def test_gateways_are_matched_by_name() -> None:
    inside = GovernanceCriteria(require_gateways=["shared-omni-gateway"])
    outside = GovernanceCriteria(require_gateways=["private-space-omni-gateway"])

    assert (await _registry(_Anypoint()).explain(REF, criteria=inside)).checks[-1] == Check(
        "gateways", True, "on shared-omni-gateway"
    )
    assert (await _registry(_Anypoint()).explain(REF, criteria=outside)).checks[-1] == Check(
        "gateways", False, "ddk-verify1298-g is on shared-omni-gateway"
    )


async def test_tags_are_the_exchange_labels_ignoring_case() -> None:
    tagged = _Anypoint(**{_EXCHANGE: _body("asset.version.tagged.json")})
    criteria = replace(_NONE, require_tags=["DDK-verify1298"])

    assert (await _registry(tagged).explain(REF, criteria=criteria)).checks == [
        Check("tags", True, "tagged DDK-verify1298")
    ]
    assert (await _registry(_Anypoint()).explain(REF, criteria=criteria)).checks == [
        Check("tags", False, "missing: DDK-verify1298")
    ]


async def test_lifecycle_is_the_exchange_status() -> None:
    published = replace(_NONE, require_lifecycle=["published"])
    deprecated = replace(_NONE, require_lifecycle=["deprecated"])

    assert (await _registry(_Anypoint()).explain(REF, criteria=published)).checks == [
        Check("lifecycle", True, "status published")
    ]
    assert (await _registry(_Anypoint()).explain(REF, criteria=deprecated)).checks == [
        Check("lifecycle", False, "status published")
    ]


async def test_a_refused_exchange_read_is_unknown() -> None:
    fake = _Anypoint(**{_EXCHANGE: {"message": "Forbidden"}})
    fake.status[_EXCHANGE] = 403
    criteria = replace(_NONE, require_tags=["a"], require_lifecycle=["published"])

    report = await _registry(fake).explain(REF, criteria=criteria)

    assert [(c.name, c.passed) for c in report.checks] == [("tags", None), ("lifecycle", None)]
    assert "HTTP 403" in report.checks[0].detail
    assert report.governed is False


async def test_a_conformant_report_passes_the_governance_check() -> None:
    """The report was captured for a throwaway asset under a throwaway profile."""
    fake = _Anypoint(**{_GOVERNANCE: _body("governance.reports.conformant.json")})

    report = await _registry(fake).explain(REF, criteria=STRICT)

    assert report.governed is True
    assert report.checks[-1] == Check(
        "governance_pass", True, "Agent Network Best Practices: Conformant"
    )
    assert dict(fake.requests[-1].url.params) == {"organization": ORG}


async def test_a_nonconformant_report_fails_the_governance_check() -> None:
    fake = _Anypoint(**{_GOVERNANCE: _report("NonConformant")})

    report = await _registry(fake).explain(REF, criteria=STRICT)

    assert report.reasons_failed() == [
        "governance_pass: Agent Network Best Practices: NonConformant"
    ]


async def test_a_pending_report_is_unknown() -> None:
    fake = _Anypoint(**{_GOVERNANCE: _report("Pending")})

    report = await _registry(fake).explain(REF, criteria=STRICT)

    assert report.checks[-1] == Check(
        "governance_pass", None, "UNKNOWN: Agent Network Best Practices: Pending"
    )


async def test_no_report_is_unknown_unless_unknown_is_allowed() -> None:
    lenient = GovernanceCriteria(require_governance_pass=True, allow_unknown=True)

    strict = await _registry(_Anypoint()).explain(REF, criteria=STRICT)
    allowed = await _registry(_Anypoint()).explain(REF, criteria=lenient)

    assert strict.checks[-1] == Check(
        "governance_pass", None, f"UNKNOWN: no governance profile has evaluated {REF}"
    )
    assert strict.governed is False
    assert allowed.governed is True


async def test_a_refused_governance_read_is_unknown() -> None:
    fake = _Anypoint(**{_GOVERNANCE: {"message": "Forbidden"}})
    fake.status[_GOVERNANCE] = 403

    report = await _registry(fake).explain(REF, criteria=STRICT)

    assert report.checks[-1].passed is None
    assert report.checks[-1].detail.startswith("UNKNOWN: Anypoint refused")


async def test_a_refused_api_manager_read_is_a_registry_error() -> None:
    fake = _Anypoint(**{_LIST: {"message": "Forbidden"}})
    fake.status[_LIST] = 403

    with pytest.raises(RegistryError, match="HTTP 403"):
        await _registry(fake).explain(REF, criteria=GovernanceCriteria())


async def test_only_the_conditions_set_are_checked_and_read() -> None:
    fake = _Anypoint()

    report = await _registry(fake).explain(REF, criteria=_NONE)

    assert report.governed is True
    assert report.checks == []
    assert fake.paths == [_ENVIRONMENTS, _LIST]


async def test_explanations_are_cached_per_criteria_until_refresh() -> None:
    fake = _Anypoint()
    registry = _registry(fake)

    first = await registry.explain(REF, criteria=GovernanceCriteria())
    assert await registry.explain(REF, criteria=GovernanceCriteria()) == first
    assert len(fake.requests) == 3

    await registry.explain(REF, criteria=STRICT)
    assert len(fake.requests) == 6

    registry.refresh()
    await registry.explain(REF, criteria=GovernanceCriteria())
    assert len(fake.requests) == 9


async def test_a_bad_ref_is_refused_before_any_read() -> None:
    fake = _Anypoint()

    with pytest.raises(ValueError, match="group_id/asset_id/version"):
        await _registry(fake).explain("not-a-ref", criteria=STRICT)
    assert fake.requests == []


async def test_explain_needs_the_control_plane_settings() -> None:
    fake = _Anypoint()
    cfg = DonkeyConfig(client_id="client", client_secret="secret")

    with pytest.raises(ConfigError):
        await _registry(fake, cfg).explain(REF, criteria=STRICT)
    assert fake.requests == []
