"""Fixture-driven validation of the governed-state model against REAL Anypoint
API Manager data (captured 2026-08-28 from the sandbox; see
tests/fixtures/anypoint/README.md).

Proves that `AssetRef` / `McpServerHandle` / the governance `Check`+`evaluate`
logic faithfully represent the direct Anypoint control-plane contract — the
join `ExchangeRegistry` will perform once its (still-blocked) live fetch path is
wired (docs/verified-apis.md §12). The raw→domain mapping lives here in the test,
not in the SDK, keeping the verification discipline: shapes are verified, the live fetch
endpoint is not yet.
"""

from __future__ import annotations

import json
from pathlib import Path

from donkey_kit.registry.governance import STRICT, Check, GovernanceCriteria, evaluate
from donkey_kit.registry.models import AssetRef, McpServerHandle

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text())


def test_api_manager_record_maps_to_asset_and_handle() -> None:
    instances = _load("api_list.sandbox.json")
    assert isinstance(instances, list) and instances
    rec = instances[0]

    ref = AssetRef(
        group_id=rec["groupId"],
        asset_id=rec["assetId"],
        version=rec["assetVersion"],
        name=rec["instanceLabel"],
        type="mcp",
    )
    handle = McpServerHandle(
        ref=ref,
        endpoint_url=rec["endpointUri"],
        transport="streamable_http",
        auth_required=True,  # client-id-enforcement policy is applied (see below)
    )

    assert ref.coordinates == f"{rec['groupId']}/{rec['assetId']}/{rec['assetVersion']}"
    assert rec["technology"] == "flexGateway"
    # Governed MCP servers sit behind the Agent Network ingress gateway.
    assert "/mcp/" in handle.endpoint_url
    assert handle.endpoint_url.startswith("https://agent-network-ingress-gw")


def _enabled_policy_ids(policy_rows: list[dict]) -> set[str]:
    return {p["Asset ID"] for p in policy_rows if p.get("Status") == "Enabled"}


def test_policies_drive_governed_verdict() -> None:
    policy_rows = _load("policy_list.product-catalog-mcp.json")
    describe = _load("api_describe.product-catalog-mcp.json")
    enabled = _enabled_policy_ids(policy_rows)

    # The real instance carries exactly the enforcement policy STRICT requires.
    assert "client-id-enforcement" in enabled
    assert "mcp-support" in enabled  # MCP-specific gateway policy

    checks = [
        Check("api_instance", describe.get("id") is not None, "API Manager instance exists"),
        Check("deployed", bool(describe.get("endpointUri")), "has a gateway endpoint"),
        Check(
            "required_policies",
            all(p in enabled for p in STRICT.required_policies),
            f"required {STRICT.required_policies}; enabled {sorted(enabled)}",
        ),
    ]
    # Use STRICT minus the ruleset-pass check (ruleset result-read shape is still
    # UNVERIFIED, docs/verified-apis.md §6); everything else is real and should pass.
    criteria = GovernanceCriteria(
        require_governance_pass=False,
        required_policies=STRICT.required_policies,
        allow_unknown=False,
    )
    report = evaluate(checks, criteria)

    assert report.governed is True
    assert report.reasons_failed() == []


def test_missing_required_policy_fails_and_explains() -> None:
    # Same instance but demand a policy it does NOT have → not governed, with a
    # retained reason (the "filtered vs broken credential" distinction, the governed-state check
    # API).
    checks = [Check("required_policies", False, "rate-limiting not applied")]
    report = evaluate(checks, GovernanceCriteria(allow_unknown=False))
    assert report.governed is False
    assert report.reasons_failed() == ["required_policies: rate-limiting not applied"]
