"""``donkey publish``: register a plan's MCP servers in Exchange (BG §2.5, #218).

:func:`publish_plan` sends each ``mcp`` asset of a
:class:`~donkey_kit.registry.publish_plan.PublishPlan` with the Exchange
multipart publish, over the control-plane client. Every call below is in the
ledger (docs/verified-apis.md §7, §12.3) with the fixtures it was captured as:

* the latest version is the GraphQL ``asset`` query without a version; an
  asset Exchange does not have comes back as a ``200`` whose error carries
  ``extensions.status`` ``404``;
* the digest is published as the ``properties.ddkDigest`` field. Exchange keeps
  a property only in the asset's generated POM, so the digest is read back from
  the POM's ``<properties>``, downloaded from the pre-signed ``externalLink``
  GraphQL returns. The control-plane client sends no credential to that host;
* a version that already exists is a ``409`` and the published asset is left
  as it was, so a version is never overwritten;
* an endpoint is registered as an external instance of the version group,
  once per endpoint URI.

With ``if_changed`` an asset whose latest published digest equals the planned
one is left alone, so a merge that changes nothing publishes nothing. A planned
version that is already published with other content fails before anything is
sent: bump the version. Agent assets are not published yet: the scan does not
capture the protocol and interface an agent asset's metadata needs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from ..core.errors import RegistryError
from .publish_plan import PlannedAsset, PublishPlan
from .scan import ScanResult

__all__ = [
    "DIGEST_PROPERTY",
    "PublishOutcome",
    "PublishedVersion",
    "latest_version",
    "mcp_metadata",
    "publish_plan",
]

# docs/verified-apis.md §12.3.
_PUBLISH_PATH = "/exchange/api/v2/organizations/{org}/assets/{group}/{asset}/{version}"
_GRAPHQL_PATH = "/graph/api/v2/graphql"
_INSTANCE_PATH = (
    "/exchange/api/v2/assets/{group}/{asset}/versionGroups/{group_name}/instances/external"
)

#: The ``properties.*`` key the digest is published under; Exchange writes it to
#: the generated POM as ``<ddkDigest>`` (docs/verified-apis.md §7).
DIGEST_PROPERTY = "ddkDigest"

_LATEST_QUERY = (
    "query($groupId: String!, $assetId: String!) { asset(groupId: $groupId, assetId: $assetId) "
    "{ version files { classifier packaging externalLink } otherVersions { version } } }"
)
_VERSION_QUERY = (
    "query($groupId: String!, $assetId: String!, $version: String!) "
    "{ asset(groupId: $groupId, assetId: $assetId, version: $version) "
    "{ versionGroup instances { endpointUri } } }"
)
_POM_DIGEST = re.compile(rf"<{DIGEST_PROPERTY}>\s*(sha256:[0-9a-f]{{64}})\s*</{DIGEST_PROPERTY}>")

# The scan's transport names → mcp-metadata ``transport.kind``. An undeclared
# transport is Exchange's own default, streamable HTTP.
_TRANSPORT_KINDS = {None: "streamableHttp", "streamable-http": "streamableHttp", "sse": "sse"}
# Bare annotations with an obvious JSON Schema type; anything else is left open.
_SCHEMA_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


@dataclass(frozen=True)
class PublishedVersion:
    """The latest published version of an asset, as Exchange reports it.

    ``digest`` is ``None`` when that version was not published with one, for
    example by hand. ``versions`` holds every published version, this one
    included.
    """

    version: str
    digest: str | None
    versions: tuple[str, ...]


@dataclass(frozen=True)
class PublishOutcome:
    """What :func:`publish_plan` did with one planned asset.

    ``status`` is ``published`` (a new version was created), ``unchanged`` (the
    latest version already has this digest) or ``skipped`` (not publishable
    yet; ``detail`` says why).
    """

    asset: PlannedAsset
    status: Literal["published", "unchanged", "skipped"]
    detail: str


async def publish_plan(
    plan: PublishPlan,
    result: ScanResult,
    http: httpx.AsyncClient,
    *,
    base_url: str,
    org_id: str,
    if_changed: bool = True,
) -> tuple[PublishOutcome, ...]:
    """Publish ``plan``'s MCP assets to Exchange, one at a time, in plan order.

    ``http`` is the control-plane client and ``base_url`` the control plane;
    ``result`` is the scan the plan came from. A plan with issues is refused.

    Raises:
        ValueError: The plan has issues.
        RegistryError: Exchange refused a call, or a planned version is
            already published with other content.
    """
    if plan.issues:
        raise ValueError("a plan with issues is not published; fix them first")
    exchange = _Exchange(http, base_url)
    outcomes: list[PublishOutcome] = []
    for asset in plan.assets:
        if asset.type != "mcp":
            outcomes.append(
                PublishOutcome(
                    asset,
                    "skipped",
                    "agent assets are not published yet: the scan does not capture "
                    "the protocol and interface their metadata needs",
                )
            )
            continue
        outcomes.append(
            await _publish_one(asset, result, exchange, org_id=org_id, if_changed=if_changed)
        )
    return tuple(outcomes)


async def _publish_one(
    asset: PlannedAsset,
    result: ScanResult,
    exchange: _Exchange,
    *,
    org_id: str,
    if_changed: bool,
) -> PublishOutcome:
    group, asset_id, version = _gav(asset)
    latest = await exchange.latest(group, asset_id)
    if latest is not None:
        if latest.version == version and latest.digest == asset.digest:
            return PublishOutcome(asset, "unchanged", f"{version} is already published")
        if version in latest.versions:
            raise _version_exists(asset, latest)
        if if_changed and latest.digest == asset.digest:
            return PublishOutcome(
                asset, "unchanged", f"the latest version, {latest.version}, has this digest"
            )
    metadata = json.dumps(mcp_metadata(asset, result), sort_keys=True).encode()
    response = await exchange.post(
        _PUBLISH_PATH.format(org=org_id, group=group, asset=asset_id, version=version),
        data={
            "name": asset.name,
            "description": asset.description or "",
            "type": "mcp",
            f"properties.{DIGEST_PROPERTY}": asset.digest,
        },
        files={"files.mcp-metadata.json": ("mcp-metadata.json", metadata, "application/json")},
        headers={"x-sync-publication": "true"},
    )
    if response.status_code == 409:
        # A version that appeared since the read above, or an earlier attempt
        # of this one that landed before a retry: fine only with this digest.
        latest = await exchange.latest(group, asset_id)
        if latest is None or latest.version != version or latest.digest != asset.digest:
            raise _version_exists(asset, latest)
    else:
        _check(response, f"publishing {group}:{asset_id}:{version}")
    if asset.endpoint:
        await exchange.register_endpoint(asset, group, asset_id, version)
    return PublishOutcome(asset, "published", f"published {version}")


async def latest_version(
    http: httpx.AsyncClient, group_id: str, asset_id: str, *, base_url: str
) -> PublishedVersion | None:
    """The latest published version of ``group_id:asset_id``, or ``None`` when
    Exchange has no such asset. Its digest is read from the version's POM.

    Raises:
        RegistryError: Exchange refused the query or the POM download.
    """
    return await _Exchange(http, base_url).latest(group_id, asset_id)


def mcp_metadata(asset: PlannedAsset, result: ScanResult) -> dict[str, Any]:
    """The ``mcp-metadata`` file published with ``asset``: its transport, its
    tools with their descriptions and an input schema derived from the scanned
    parameters, and its ``urn:gav`` provenance (the agent-fabric plugin's
    shape, docs/verified-apis.md §12.3).

    A parameter's schema type comes from a bare ``str``/``int``/``float``/
    ``bool``/``list``/``dict`` annotation and is left open otherwise. No
    parameter is marked required: the scan does not record defaults.
    """
    server = next(s for s in result.mcp_servers if s.id == asset.service_id)
    transport: dict[str, Any]
    if server.transport == "stdio":
        transport = {"kind": "stdio"}
    else:
        kind = _TRANSPORT_KINDS[server.transport]
        transport = {"kind": kind}
        path = urlsplit(server.endpoint).path if server.endpoint else ""
        if path:
            transport["ssePath" if kind == "sse" else "path"] = path
    tools = []
    for t in sorted(result.tools, key=lambda t: t.name):
        if t.server != asset.service_id:
            continue
        properties = {
            p.name: ({"type": _SCHEMA_TYPES[p.annotation]} if p.annotation in _SCHEMA_TYPES else {})
            for p in t.parameters
        }
        tool: dict[str, Any] = {
            "name": t.name,
            "inputSchema": {"type": "object", "properties": properties},
        }
        if t.description:
            tool["description"] = t.description
        tools.append(tool)
    group, asset_id, version = _gav(asset)
    return {
        "transport": transport,
        "tools": tools,
        "provenance": {"id": f"urn:gav:{group}:{asset_id}:{version}"},
    }


class _Exchange:
    """The Exchange calls, on the control-plane client, against ``base_url``."""

    def __init__(self, http: httpx.AsyncClient, base_url: str) -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def post(self, path: str, **kw: Any) -> httpx.Response:
        return await self._http.post(self._base + path, **kw)

    async def latest(self, group_id: str, asset_id: str) -> PublishedVersion | None:
        data = await self._asset(_LATEST_QUERY, {"groupId": group_id, "assetId": asset_id})
        if data is None:
            return None
        pom = next(
            (
                f["externalLink"]
                for f in data.get("files") or ()
                if f.get("packaging") == "pom" and not f.get("classifier") and f.get("externalLink")
            ),
            None,
        )
        digest = None
        if pom is not None:
            if urlsplit(pom).scheme != "https":
                raise RegistryError(
                    f"Exchange returned a non-https POM link for {group_id}:{asset_id}"
                )
            # A pre-signed link: the control-plane client sends its credential
            # only to the control plane, so none goes to this host.
            response = await self._http.get(pom)
            _check(response, f"downloading the POM of {group_id}:{asset_id}")
            match = _POM_DIGEST.search(response.text)
            digest = match.group(1) if match else None
        version = str(data["version"])
        others = [str(v["version"]) for v in data.get("otherVersions") or ()]
        return PublishedVersion(
            version=version,
            digest=digest,
            versions=(version, *others),
        )

    async def register_endpoint(
        self, asset: PlannedAsset, group: str, asset_id: str, version: str
    ) -> None:
        # Instances belong to the version group, so a new patch version usually
        # has the endpoint already.
        data = await self._asset(
            _VERSION_QUERY, {"groupId": group, "assetId": asset_id, "version": version}
        )
        if data is None:
            raise RegistryError(
                f"Exchange does not list {group}:{asset_id}:{version} after publishing it"
            )
        if asset.endpoint in _endpoints(data):
            return
        response = await self.post(
            _INSTANCE_PATH.format(group=group, asset=asset_id, group_name=data["versionGroup"]),
            json={"name": asset.service_id, "endpointUri": asset.endpoint},
        )
        _check(response, f"registering the endpoint of {group}:{asset_id}:{version}")

    async def _asset(self, query: str, variables: dict[str, str]) -> dict[str, Any] | None:
        """The ``asset`` the query selects, or ``None`` when Exchange has none."""
        response = await self.post(_GRAPHQL_PATH, json={"query": query, "variables": variables})
        _check(response, "querying Exchange")
        body = response.json()
        errors = body.get("errors") or ()
        if errors:
            if all((e.get("extensions") or {}).get("status") == 404 for e in errors):
                return None
            raise RegistryError(
                "Exchange GraphQL query failed: " + "; ".join(str(e.get("message")) for e in errors)
            )
        asset = (body.get("data") or {}).get("asset")
        return asset if isinstance(asset, dict) else None


def _endpoints(data: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(i["endpointUri"]) for i in data.get("instances") or () if i.get("endpointUri"))


def _gav(asset: PlannedAsset) -> tuple[str, str, str]:
    # A plan without issues has both; plan_publication reports either missing.
    assert asset.group_id is not None and asset.version is not None
    return asset.group_id, asset.asset_id, asset.version


def _check(response: httpx.Response, action: str) -> None:
    if response.is_success:
        return
    raise RegistryError(
        f"Exchange refused {action}: HTTP {response.status_code} {response.text[:300]}",
        remediation=(
            "Check that the connected app has the Exchange Contributor permission "
            "for the business group, and that the group ID is right."
            if response.status_code in (401, 403)
            else "Fix what Exchange reported, then re-run donkey publish."
        ),
    )


def _version_exists(asset: PlannedAsset, latest: PublishedVersion | None) -> RegistryError:
    group, asset_id, version = _gav(asset)
    published = f" (latest: {latest.version})" if latest is not None else ""
    return RegistryError(
        f"{group}:{asset_id}:{version} is already published with other content{published}; "
        "Exchange never overwrites a version",
        remediation=(
            "Bump the version in [donkey.publication] (or the asset's own table) "
            "and re-run donkey publish."
        ),
    )
