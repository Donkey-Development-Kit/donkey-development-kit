"""Exchange discovery + the governed-only join (BG §2.7).

:meth:`ExchangeRegistry.search` reads Exchange's GraphQL ``assets`` query
(docs/verified-apis.md §7, #53), :meth:`ExchangeRegistry.resolve_mcp` reads
API Manager and Gateway Manager (§6, #1297), and :meth:`ExchangeRegistry.explain`
reads API Manager, Exchange and API Governance (§6, #1298). Every other method that requires a real
Anypoint endpoint is gated behind a verification-blocked error (verification
discipline, working instruction #2) rather than a fabricated request path. The
N+1-avoiding index design is documented in :meth:`warm` for the implementer who
fills it in after the Verification milestone.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlsplit

from ..core import _verify
from ..core.cache import TTLCache
from ..core.config import DonkeyConfig
from ..core.errors import AuthError, RegistryError
from ..core.transport import DonkeyAsyncClient
from . import _scopes
from .criteria import Check, GovernanceCriteria, GovernanceReport, evaluate
from .models import AgentHandle, AssetRef, AssetType, McpServerHandle

__all__ = ["ExchangeRegistry"]

# docs/verified-apis.md §7 (#53): the Exchange Graph API search MuleSoft
# documents in "Searching the Graph API", plus the ``types``, ``limit`` and
# ``offset`` fields its ``SearchAsset`` input also takes.
_GRAPHQL_PATH = "/graph/api/v1/graphql"
_SEARCH_QUERY = (
    "query($query: SearchAsset) { assets(query: $query, latestVersionsOnly: true) "
    "{ groupId assetId version name type description tags { key value } } }"
)
# One page of ``assets``.
_PAGE_MAX = 250
_GLOB = re.compile(r"[*?\[]")
# API Governance's conformance stats for one asset version (docs/verified-apis.md §6, #1298).
_GOVERNANCE_TARGETS = "/governance/xapi/api/v1/stats/organization/targets"
# The SDK's discovery types → Exchange's ``type`` values. ``a2a-agent`` has no
# Exchange type of its own: an A2A agent publishes as ``agent`` (#1260).
_EXCHANGE_TYPES: dict[str, tuple[str, ...]] = {
    "mcp": ("mcp",),
    "agent": ("agent",),
    "api": ("rest-api", "http-api", "soap-api"),
}
_SDK_TYPES: dict[str, AssetType] = {
    "mcp": "mcp",
    "agent": "agent",
    "rest-api": "api",
    "http-api": "api",
    "soap-api": "api",
}


class ExchangeRegistry:
    """The Exchange client behind ``donkey.registry``: search, resolve and explain assets (BG §2.7).

    Uses the control-plane credential and caches lookups for
    ``registry_cache_ttl_s`` seconds (``DONKEY_NO_CACHE=1`` bypasses the cache).
    :meth:`search` reads Exchange, :meth:`resolve_mcp` reads API Manager, and
    :meth:`explain` joins the two with API Governance; the methods that need an
    unverified endpoint raise ``NotImplementedError`` for now (docs/verified-apis.md
    §6, §7).

    Args:
        cfg: The resolved configuration.
        http_client: The control-plane client.

    Docs: https://docs.donkey-kit.dev/tool-access/discovery
    """

    def __init__(self, cfg: DonkeyConfig, http_client: DonkeyAsyncClient) -> None:
        self._cfg = cfg
        self._http = http_client
        self._index_cache: TTLCache[object] = TTLCache(ttl_s=cfg.registry_cache_ttl_s)

    async def search(
        self,
        *,
        query: str | None = None,
        asset_types: list[AssetType] | None = None,
        tags: list[str] | None = None,
        domain: str | None = None,
        environment: str | None = None,
        governed: bool | GovernanceCriteria | None = None,
        limit: int = 50,
    ) -> list[AssetRef]:
        """Search Exchange for assets matching the given filters.

        Exchange matches a plain ``query`` itself: whole words and word prefixes
        of the name, asset ID or description, ignoring case. A ``query`` with
        ``*``, ``?`` or ``[`` is a glob over those fields, matched here, because
        Exchange does not match inside words. ``asset_types`` and ``tags`` are
        Exchange filters; an asset matches when it has every tag, ignoring case.
        A tag is one set in Exchange or at publish, not one Exchange derives
        (those have a key). Each asset comes back at its latest version.
        Results are cached for ``registry_cache_ttl_s`` seconds.

        A connected app without the ``read:exchange`` scope gets no assets, not
        an error: Exchange answers the search with an empty list (docs/scopes.md).

        Raises:
            ConfigError: The control-plane settings are missing.
            ValueError: ``limit`` is below 1.
            RegistryError: Exchange refused the query.
            AuthError: Exchange refused the query with ``401``/``403``.
            NotImplementedError: ``domain``, ``environment``, ``governed`` or the
                ``a2a-agent`` type was given; see docs/verified-apis.md §7.
        """
        cfg = self._cfg.validated(need="control_plane")
        if limit < 1:
            raise ValueError(f"limit must be at least 1, not {limit}.")
        if domain is not None:
            raise _verify.blocked(
                "search(domain=): Exchange's assets query has no domain filter, and "
                "where a domain lives on an asset is unconfirmed (docs/verified-apis.md §7)."
            )
        if governed or environment is not None:
            raise _verify.blocked(
                "search(governed=, environment=): the governed-state join over API "
                "Manager instances (the Verification milestone); see warm()."
            )
        if asset_types and "a2a-agent" in asset_types:
            raise _verify.blocked(
                "search(asset_types=['a2a-agent']): Exchange has no a2a-agent type; an "
                "A2A agent publishes as 'agent' (docs/verified-apis.md §7)."
            )
        glob = query.lower() if query and _GLOB.search(query) else None
        search: dict[str, Any] = {"organizationIds": [cfg.org_id]}
        if query and glob is None:
            search["searchTerm"] = query
        if asset_types:
            search["types"] = [t for sdk in asset_types for t in _EXCHANGE_TYPES[sdk]]
        if tags:
            search["tags"] = [{"value": tag} for tag in tags]
        wanted = {tag.lower() for tag in tags or ()}
        key = "search:" + json.dumps([search, glob, limit], sort_keys=True)
        cached = self._index_cache.get(key)
        if isinstance(cached, tuple):
            return list(cached)

        found: list[AssetRef] = []
        offset = 0
        page = min(limit, _PAGE_MAX) if glob is None else _PAGE_MAX
        while len(found) < limit:
            assets = await self._assets({**search, "limit": page, "offset": offset})
            for asset in assets:
                ref = _asset_ref(asset)
                if wanted <= {tag.lower() for tag in ref.tags} and _globbed(ref, glob):
                    found.append(ref)
            if len(assets) < page:
                break
            offset += page
        refs = tuple(found[:limit])
        self._index_cache.set(key, refs)
        return list(refs)

    async def _environment_id(self) -> str:
        """The id of the configured ``environment``, matched by name ignoring case."""
        cfg = self._cfg
        key = f"environment:{cfg.environment}"
        cached = self._index_cache.get(key)
        if isinstance(cached, str):
            return cached
        body = await self._get(
            f"/accounts/api/organizations/{cfg.org_id}/environments",
            what="the organization's environments",
            scope=_scopes.ENVIRONMENTS,
        )
        envs = [e for e in body.get("data") or () if isinstance(e, dict)]
        for env in envs:
            if str(env.get("name")).lower() == cfg.environment.lower():
                env_id = str(env.get("id"))
                self._index_cache.set(key, env_id)
                return env_id
        names = ", ".join(repr(e.get("name")) for e in envs)
        raise RegistryError(
            f"Environment {cfg.environment!r} is not in organization {cfg.org_id}; "
            f"it has {names or 'none'}."
        )

    async def _instances(self, base: str, asset: AssetRef) -> list[dict[str, Any]]:
        """The asset version's API Manager instances in the environment at ``base``."""
        listing = await self._get(
            f"{base}/apis",
            params={
                "assetId": asset.asset_id,
                "groupId": asset.group_id,
                "assetVersion": asset.version,
            },
            what=f"the API Manager instances of {asset.coordinates}",
            scope=_scopes.API_READ,
        )
        return [
            api
            for row in listing.get("assets") or ()
            for api in row.get("apis") or ()
            if isinstance(api, dict)
        ]

    async def _get(
        self, path: str, *, what: str, scope: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """``path``'s JSON body. A ``401``/``403`` is an :class:`AuthError` naming
        ``scope``, the connected-app scope the read needs (docs/scopes.md)."""
        response = await self._http.get(
            self._cfg.control_plane_url.rstrip("/") + path, params=params
        )
        if not response.is_success:
            message = (
                f"Anypoint refused the read of {what}: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )
            if response.status_code in (401, 403):
                raise _scopes.missing_scope(response, message, scope=scope, org_id=self._cfg.org_id)
            raise RegistryError(message)
        body = response.json()
        if not isinstance(body, dict):
            raise RegistryError(f"Anypoint returned an unexpected body for {what}.")
        return body

    async def _assets(self, search: dict[str, Any]) -> list[dict[str, Any]]:
        response = await self._http.post(
            self._cfg.control_plane_url.rstrip("/") + _GRAPHQL_PATH,
            json={"query": _SEARCH_QUERY, "variables": {"query": search}},
        )
        if not response.is_success:
            message = (
                f"Exchange refused the asset search: HTTP {response.status_code} "
                f"{response.text[:300]}"
            )
            if response.status_code in (401, 403):
                raise _scopes.missing_scope(
                    response, message, scope=_scopes.EXCHANGE_READ, org_id=self._cfg.org_id
                )
            raise RegistryError(message)
        body = response.json()
        errors = body.get("errors") or ()
        if errors:
            raise RegistryError(
                "Exchange asset search failed: " + "; ".join(str(e.get("message")) for e in errors)
            )
        assets = (body.get("data") or {}).get("assets") or ()
        return [a for a in assets if isinstance(a, dict)]

    async def resolve_mcp(self, ref: AssetRef | str) -> McpServerHandle:
        """Resolve an MCP server asset to its governed gateway endpoint.

        Finds the asset version's API Manager instance in the configured
        ``environment`` that is deployed to a gateway, and joins the gateway's
        public URL with the instance's path. The handle does not open a
        connection, and ``governance`` is left for ``explain()`` to fill.
        Results are cached for ``registry_cache_ttl_s`` seconds.

        Raises:
            ConfigError: The control-plane settings are missing.
            ValueError: ``ref`` is not ``group_id/asset_id/version``.
            RegistryError: The environment does not exist, the asset version has
                no deployed MCP instance there or has several, or an Anypoint
                read failed.
            AuthError: Anypoint refused a read (``401``/``403``); its
                ``remediation`` names the connected-app scope the read needs.
        """
        cfg = self._cfg.validated(need="control_plane")
        asset = AssetRef.parse(ref)
        key = f"resolve_mcp:{cfg.environment}:{asset.coordinates}"
        cached = self._index_cache.get(key)
        if isinstance(cached, McpServerHandle):
            return cached

        env_id = await self._environment_id()
        base = f"/apimanager/api/v1/organizations/{cfg.org_id}/environments/{env_id}"
        instances = [
            api
            for api in await self._instances(base, asset)
            if api.get("endpointType") == "mcp" and _deployed(api)
        ]
        where = f"environment {cfg.environment!r}"
        if not instances:
            raise RegistryError(
                f"{asset.coordinates} has no MCP instance deployed to a gateway in {where}."
            )
        if len(instances) > 1:
            labels = ", ".join(sorted(str(api.get("instanceLabel")) for api in instances))
            raise RegistryError(
                f"{asset.coordinates} has {len(instances)} deployed MCP instances in "
                f"{where} ({labels}); resolve_mcp() needs exactly one."
            )
        api = await self._get(
            f"{base}/apis/{instances[0]['id']}",
            what=f"API Manager instance {instances[0]['id']}",
            scope=_scopes.API_READ,
        )
        target = (api.get("deployment") or {}).get("targetId")
        gateway = await self._get(
            f"/gatewaymanager/api/v1/organizations/{cfg.org_id}/environments/{env_id}"
            f"/gateways/{target}",
            what=f"gateway {target}",
            scope=_scopes.GATEWAY_READ,
        )
        public = ((gateway.get("configuration") or {}).get("ingress") or {}).get("publicUrl")
        if not public:
            raise RegistryError(f"Gateway {target} has no public URL for {asset.coordinates}.")
        # The gateway routes the instance's path only with a trailing slash; without
        # one it answers an empty 404 (docs/verified-apis.md §6, #1297).
        path = urlsplit(str((api.get("endpoint") or {}).get("proxyUri") or "")).path.strip("/")
        url = str(public).rstrip("/") + "/" + (f"{path}/" if path else "")
        handle = McpServerHandle(ref=asset, endpoint_url=url)
        self._index_cache.set(key, handle)
        return handle

    async def resolve_agent(self, ref: AssetRef | str) -> AgentHandle:
        """Resolve an A2A agent asset to a handle.

        Raises:
            ValueError: ``ref`` is not ``group_id/asset_id/version``.
            NotImplementedError: Otherwise, for now; see docs/verified-apis.md §7.
        """
        AssetRef.parse(ref)
        raise _verify.blocked(
            "Exchange asset-resolution API for A2A agents (docs/verified-apis.md §7)."
        )

    async def explain(
        self, ref: AssetRef | str, *, criteria: GovernanceCriteria
    ) -> GovernanceReport:
        """Explain why an asset version is, or is not, governed in the configured ``environment``.

        Reports one :class:`Check` for each condition ``criteria`` sets. Policy and
        gateway checks judge the asset version's deployed API Manager instances, every
        one of them: a deployed instance without a required policy is an ungoverned
        route. A check whose source refused the read is ``None`` (UNKNOWN), with the
        refusal as its detail. Results are cached for ``registry_cache_ttl_s`` seconds.

        Raises:
            ConfigError: The control-plane settings are missing.
            ValueError: ``ref`` is not ``group_id/asset_id/version``.
            RegistryError: The environment does not exist, or an API Manager read failed.
            AuthError: Accounts or API Manager refused a read (``401``/``403``); its
                ``remediation`` names the connected-app scope the read needs. A
                refused Exchange or API Governance read is an UNKNOWN check instead.
        """
        cfg = self._cfg.validated(need="control_plane")
        asset = AssetRef.parse(ref)
        key = "explain:" + json.dumps(
            [cfg.environment, asset.coordinates, asdict(criteria)], sort_keys=True
        )
        cached = self._index_cache.get(key)
        if isinstance(cached, GovernanceReport):
            return cached

        env_id = await self._environment_id()
        base = f"/apimanager/api/v1/organizations/{cfg.org_id}/environments/{env_id}"
        instances = await self._instances(base, asset)
        deployed = [api for api in instances if _deployed(api)]
        where = f"environment {cfg.environment!r}"
        checks: list[Check] = []
        if criteria.require_api_instance:
            checks.append(
                Check(
                    "api_instance_exists",
                    bool(instances),
                    f"instances {_labels(instances)} in {where}"
                    if instances
                    else f"no API Manager instance in {where}",
                )
            )
        if criteria.require_deployed:
            checks.append(
                Check(
                    "deployed",
                    bool(deployed),
                    f"deployed: {_labels(deployed)}"
                    if deployed
                    else f"no instance is deployed to a gateway in {where}",
                )
            )
        if (
            criteria.require_any_policy
            or criteria.required_policies
            or criteria.forbidden_policies
            or criteria.require_gateways
        ):
            checks += await self._instance_checks(base, deployed, criteria)
        if criteria.require_tags or criteria.require_lifecycle:
            checks += await self._exchange_checks(asset, criteria)
        if criteria.require_governance_pass:
            checks.append(await self._governance_check(asset))
        report = evaluate(checks, criteria)
        self._index_cache.set(key, report)
        return report

    async def _instance_checks(
        self, base: str, deployed: list[dict[str, Any]], criteria: GovernanceCriteria
    ) -> list[Check]:
        """The policy and gateway checks over every deployed instance."""
        wants_policies = (
            criteria.require_any_policy or criteria.required_policies or criteria.forbidden_policies
        )
        policies: dict[str, set[str]] = {}
        gateways: dict[str, str] = {}
        for api in deployed:
            label = str(api.get("instanceLabel"))
            if wants_policies:
                body = await self._get(
                    f"{base}/apis/{api['id']}/policies",
                    what=f"the policies of instance {label}",
                    scope=_scopes.POLICIES_READ,
                )
                policies[label] = {
                    str((policy.get("template") or {}).get("assetId"))
                    for policy in body.get("policies") or ()
                    if isinstance(policy, dict)
                }
            if criteria.require_gateways:
                body = await self._get(
                    f"{base}/apis/{api['id']}", what=f"instance {label}", scope=_scopes.API_READ
                )
                gateways[label] = str((body.get("deployment") or {}).get("targetName"))

        checks: list[Check] = []
        if criteria.require_any_policy:
            bare: dict[str, list[str]] = {
                label: [] for label, applied in policies.items() if not applied
            }
            checks.append(
                _over_instances("any_policy", policies, bare, "has no policy", "a policy on each")
            )
        if criteria.required_policies:
            wanted = set(criteria.required_policies)
            missing = {
                label: sorted(wanted - applied)
                for label, applied in policies.items()
                if wanted - applied
            }
            checks.append(
                _over_instances(
                    "required_policies",
                    policies,
                    missing,
                    "is missing",
                    f"{', '.join(criteria.required_policies)} on each",
                )
            )
        if criteria.forbidden_policies:
            forbidden = set(criteria.forbidden_policies)
            present = {
                label: sorted(forbidden & applied)
                for label, applied in policies.items()
                if forbidden & applied
            }
            checks.append(
                _over_instances(
                    "forbidden_policies",
                    policies,
                    present,
                    "has",
                    f"none of {', '.join(criteria.forbidden_policies)}",
                )
            )
        if criteria.require_gateways:
            outside = {
                label: [name]
                for label, name in gateways.items()
                if name not in criteria.require_gateways
            }
            checks.append(
                _over_instances(
                    "gateways",
                    gateways,
                    outside,
                    "is on",
                    f"on {', '.join(sorted(set(gateways.values())))}",
                )
            )
        return checks

    async def _exchange_checks(self, asset: AssetRef, criteria: GovernanceCriteria) -> list[Check]:
        """The tag and lifecycle checks, from the Exchange asset version."""
        names = [
            name
            for name, wanted in (
                ("tags", criteria.require_tags),
                ("lifecycle", criteria.require_lifecycle),
            )
            if wanted
        ]
        try:
            body = await self._get(
                f"/exchange/api/v2/assets/{asset.group_id}/{asset.asset_id}/{asset.version}",
                what=f"Exchange asset {asset.coordinates}",
                scope=_scopes.EXCHANGE_READ,
            )
        except (AuthError, RegistryError) as exc:
            return [Check(name, None, _unknown(exc)) for name in names]
        checks: list[Check] = []
        if criteria.require_tags:
            labels = {str(label).lower() for label in body.get("labels") or ()}
            missing = [tag for tag in criteria.require_tags if tag.lower() not in labels]
            checks.append(
                Check(
                    "tags",
                    not missing,
                    f"missing: {', '.join(missing)}"
                    if missing
                    else f"tagged {', '.join(criteria.require_tags)}",
                )
            )
        if criteria.require_lifecycle:
            status = str(body.get("status"))
            checks.append(
                Check(
                    "lifecycle",
                    status in criteria.require_lifecycle,
                    f"status {status}",
                )
            )
        return checks

    async def _governance_check(self, asset: AssetRef) -> Check:
        """The ``governance_pass`` check, from API Governance's conformance reports."""
        try:
            # Without ``organization`` the read looks in the root organization and
            # finds nothing for a business group's asset.
            body = await self._get(
                f"{_GOVERNANCE_TARGETS}/{asset.group_id}/{asset.asset_id}/{asset.version}",
                params={"organization": str(self._cfg.org_id)},
                what=f"the conformance reports of {asset.coordinates}",
                scope=_scopes.EXCHANGE_READ,
            )
        except (AuthError, RegistryError) as exc:
            return Check("governance_pass", None, _unknown(exc))
        reports = [r for r in body.get("reports") or () if isinstance(r, dict)]
        if not reports:
            # An asset no profile governs, and one that does not exist, both come
            # back with no reports (docs/verified-apis.md §6, #1298).
            return Check(
                "governance_pass",
                None,
                f"UNKNOWN: no governance profile has evaluated {asset.coordinates}",
            )
        # One report per ruleset of the profiles that govern the asset's minor version.
        statuses = {
            str((r.get("ruleset") or {}).get("label")): str(r.get("summaryStatus")) for r in reports
        }
        detail = "; ".join(f"{label}: {status}" for label, status in sorted(statuses.items()))
        if "NonConformant" in statuses.values():
            return Check("governance_pass", False, detail)
        if set(statuses.values()) == {"Conformant"}:
            return Check("governance_pass", True, detail)
        # ``Pending`` (not evaluated yet) or ``Failed`` (the evaluation did not run).
        return Check("governance_pass", None, f"UNKNOWN: {detail}")

    async def warm(self, *, environment: str | None = None) -> None:
        """Build the in-memory governance index at startup.

        Design (fill in after the Verification milestone):
          1. ONE call to list all API Manager instances for (org, environment);
             index by (groupId, assetId, version) AND (groupId, assetId).
          2. Bulk policies call if one exists (verify the Verification milestone), else
             per-candidate.
          3. Apply Exchange-side filters (tags/lifecycle/type) BEFORE any API
             Manager calls to shrink the candidate set.
          4. Cache the whole index under registry_cache_ttl_s, keyed by env.
        """

        raise _verify.blocked(
            "API Manager instance-list + bulk-policy APIs for the governed-state "
            "index (the Verification milestone)."
        )

    def refresh(self) -> None:
        """Drop every cached registry lookup."""
        self._index_cache.invalidate()


def _asset_ref(asset: dict[str, Any]) -> AssetRef:
    return AssetRef(
        group_id=str(asset.get("groupId")),
        asset_id=str(asset.get("assetId")),
        version=str(asset.get("version")),
        name=asset.get("name"),
        type=_SDK_TYPES.get(str(asset.get("type"))),
        # A tag with a key is one Exchange derives, not one set on the asset.
        tags=tuple(
            str(tag.get("value"))
            for tag in asset.get("tags") or ()
            if isinstance(tag, dict) and tag.get("key") is None
        ),
        description=asset.get("description") or None,
    )


def _deployed(api: dict[str, Any]) -> bool:
    return (api.get("deployment") or {}).get("expectedStatus") == "deployed"


def _labels(apis: list[dict[str, Any]]) -> str:
    return ", ".join(sorted(str(api.get("instanceLabel")) for api in apis))


def _unknown(exc: AuthError | RegistryError) -> str:
    """An UNKNOWN check's detail: the refusal, and the missing scope if it was one."""
    if isinstance(exc, AuthError):
        return f"UNKNOWN: {exc} {exc.remediation}"
    return f"UNKNOWN: {exc}"


def _over_instances(
    name: str,
    read: dict[str, Any],
    failed: dict[str, list[str]],
    what: str,
    passed: str,
) -> Check:
    """A check over every deployed instance: ``failed`` maps a label to what failed it."""
    if not read:
        return Check(name, False, "no deployed instance to check")
    if failed:
        detail = "; ".join(
            f"{label} {what} {', '.join(found)}".rstrip() for label, found in sorted(failed.items())
        )
        return Check(name, False, detail)
    return Check(name, True, passed)


def _globbed(ref: AssetRef, glob: str | None) -> bool:
    """Whether ``ref``'s asset ID, name or description matches ``glob``, ignoring case."""
    if glob is None:
        return True
    fields = (ref.asset_id, ref.name or "", ref.description or "")
    return any(fnmatchcase(value.lower(), glob) for value in fields)
