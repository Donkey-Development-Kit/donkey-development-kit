"""``donkey expose``: put one MCP server or A2A agent behind a gateway the
platform already runs (BG §2.9.2, ADR 0015, #226).

:func:`expose` runs an :class:`~donkey_kit.registry.expose_plan.ExposePlan` as
a fixed list of steps and reports an outcome for each, so a run that stops
part-way says exactly what was and was not done. With ``dry_run`` it only
reads, which is also ``donkey preflight`` (#1279):

1. **environment** and **gateway**: the configured environment and the named
   gateway must exist and be running. They are the platform's (ADR 0015
   rule 5): a missing one is reported with where to create it, never created.
   The gateway's ingress port is the instance's listener port.
2. **registry**: the service's Exchange asset, published as ``donkey publish
   --if-changed`` would publish it. The instance names the asset, so it
   comes first.
3. **instance**: the API Manager instance, found by ``instanceLabel`` and
   created only when there is none.
4. **policy**: each declared policy that the instance does not have, inbound
   or, for an A2A instance, outbound on its upstreams.
5. **deployment**: the instance deployed to the gateway, then polled until
   the gateway applies it.

Every step is additive and idempotent (ADR 0015 rule 3): an existing instance
is never edited or moved, a policy it has is never reconfigured, and nothing
is deleted. What differs from the declaration is reported as ``differs`` and
left as it is. Every call is in docs/verified-apis.md §12.4 and §12.5; the
outbound policy and the deployment are experience APIs (``xapi``), listed with
their owner on the docs site's unsupported-boundary page.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal, TypeVar

import httpx

from ..core.config import DonkeyConfig
from ..core.errors import DonkeyError, RegistryError
from . import _scopes
from .exchange_page import PageSource
from .exchange_publish import latest_version, publish_plan
from .expose_plan import ExposePlan, PlannedPolicy
from .publish_plan import PublishPlan
from .scan import ScanResult

__all__ = ["ExposeReport", "Step", "StepStatus", "expose"]

StepStatus = Literal["present", "created", "planned", "differs", "missing", "failed", "skipped"]

# The statuses after which the run can go on.
_GOOD = frozenset({"present", "created", "planned"})
# docs/verified-apis.md §12.5: how long the gateway took to apply a deployment
# was about 10 s (A2A) and 40 s (MCP).
_POLL_S = 5.0
_DEPLOY_TIMEOUT_S = 180.0
# One page of instances; the listing a label is looked up in (#1278).
_LIST_LIMIT = 500
_APPLIED = "applied"
_T = TypeVar("_T")


@dataclass(frozen=True)
class Step:
    """One step's outcome.

    ``status`` is ``present`` (already there, left alone), ``created``,
    ``planned`` (a dry run would create it), ``differs`` (there, but not as
    declared; left alone), ``missing`` (a platform-owned resource that expose
    does not create), ``failed`` or ``skipped`` (not attempted, because an
    earlier step did not succeed). ``remediation`` says what to do about any
    status but the first three.
    """

    name: str
    status: StepStatus
    detail: str
    remediation: str | None = None


@dataclass(frozen=True)
class ExposeReport:
    """What :func:`expose` did, step by step, and where the service is reachable."""

    steps: tuple[Step, ...]
    url: str | None = None
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        """Whether every step is ``present``, ``created`` or ``planned``."""
        return all(s.status in _GOOD for s in self.steps)

    def to_dict(self) -> dict[str, Any]:
        """The report as plain JSON-ready data."""
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "url": self.url,
            "steps": [
                {
                    "name": s.name,
                    "status": s.status,
                    "detail": s.detail,
                    "remediation": s.remediation,
                }
                for s in self.steps
            ],
        }


@dataclass
class _Gateway:
    id: str
    name: str
    port: int
    version: str | None
    public_url: str | None


@dataclass
class _Instance:
    id: int
    data: dict[str, Any] = field(default_factory=dict)


async def expose(
    plan: ExposePlan,
    result: ScanResult,
    http: httpx.AsyncClient,
    *,
    cfg: DonkeyConfig,
    dry_run: bool = False,
    source: PageSource | None = None,
) -> ExposeReport:
    """Run ``plan`` against the control plane and report each step.

    ``http`` is the control-plane client; ``cfg`` names the control plane,
    the organization and the environment; ``result`` is the scan the plan
    came from, for the registry step. With ``dry_run`` nothing is written.
    Never raises for what Anypoint answers: a refused call is a ``failed``
    step whose remediation names the connected-app scope it needs.

    Raises:
        ValueError: The plan has issues.
    """
    if plan.issues or plan.asset is None:
        raise ValueError("a plan with issues is not exposed; fix them first")
    run = _Run(plan, result, http, cfg, dry_run=dry_run, source=source)
    await run.go()
    return ExposeReport(tuple(run.steps), url=run.url, dry_run=dry_run)


class _Run:
    def __init__(
        self,
        plan: ExposePlan,
        result: ScanResult,
        http: httpx.AsyncClient,
        cfg: DonkeyConfig,
        *,
        dry_run: bool,
        source: PageSource | None,
    ) -> None:
        assert plan.asset is not None and plan.label is not None
        self.plan, self.asset, self.label = plan, plan.asset, plan.label
        self.result, self.http, self.cfg = result, http, cfg
        self.dry_run, self.source = dry_run, source
        self.base = cfg.control_plane_url.rstrip("/")
        self.steps: list[Step] = []
        self.url: str | None = None
        self.env_id = ""
        self.environment_name = ""
        self._policies: dict[tuple[str, str], str] | None = None

    @property
    def apim(self) -> str:
        return f"/apimanager/api/v1/organizations/{self.cfg.org_id}/environments/{self.env_id}"

    def add(
        self, name: str, status: StepStatus, detail: str, remediation: str | None = None
    ) -> bool:
        self.steps.append(Step(name, status, detail, remediation))
        return status in _GOOD

    def skip(self, *names: str) -> None:
        for name in names:
            self.add(name, "skipped", "not attempted: an earlier step did not succeed")

    def policy_names(self) -> list[str]:
        return [f"policy {p.asset.asset_id}" for p in self.plan.policies]

    async def go(self) -> None:
        later = ["registry", "instance", *self.policy_names(), "deployment"]
        gateway = None
        if await self.step("environment", self.environment):
            gateway = await self.step("gateway", self.gateway)
        if gateway is None:
            self.skip(*([] if self.env_id else ["gateway"]), *later)
            return
        assert isinstance(gateway, _Gateway)
        if gateway.public_url:
            # The gateway routes an instance's path only with a trailing slash (#1297).
            self.url = f"{gateway.public_url.rstrip('/')}/{self.label}/"
        if not await self.step("registry", self.registry):
            self.skip(*later[1:])
            return
        instance = await self.step("instance", lambda: self.instance(gateway))
        if instance is None:
            self.skip(*later[2:])
            return
        ok = True
        for policy in self.plan.policies:
            name = f"policy {policy.asset.asset_id}"
            done = await self.step(name, partial(self.policy, instance, policy))
            ok = ok and done is not None
        if not ok:
            self.skip("deployment")
            return
        await self.step("deployment", lambda: self.deploy(instance, gateway))

    async def step(self, name: str, call: Callable[[], Awaitable[_T]]) -> _T | None:
        """Run one step; its value, or ``None`` when it did not succeed. A
        refused call becomes a ``failed`` step rather than an exception."""
        try:
            value = await call()
        except DonkeyError as exc:
            self.add(name, "failed", str(exc), exc.remediation)
            return None
        last = self.steps[-1] if self.steps else None
        # ``differs`` goes on: what is there is left alone, and the rest is still added.
        if last is None or last.name != name or last.status not in _GOOD | {"differs"}:
            return None
        return value

    # -- the platform's resources (read only) --------------------------------

    async def environment(self) -> bool:
        cfg = self.cfg
        body = await self.read(
            f"/accounts/api/organizations/{cfg.org_id}/environments",
            "the organization's environments",
            _scopes.ENVIRONMENTS,
        )
        for env in body.get("data") or ():
            if isinstance(env, dict) and str(env.get("name")).lower() == cfg.environment.lower():
                self.env_id = str(env.get("id"))
                self.environment_name = str(env.get("name"))
                return self.add(
                    "environment", "present", f"{self.environment_name} ({self.env_id})"
                )
        return self.add(
            "environment",
            "missing",
            f"organization {cfg.org_id} has no environment {cfg.environment!r}",
            "Pick an existing one with --env or ANYPOINT_ENV, or create it in Access "
            "Management > Environments; expose never creates one.",
        )

    async def gateway(self) -> _Gateway | None:
        target = self.plan.target
        assert target is not None
        gateways = (
            f"/gatewaymanager/api/v1/organizations/{self.cfg.org_id}"
            f"/environments/{self.env_id}/gateways"
        )
        # The name filter matches substrings, so the exact name is matched here.
        listing = await self.read(gateways, "the gateways", _scopes.GATEWAY_READ, {"name": target})
        found = [
            g
            for g in listing.get("content") or ()
            if isinstance(g, dict) and g.get("name") == target
        ]
        if not found:
            self.add(
                "gateway",
                "missing",
                f"environment {self.environment_name} has no gateway named {target!r}",
                "Create it in Runtime Manager > Flex Gateways (or with your platform's IaC), "
                "or name an existing one with --target; expose never creates a gateway.",
            )
            return None
        data = await self.read(
            f"{gateways}/{found[0]['id']}", f"gateway {target}", _scopes.GATEWAY_READ
        )
        status = data.get("status")
        port = ((data.get("portConfiguration") or {}).get("ingress") or {}).get("port")
        if status != "APPLIED":
            self.add(
                "gateway",
                "failed",
                f"gateway {target} is {status}, not APPLIED",
                "Wait until Runtime Manager shows it running, then re-run.",
            )
            return None
        if not isinstance(port, int):
            self.add("gateway", "failed", f"gateway {target} reports no ingress port")
            return None
        public = ((data.get("configuration") or {}).get("ingress") or {}).get("publicUrl")
        self.add("gateway", "present", f"{target}, ingress port {port}")
        return _Gateway(
            id=str(data.get("id") or found[0]["id"]),
            name=target,
            port=port,
            version=data.get("runtimeVersion"),
            public_url=str(public) if public else None,
        )

    # -- what expose creates -------------------------------------------------

    async def registry(self) -> bool:
        asset = self.asset
        assert asset.group_id is not None and asset.version is not None
        gav = f"{asset.group_id}:{asset.asset_id}:{asset.version}"
        org_id = self.cfg.org_id
        assert org_id is not None
        if self.dry_run:
            latest = await latest_version(
                self.http, asset.group_id, asset.asset_id, base_url=self.base
            )
            if latest is not None and latest.digest == asset.digest:
                return self.add(
                    "registry", "present", f"{asset.asset_id} {latest.version} has this content"
                )
            if latest is not None and asset.version in latest.versions:
                return self.add(
                    "registry",
                    "failed",
                    f"{gav} is already published with other content",
                    "Bump the version in [donkey.publication] and re-run.",
                )
            return self.add("registry", "planned", f"would publish {gav}")
        (outcome,) = await publish_plan(
            PublishPlan(assets=(asset,)),
            self.result,
            self.http,
            base_url=self.base,
            org_id=org_id,
            source=self.source,
        )
        if outcome.error is not None:
            return self.add("registry", "failed", outcome.detail, outcome.error.remediation)
        status: StepStatus = "created" if outcome.status == "published" else "present"
        return self.add("registry", status, f"{asset.asset_id}: {outcome.detail}")

    async def instance(self, gateway: _Gateway) -> _Instance | Literal["planned"] | None:
        asset, label = self.asset, self.label
        found = await self.find_instance()
        if found is not None:
            return await self.existing_instance(found)
        if self.dry_run:
            self.add("instance", "planned", f"would create {label} on {gateway.name}")
            return "planned"
        body = {
            "spec": {
                "groupId": asset.group_id,
                "assetId": asset.asset_id,
                "version": asset.version,
            },
            "endpoint": {
                "type": self.plan.endpoint_type,
                "uri": self.plan.upstream,
                "proxyUri": f"http://0.0.0.0:{gateway.port}/{label}",
                "isCloudHub": None,
                "deploymentType": "HY",
            },
            "instanceLabel": label,
            "technology": "flexGateway",
        }
        response = await self.http.post(self.base + f"{self.apim}/apis", json=body)
        if response.status_code == 409:
            # Created since the listing, e.g. by a concurrent run.
            found = await self.find_instance()
            if found is not None:
                return await self.existing_instance(found)
        if response.status_code == 400 and "retrieve asset" in response.text:
            # What a manage scope without read:exchange gets (docs/scopes.md).
            raise _scopes.missing_scope(
                response,
                f"API Manager could not read {asset.asset_id} to create the instance: "
                f"HTTP 400 {response.text[:300]}",
                scope=_scopes.EXCHANGE_READ,
                org_id=self.cfg.org_id,
            )
        if response.status_code == 400 and "must have an A2A protocol" in response.text:
            # An agent published before #226 is stored with protocol a2a_v1, which
            # API Manager will not front (docs/verified-apis.md §12.5).
            raise RegistryError(
                f"API Manager will not front {asset.asset_id} {asset.version}: Exchange has "
                f"it with an agent protocol API Manager does not accept "
                f"(HTTP 400 {response.text[:300]})",
                remediation=(
                    "Bump the version in [donkey.publication] and re-run donkey expose: "
                    "the new version is published with the protocol API Manager accepts."
                ),
            )
        _check(response, f"creating instance {label}", _scopes.API_WRITE, self.cfg.org_id)
        created = _Instance(int(response.json()["id"]), response.json())
        self.add("instance", "created", f"{label} ({created.id}), upstream {self.plan.upstream}")
        return created

    async def find_instance(self) -> dict[str, Any] | None:
        listing = await self.read(
            f"{self.apim}/apis",
            "the API Manager instances",
            _scopes.API_READ,
            {"limit": str(_LIST_LIMIT)},
        )
        rows = [
            api
            for a in listing.get("assets") or ()
            for api in a.get("apis") or ()
            if isinstance(api, dict)
        ]
        total = listing.get("total")
        if isinstance(total, int) and total > len(rows):
            raise RegistryError(
                f"the environment has {total} instances and one read returns {len(rows)}, so "
                f"expose cannot tell whether {self.label!r} is taken",
                remediation=(
                    "Check the label in API Manager, and report this so paging can be added."
                ),
            )
        mine = [r for r in rows if r.get("instanceLabel") == self.label]
        theirs = [
            r
            for r in mine
            if (r.get("groupId"), r.get("assetId")) != (self.asset.group_id, self.asset.asset_id)
        ]
        if theirs:
            other = theirs[0]
            raise RegistryError(
                f"label {self.label!r} is already instance {other.get('id')} of "
                f"{other.get('groupId')}:{other.get('assetId')}",
                remediation="Set another label in the service's [donkey.expose.services] table.",
            )
        return mine[0] if mine else None

    async def existing_instance(self, row: dict[str, Any]) -> _Instance:
        api = _Instance(int(row["id"]))
        api.data = await self.read(
            f"{self.apim}/apis/{api.id}", f"instance {api.id}", _scopes.API_READ
        )
        notes = []
        if row.get("assetVersion") != self.asset.version:
            notes.append(f"asset version {row.get('assetVersion')}, not {self.asset.version}")
        uri = (api.data.get("endpoint") or {}).get("uri")
        if uri != self.plan.upstream:
            notes.append(f"upstream {uri}, not {self.plan.upstream}")
        if notes:
            self.add(
                "instance",
                "differs",
                f"{self.label} ({api.id}) has {'; '.join(notes)}; expose does not change it",
                "Change the instance in API Manager, or match the declaration to it.",
            )
        else:
            self.add("instance", "present", f"{self.label} ({api.id})")
        return api

    async def existing_policies(self, instance: _Instance) -> dict[tuple[str, str], str]:
        """The instance's policies, ``(group, asset) -> version``, read once."""
        if self._policies is not None:
            return self._policies
        body = await self.read(
            f"{self.apim}/apis/{instance.id}/policies",
            f"the policies of {instance.id}",
            _scopes.POLICIES_READ,
        )
        rows = body.get("policies") if isinstance(body, dict) else None
        existing: dict[tuple[str, str], str] = {}
        for row in rows or ():
            template = row.get("template") or {}
            existing[(str(template.get("groupId")), str(template.get("assetId")))] = str(
                template.get("assetVersion")
            )
        self._policies = existing
        return existing

    async def policy(
        self,
        instance: _Instance | Literal["planned"],
        policy: PlannedPolicy,
    ) -> bool:
        ref, name = policy.asset, f"policy {policy.asset.asset_id}"
        kind = "outbound" if policy.outbound else "inbound"
        existing = await self.existing_policies(instance) if isinstance(instance, _Instance) else {}
        have = existing.get((ref.group_id, ref.asset_id))
        if have is not None:
            if have != ref.version:
                return self.add(
                    name,
                    "differs",
                    f"{ref.asset_id} {have} is applied, not {ref.version}; "
                    "expose does not change it",
                    "Change the policy in API Manager, or match the declaration to it.",
                )
            return self.add(name, "present", f"{ref.asset_id} {have}")
        if self.dry_run or instance == "planned":
            return self.add(name, "planned", f"would apply {kind} {ref.asset_id} {ref.version}")
        assert isinstance(instance, _Instance)
        if policy.outbound:
            upstreams = await self.read(
                f"{self.apim}/apis/{instance.id}/upstreams",
                f"the upstreams of {instance.id}",
                _scopes.API_READ,
            )
            ids = [str(u["id"]) for u in upstreams.get("upstreams") or () if u.get("id")]
            path = (
                f"/apimanager/xapi/v1/organizations/{self.cfg.org_id}/environments/{self.env_id}"
                f"/apis/{instance.id}/policies/outbound-policies"
            )
            body: dict[str, Any] = {
                "configurationData": policy.configuration,
                "groupId": ref.group_id,
                "assetId": ref.asset_id,
                "assetVersion": ref.version,
                "upstreamIds": ids,
                "apiVersionId": instance.id,
            }
        else:
            path = f"{self.apim}/apis/{instance.id}/policies"
            body = {
                "groupId": ref.group_id,
                "assetId": ref.asset_id,
                "assetVersion": ref.version,
                "configurationData": policy.configuration,
                "pointcutData": None,
            }
        response = await self.http.post(self.base + path, json=body)
        _check(
            response,
            f"applying {kind} policy {ref.asset_id}",
            _scopes.POLICIES_WRITE,
            self.cfg.org_id,
        )
        return self.add(name, "created", f"{kind} {ref.asset_id} {ref.version}")

    async def deploy(self, instance: _Instance | Literal["planned"], gateway: _Gateway) -> bool:
        if instance == "planned":
            return self.add("deployment", "planned", f"would deploy to {gateway.name}")
        assert isinstance(instance, _Instance)
        deployment = instance.data.get("deployment") or {}
        proxies = (
            f"/proxies/xapi/v1/organizations/{self.cfg.org_id}"
            f"/environments/{self.env_id}/apis/{instance.id}"
        )
        if deployment.get("expectedStatus") == "deployed":
            if deployment.get("targetId") != gateway.id:
                return self.add(
                    "deployment",
                    "differs",
                    f"{self.label} is deployed to {deployment.get('targetName')}, not "
                    f"{gateway.name}; expose does not move it",
                    "Move it in API Manager, or set target to the gateway it is on.",
                )
            if self.dry_run:
                return self.add("deployment", "present", f"deployed to {gateway.name}")
            return await self.applied(proxies, str(deployment.get("id")), gateway, "present")
        if self.dry_run:
            return self.add("deployment", "planned", f"would deploy to {gateway.name}")
        response = await self.http.post(
            self.base + f"{proxies}/deployments",
            json={
                "type": "HY",
                "gatewayVersion": gateway.version,
                "targetId": gateway.id,
                "targetName": gateway.name,
                "environmentId": self.env_id,
                "environmentName": self.environment_name,
            },
        )
        _check(
            response, f"deploying {self.label} to {gateway.name}", _scopes.DEPLOY, self.cfg.org_id
        )
        return await self.applied(proxies, str(response.json()["id"]), gateway, "created")

    async def applied(
        self, proxies: str, deployment_id: str, gateway: _Gateway, done: StepStatus
    ) -> bool:
        """Poll the deployment until the gateway applies it, or give up."""
        deadline = time.monotonic() + _DEPLOY_TIMEOUT_S
        status = None
        while True:
            body = await self.read(
                f"{proxies}/deployments/{deployment_id}/status",
                "the deployment status",
                _scopes.DEPLOY,
            )
            status = body.get("status")
            if status == _APPLIED:
                return self.add("deployment", done, f"applied on {gateway.name}")
            if time.monotonic() >= deadline:
                return self.add(
                    "deployment",
                    "failed",
                    f"deployed to {gateway.name}, but still {status} "
                    f"after {_DEPLOY_TIMEOUT_S:.0f} s",
                    "Re-run donkey expose to check it again, "
                    "or look at the gateway in Runtime Manager.",
                )
            await asyncio.sleep(_POLL_S)

    async def read(
        self, path: str, what: str, scope: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = await self.http.get(self.base + path, params=params)
        _check(response, f"the read of {what}", scope, self.cfg.org_id)
        body = response.json()
        if not isinstance(body, dict):
            raise RegistryError(f"Anypoint returned an unexpected body for {what}.")
        return body


def _check(response: httpx.Response, action: str, scope: str, org_id: str | None) -> None:
    """Raise unless ``response`` succeeded; a ``401``/``403`` names ``scope``."""
    if response.is_success:
        return
    message = f"Anypoint refused {action}: HTTP {response.status_code} {response.text[:300]}"
    if response.status_code in (401, 403):
        raise _scopes.missing_scope(response, message, scope=scope, org_id=org_id)
    raise RegistryError(
        message, remediation="Fix what Anypoint reported, then re-run donkey expose."
    )
