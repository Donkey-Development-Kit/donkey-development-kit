"""A stateful fake control plane for ``donkey expose`` tests (#226).

It answers what :mod:`donkey_kit.registry.expose` calls: the environments
(``tests/fixtures/anypoint/mcp_resolve/``), the gateway by name
(``gateway/``), and the API Manager instances, policies, upstreams and
deployments it creates, replaying the bodies captured in ``mcp_instance/`` and
``a2a_instance/`` with the ids and fields of what was created. Exchange and
GraphQL calls go to a :class:`~_fake_exchange.FakeExchange`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from _fake_exchange import ORG, FakeExchange

ANYPOINT = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint"
ENV = "00000000-0000-4000-8000-5c4fd1a49fc3"  # Sandbox, in mcp_resolve/environments.json
GATEWAY = "shared-omni-gateway"
GATEWAY_ID = "00000000-0000-4000-8000-d4b0d8dad526"
APIM = f"/apimanager/api/v1/organizations/{ORG}/environments/{ENV}"
_GATEWAYS = f"/gatewaymanager/api/v1/organizations/{ORG}/environments/{ENV}/gateways"
_PROXIES = f"/proxies/xapi/v1/organizations/{ORG}/environments/{ENV}/apis"
_OUTBOUND = f"/apimanager/xapi/v1/organizations/{ORG}/environments/{ENV}/apis"


def body(name: str) -> Any:
    return json.loads((ANYPOINT / name).read_text(encoding="utf-8"))


@dataclass
class FakeAnypoint:
    """The environment's gateways (by name; each but the captured one gets a
    made-up id), its instances (``id -> describe body``) and their policies
    (``id -> rows``), and the statuses a deployment reports, polled in turn
    (the last repeats). ``refuse`` answers a call, by the name
    :meth:`route` gives it, with a canned response instead. ``requests``
    records every call, Exchange's too."""

    exchange: FakeExchange = field(default_factory=FakeExchange)
    gateways: list[str] = field(default_factory=lambda: [GATEWAY])
    gateway_status: str = "APPLIED"
    apis: dict[int, dict[str, Any]] = field(default_factory=dict)
    policies: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    statuses: list[str] = field(default_factory=lambda: ["applying", "applied"])
    refuse: dict[str, httpx.Response] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    _next_id: int = 21259411
    _polls: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: self.handle(request))

    def writes(self) -> list[str]:
        """Every write call, as ``METHOD path``: the GraphQL query is a read."""
        return [
            f"{r.method} {r.url.path}"
            for r in self.requests
            if r.method != "GET" and r.url.path != "/graph/api/v2/graphql"
        ]

    def add_instance(
        self,
        label: str,
        asset_id: str,
        *,
        group_id: str = ORG,
        version: str = "1.0.0",
        uri: str = "https://sku.example.invalid/mcp",
        endpoint_type: str = "mcp",
        target: str | None = None,
    ) -> int:
        """An instance that is already there, deployed to ``target`` when given."""
        api_id = self._next_id
        self._next_id += 1
        self.apis[api_id] = {
            "id": api_id,
            "instanceLabel": label,
            "groupId": group_id,
            "assetId": asset_id,
            "assetVersion": version,
            "endpoint": {"type": endpoint_type, "uri": uri},
            "deployment": None,
        }
        self.policies[api_id] = []
        if target is not None:
            self._mark_deployed(api_id, self._gateway_id(target), target)
        return api_id

    def add_policy(self, api_id: int, group_id: str, asset_id: str, version: str) -> None:
        self.policies[api_id].append(
            {"template": {"groupId": group_id, "assetId": asset_id, "assetVersion": version}}
        )

    def route(self, request: httpx.Request) -> str | None:
        """The name of the call, or ``None`` when it is not this fake's."""
        path, method = request.url.path, request.method
        if path == f"/accounts/api/organizations/{ORG}/environments":
            return "environments"
        if path == _GATEWAYS:
            return "gateways"
        if path.startswith(_GATEWAYS + "/"):
            return "gateway"
        if path == f"{APIM}/apis":
            return "create" if method == "POST" else "list"
        if re.fullmatch(rf"{APIM}/apis/\d+", path):
            return "describe"
        if re.fullmatch(rf"{APIM}/apis/\d+/policies", path):
            return "inbound" if method == "POST" else "policies"
        if re.fullmatch(rf"{APIM}/apis/\d+/upstreams", path):
            return "upstreams"
        if re.fullmatch(rf"{_OUTBOUND}/\d+/policies/outbound-policies", path):
            return "outbound"
        if re.fullmatch(rf"{_PROXIES}/\d+/deployments", path):
            return "deploy"
        if re.fullmatch(rf"{_PROXIES}/\d+/deployments/\d+/status", path):
            return "status"
        return None

    def handle(self, request: httpx.Request) -> httpx.Response:
        name = self.route(request)
        if name is None:
            response = self.exchange.handle(request)
            self.requests.append(request)
            return response
        self.requests.append(request)
        if name in self.refuse:
            return self.refuse[name]
        ids = [int(n) for n in re.findall(r"/apis/(\d+)", request.url.path)]
        sent = json.loads(request.content) if request.content else None
        return getattr(self, "_" + name)(request, ids[0] if ids else None, sent)

    # -- the platform's -------------------------------------------------------

    def _environments(self, request: httpx.Request, *_: Any) -> httpx.Response:
        return httpx.Response(200, json=body("mcp_resolve/environments.json"))

    def _gateway_id(self, name: str) -> str:
        return GATEWAY_ID if name == GATEWAY else f"00000000-0000-4000-8000-{len(name):012d}"

    def _gateways(self, request: httpx.Request, *_: Any) -> httpx.Response:
        listing = body("gateway/gateways.by-name.json")
        (row,) = listing["content"]
        # As Gateway Manager does: ``name`` matches a substring.
        wanted = request.url.params.get("name", "")
        content = [
            {**row, "id": self._gateway_id(n), "name": n} for n in self.gateways if wanted in n
        ]
        return httpx.Response(200, json={**listing, "content": content})

    def _gateway(self, request: httpx.Request, *_: Any) -> httpx.Response:
        gateway_id = request.url.path.rsplit("/", 1)[1]
        for name in self.gateways:
            if self._gateway_id(name) == gateway_id:
                data = body("gateway/gateway.200.json")
                return httpx.Response(
                    200,
                    json={**data, "id": gateway_id, "name": name, "status": self.gateway_status},
                )
        return httpx.Response(404, json={"message": "gateway not found"})

    # -- API Manager ----------------------------------------------------------

    def _list(self, *_: Any) -> httpx.Response:
        rows = [
            {
                "id": a["id"],
                "instanceLabel": a["instanceLabel"],
                "groupId": a["groupId"],
                "assetId": a["assetId"],
                "assetVersion": a["assetVersion"],
                "endpointType": a["endpoint"]["type"],
                "deployment": a["deployment"],
            }
            for a in self.apis.values()
        ]
        return httpx.Response(200, json={"total": len(rows), "assets": [{"apis": rows}]})

    def _create(self, request: httpx.Request, _: Any, sent: dict[str, Any]) -> httpx.Response:
        label = sent["instanceLabel"]
        if any(a["instanceLabel"] == label for a in self.apis.values()):
            return httpx.Response(409, json=body("mcp_instance/create.409.json"))
        spec, endpoint = sent["spec"], sent["endpoint"]
        api_id = self.add_instance(
            label,
            spec["assetId"],
            group_id=spec["groupId"],
            version=spec["version"],
            uri=endpoint["uri"],
            endpoint_type=endpoint["type"],
        )
        self.apis[api_id]["endpoint"]["proxyUri"] = endpoint["proxyUri"]
        created = body(f"{endpoint['type']}_instance/create.201.json")
        created.update(
            id=api_id,
            instanceLabel=label,
            groupId=spec["groupId"],
            assetId=spec["assetId"],
            assetVersion=spec["version"],
        )
        created["endpoint"].update(endpoint)
        return httpx.Response(201, json=created)

    def _describe(self, _: Any, api_id: int, *__: Any) -> httpx.Response:
        if api_id not in self.apis:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json=self.apis[api_id])

    def _policies(self, _: Any, api_id: int, *__: Any) -> httpx.Response:
        return httpx.Response(200, json={"policies": self.policies[api_id]})

    def _inbound(self, _: Any, api_id: int, sent: dict[str, Any]) -> httpx.Response:
        self.add_policy(api_id, sent["groupId"], sent["assetId"], sent["assetVersion"])
        return httpx.Response(201, json=body("mcp_instance/policy-mcp-support.201.json"))

    def _upstreams(self, _: Any, api_id: int, *__: Any) -> httpx.Response:
        uri = self.apis[api_id]["endpoint"]["uri"]
        return httpx.Response(
            200, json={"total": 1, "upstreams": [{"id": f"upstream-{api_id}", "uri": uri}]}
        )

    def _outbound(self, _: Any, api_id: int, sent: dict[str, Any]) -> httpx.Response:
        assert sent["upstreamIds"] == [f"upstream-{api_id}"]
        self.add_policy(api_id, sent["groupId"], sent["assetId"], sent["assetVersion"])
        return httpx.Response(201, json=body("a2a_instance/outbound-policy.201.json"))

    # -- the deployment -------------------------------------------------------

    def _mark_deployed(self, api_id: int, target_id: str, target_name: str) -> int:
        deployment_id = api_id + 7223101
        self.apis[api_id]["deployment"] = {
            "id": deployment_id,
            "targetId": target_id,
            "targetName": target_name,
            "expectedStatus": "deployed",
        }
        return deployment_id

    def _deploy(self, _: Any, api_id: int, sent: dict[str, Any]) -> httpx.Response:
        deployment_id = self._mark_deployed(api_id, sent["targetId"], sent["targetName"])
        created = body("mcp_instance/deployment.201.json")
        created.update(id=deployment_id, apiId=api_id, environmentApiId=api_id)
        return httpx.Response(201, json=created)

    def _status(self, *_: Any) -> httpx.Response:
        status = self.statuses[min(self._polls, len(self.statuses) - 1)]
        self._polls += 1
        return httpx.Response(200, json={"status": status, "lastActiveDate": None})
