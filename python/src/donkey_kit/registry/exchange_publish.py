"""``donkey publish``: register a plan's MCP servers and agents in Exchange
(BG §2.5, #218, #1260).

:func:`publish_plan` sends each asset of a
:class:`~donkey_kit.registry.publish_plan.PublishPlan` with the Exchange
multipart publish, over the control-plane client: an ``mcp`` asset with its
:func:`mcp_metadata` file, an ``agent`` asset with its :func:`agent_metadata`
file and, for an ``a2a_v1`` agent, its :func:`agent_card`. Every call below is in the
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
  once per endpoint URI;
* a new version's page (:mod:`~donkey_kit.registry.exchange_page`) is written
  to its portal draft as Markdown and the draft published (#1269);
* Exchange takes an asset's name and description from its first version and
  ignores them on later ones, so a new version of an existing asset whose
  declared name or description differs sets them with the version-less asset
  PATCH, which applies to every version (#1281).

With ``if_changed`` an asset whose latest published digest equals the planned
one is left alone, so a merge that changes nothing publishes nothing. A planned
version that is already published with other content fails before anything is
sent: bump the version.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from ..core.errors import RegistryError
from . import _scopes
from .exchange_page import PageSource, documentation_page
from .publish_plan import PlannedAsset, PublishPlan
from .scan import ScanResult

__all__ = [
    "DIGEST_PROPERTY",
    "PublishOutcome",
    "PublishedVersion",
    "agent_card",
    "agent_metadata",
    "latest_version",
    "mcp_metadata",
    "publish_plan",
    "published_tools",
]

# docs/verified-apis.md §12.3.
_PUBLISH_PATH = "/exchange/api/v2/organizations/{org}/assets/{group}/{asset}/{version}"
_GRAPHQL_PATH = "/graph/api/v2/graphql"
_INSTANCE_PATH = (
    "/exchange/api/v2/assets/{group}/{asset}/versionGroups/{group_name}/instances/external"
)
# The asset portal's draft, its pages, and the v1 PATCH that publishes the
# draft: what `anypoint-cli-v4 exchange asset page upload` calls (#1269).
_PORTAL_DRAFT_PATH = "/exchange/api/v2/assets/{group}/{asset}/{version}/portal/draft"
_PUBLISH_DRAFT_PATH = "/exchange/api/v1/assets/{group}/{asset}/{version}"
_HOME_PAGE = "home"
# The Exchange UI's asset edit: name and description, for every version (#1281).
_ASSET_PATH = "/exchange/api/v2/assets/{group}/{asset}"

#: The ``properties.*`` key the digest is published under; Exchange writes it to
#: the generated POM as ``<ddkDigest>`` (docs/verified-apis.md §7).
DIGEST_PROPERTY = "ddkDigest"

_LATEST_QUERY = (
    "query($groupId: String!, $assetId: String!) { asset(groupId: $groupId, assetId: $assetId) "
    "{ version name description files { classifier packaging externalLink } "
    "otherVersions { version } } }"
)
# One version's files: its ``mcp-metadata`` or agent card is read back by
# Publication.verify() (docs/verified-apis.md §7, #991, #1354). A version
# Exchange does not have is a ``200`` whose error carries
# ``extensions.status`` ``404``, as for the latest query.
_FILES_QUERY = (
    "query($groupId: String!, $assetId: String!, $version: String!) "
    "{ asset(groupId: $groupId, assetId: $assetId, version: $version) "
    "{ version files { classifier packaging externalLink } } }"
)
_VERSION_QUERY = (
    "query($groupId: String!, $assetId: String!, $version: String!) "
    "{ asset(groupId: $groupId, assetId: $assetId, version: $version) "
    "{ versionGroup instances { endpointUri } } }"
)
# The agent card file's classifier per protocol: the agent-fabric plugin's
# (docs/verified-apis.md §7). An ``other`` agent has no card.
_CARD_CLASSIFIERS = {"a2a_v1": "a2a-v1-card"}
# The card classifiers read back, newest first: an agent published before the
# SDK's ``a2a_v1`` cards (the agent-fabric plugin's A2A 0.3 card) has an
# ``a2a-card`` file instead (docs/verified-apis.md §7, #1354).
_READ_CARD_CLASSIFIERS = ("a2a-v1-card", "a2a-card")
# The agent-metadata ``protocol`` per declared protocol. Exchange accepts
# ``a2a_v1`` there, but API Manager then refuses the agent an ``a2a`` instance
# ("must have an A2A protocol"); with ``a2a`` and the ``a2a-v1-card`` file,
# Exchange records ``protocol: a2a, protocol-version: v1`` and the instance is
# created (docs/verified-apis.md §12.5, #226).
_EXCHANGE_PROTOCOLS = {"a2a_v1": "a2a"}
# What ``donkey.serve()`` reads and writes; a2a/_server.py's ``_MODES``, which
# registry cannot import (the layered-import rule).
_AGENT_MODES = ("text/plain", "application/json")
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
    included. ``name`` and ``description`` are the asset's, as Exchange shows
    them.
    """

    version: str
    digest: str | None
    versions: tuple[str, ...]
    name: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class PublishOutcome:
    """What :func:`publish_plan` did with one planned asset.

    ``status`` is ``published`` (a new version was created) or ``unchanged``
    (the latest version already has this digest); ``detail`` says which
    version.
    """

    asset: PlannedAsset
    status: Literal["published", "unchanged"]
    detail: str


async def publish_plan(
    plan: PublishPlan,
    result: ScanResult,
    http: httpx.AsyncClient,
    *,
    base_url: str,
    org_id: str,
    if_changed: bool = True,
    source: PageSource | None = None,
) -> tuple[PublishOutcome, ...]:
    """Publish ``plan``'s assets to Exchange, one at a time, in plan order.

    ``http`` is the control-plane client and ``base_url`` the control plane;
    ``result`` is the scan the plan came from. ``source`` is where the publish
    runs from, for each new version's page. A plan with issues is refused.

    Raises:
        ValueError: The plan has issues.
        RegistryError: Exchange refused a call, or a planned version is
            already published with other content.
        AuthError: Exchange refused a call with ``401``/``403``; its ``remediation``
            names the ``manage:exchange`` scope (docs/scopes.md).
    """
    if plan.issues:
        raise ValueError("a plan with issues is not published; fix them first")
    exchange = _Exchange(http, base_url)
    outcomes: list[PublishOutcome] = []
    for asset in plan.assets:
        outcomes.append(
            await _publish_one(
                asset, result, exchange, org_id=org_id, if_changed=if_changed, source=source
            )
        )
    return tuple(outcomes)


async def _publish_one(
    asset: PlannedAsset,
    result: ScanResult,
    exchange: _Exchange,
    *,
    org_id: str,
    if_changed: bool,
    source: PageSource | None,
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
    data, files = _content(asset, result)
    response = await exchange.post(
        _PUBLISH_PATH.format(org=org_id, group=group, asset=asset_id, version=version),
        data={
            "name": asset.name,
            "description": asset.description or "",
            "type": asset.type,
            f"properties.{DIGEST_PROPERTY}": asset.digest,
            **data,
        },
        files=files,
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
    await exchange.publish_page(
        group, asset_id, version, documentation_page(asset, result, source=source)
    )
    detail = f"published {version}"
    changes = _changed_details(asset, latest)
    if changes:
        await exchange.update_details(group, asset_id, changes)
        detail += f"; updated the asset's {' and '.join(changes)}"
    return PublishOutcome(asset, "published", detail)


def _content(
    asset: PlannedAsset, result: ScanResult
) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    """The type's own form fields and files for the multipart publish."""

    def file(classifier: str, body: dict[str, Any]) -> dict[str, tuple[str, bytes, str]]:
        name = f"{classifier}.json"
        content = json.dumps(body, sort_keys=True).encode()
        return {f"files.{name}": (name, content, "application/json")}

    if asset.type == "mcp":
        return {}, file("mcp-metadata", mcp_metadata(asset, result))
    metadata = agent_metadata(asset)
    files = file("agent-metadata", metadata)
    classifier = _CARD_CLASSIFIERS.get(str(asset.protocol))
    if classifier is not None:
        files |= file(classifier, agent_card(asset, result))
    # The agent-fabric plugin's properties; Exchange keeps them in the POM.
    fields = {"properties.protocol": metadata["protocol"], "properties.platform": "other"}
    return fields, files


async def latest_version(
    http: httpx.AsyncClient, group_id: str, asset_id: str, *, base_url: str
) -> PublishedVersion | None:
    """The latest published version of ``group_id:asset_id``, or ``None`` when
    Exchange has no such asset. Its digest is read from the version's POM.

    Raises:
        RegistryError: Exchange refused the query or the POM download.
        AuthError: Exchange refused the query with ``401``/``403``.
    """
    return await _Exchange(http, base_url).latest(group_id, asset_id)


async def published_tools(
    http: httpx.AsyncClient, group_id: str, asset_id: str, version: str, *, base_url: str
) -> list[dict[str, Any]] | None:
    """The ``tools`` of the ``mcp-metadata`` file published with
    ``group_id:asset_id:version``, as Exchange stores it, or ``None`` when
    Exchange has no such version. A version published without the file has no
    tools to read, which is a :class:`RegistryError`.

    Raises:
        RegistryError: Exchange refused the query or the download, or the
            version has no ``mcp-metadata`` file.
        AuthError: Exchange refused the query with ``401``/``403``.
    """
    return await _Exchange(http, base_url).tools(group_id, asset_id, version)


async def published_card(
    http: httpx.AsyncClient, group_id: str, asset_id: str, version: str, *, base_url: str
) -> dict[str, Any] | None:
    """The A2A agent card published with ``group_id:asset_id:version``: its
    ``a2a-v1-card`` file, else its ``a2a-card`` one, as Exchange stores it, or
    ``None`` when Exchange has no such version. A version with neither file
    (an agent with protocol ``other``, or not an agent) is a :class:`RegistryError`.

    Raises:
        RegistryError: Exchange refused the query or the download, or the
            version has no card file.
        AuthError: Exchange refused the query with ``401``/``403``.
    """
    return await _Exchange(http, base_url).card(group_id, asset_id, version)


def mcp_metadata(asset: PlannedAsset, result: ScanResult) -> dict[str, Any]:
    """The ``mcp-metadata`` file published with ``asset``: its transport, its
    tools with their descriptions and an input schema derived from the scanned
    parameters, and its ``urn:gav`` provenance (the agent-fabric plugin's
    shape, docs/verified-apis.md §12.3). Its declared ``protocolVersion`` and
    ``provider`` are added when set, and ``capabilities`` says it has tools and
    no resources, prompts or logging: the scan sees only tools (#1269).

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
    metadata: dict[str, Any] = {
        "transport": transport,
        "tools": tools,
        "capabilities": {
            "tools": bool(tools),
            "resources": False,
            "prompts": False,
            "logging": False,
        },
        "provenance": {"id": f"urn:gav:{group}:{asset_id}:{version}"},
    }
    if asset.protocol_version:
        metadata["protocolVersion"] = asset.protocol_version
    if asset.provider:
        metadata["provider"] = {
            "organization": asset.provider.organization,
            "url": asset.provider.url,
        }
    return metadata


def agent_metadata(asset: PlannedAsset) -> dict[str, Any]:
    """The ``agent-metadata`` file published with the agent ``asset``: its
    protocol, platform, name and ``urn:gav`` provenance, in Agent Fabric's
    agent metadata schema, as the agent-fabric plugin builds it
    (docs/verified-apis.md §7). An ``a2a_v1`` agent's protocol is ``a2a``:
    its card's classifier, ``a2a-v1-card``, carries the A2A version, which is
    what API Manager reads (#226). ``platform`` is ``other``: the SDK runs
    anywhere. ``connections`` is empty, since the scan does not see what an
    agent calls; the description is added when one is planned.
    """
    group, asset_id, version = _gav(asset)
    metadata: dict[str, Any] = {
        "protocol": _EXCHANGE_PROTOCOLS.get(str(asset.protocol), asset.protocol or "other"),
        "platform": "other",
        "kind": "agent",
        "name": asset.service_id,
        "connections": [],
        "provenance": {"id": f"urn:gav:{group}:{asset_id}:{version}"},
    }
    if asset.description:
        metadata["description"] = asset.description
    return metadata


def agent_card(asset: PlannedAsset, result: ScanResult) -> dict[str, Any]:
    """The A2A 1.0 agent card published with an ``a2a_v1`` agent ``asset``.

    It is the card ``donkey.serve()`` serves at ``/.well-known/agent-card.json``
    for the same ``@donkey.agent``, built from the scan rather than the running
    agent: served at ``asset.endpoint`` and versioned as the asset is.

    Raises:
        ValueError: ``asset`` has no endpoint; scan reports an ``a2a_v1`` agent
            without one as an issue.
    """
    if not asset.endpoint:
        raise ValueError(f"agent {asset.service_id!r} has no endpoint for its card")
    agent = next(a for a in result.agents if a.name == asset.service_id)
    return {
        "name": agent.name,
        "description": agent.description or "",
        "version": asset.version,
        "supportedInterfaces": [
            {"url": asset.endpoint, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "capabilities": {"streaming": False},
        "defaultInputModes": list(_AGENT_MODES),
        "defaultOutputModes": list(_AGENT_MODES),
        "skills": [
            {"id": skill, "name": skill, "description": skill, "tags": [skill]}
            for skill in agent.skills
        ],
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
            _check(response, f"downloading the POM of {group_id}:{asset_id}", scope=None)
            match = _POM_DIGEST.search(response.text)
            digest = match.group(1) if match else None
        version = str(data["version"])
        others = [str(v["version"]) for v in data.get("otherVersions") or ()]
        return PublishedVersion(
            version=version,
            digest=digest,
            versions=(version, *others),
            name=data.get("name"),
            description=data.get("description"),
        )

    async def tools(
        self, group_id: str, asset_id: str, version: str
    ) -> list[dict[str, Any]] | None:
        coordinates = f"{group_id}:{asset_id}:{version}"
        body = await self._file(group_id, asset_id, version, ("mcp-metadata",))
        if body is None:
            return None
        tools = body.get("tools")
        if not isinstance(tools, list):
            raise RegistryError(f"The mcp-metadata of {coordinates} has no tools list")
        return [t for t in tools if isinstance(t, dict)]

    async def card(self, group_id: str, asset_id: str, version: str) -> dict[str, Any] | None:
        return await self._file(group_id, asset_id, version, _READ_CARD_CLASSIFIERS)

    async def _file(
        self, group_id: str, asset_id: str, version: str, classifiers: tuple[str, ...]
    ) -> dict[str, Any] | None:
        """The JSON body of the version's first ``json`` file among
        ``classifiers``, downloaded from its pre-signed link, or ``None`` when
        Exchange has no such version."""
        coordinates = f"{group_id}:{asset_id}:{version}"
        data = await self._asset(
            _FILES_QUERY,
            {"groupId": group_id, "assetId": asset_id, "version": version},
            scope=_scopes.EXCHANGE_READ,
        )
        if data is None:
            return None
        links = {
            f.get("classifier"): f["externalLink"]
            for f in data.get("files") or ()
            if f.get("packaging") == "json" and f.get("externalLink")
        }
        found = next(((c, links[c]) for c in classifiers if c in links), None)
        if found is None:
            raise RegistryError(f"{coordinates} has no {' or '.join(classifiers)} file in Exchange")
        classifier, link = found
        if urlsplit(link).scheme != "https":
            raise RegistryError(
                f"Exchange returned a non-https {classifier} link for {coordinates}"
            )
        # A pre-signed link, like the POM's: no credential goes to this host.
        response = await self._http.get(link)
        _check(response, f"downloading the {classifier} of {coordinates}", scope=None)
        body = response.json()
        if not isinstance(body, dict):
            raise RegistryError(f"The {classifier} of {coordinates} is not a JSON object")
        return body

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
        if response.status_code == 404:
            # What a connected app with edit:exchange but not manage:exchange
            # gets here: the version is published, its endpoint is not (#23).
            raise RegistryError(
                f"Exchange refused registering the endpoint of {group}:{asset_id}:{version}: "
                f"HTTP 404 {response.text[:300]}",
                remediation=(
                    f"Registering an endpoint needs the scope '{_scopes.EXCHANGE_PUBLISH}'; "
                    "a connected app with only edit:exchange gets this 404. Grant it, then "
                    f"re-run donkey publish. See {_scopes.SCOPES_DOC}."
                ),
                response=response,
            )
        _check(response, f"registering the endpoint of {group}:{asset_id}:{version}")

    async def publish_page(self, group: str, asset_id: str, version: str, markdown: str) -> None:
        """Write ``markdown`` as the version's Home page and publish it."""
        draft = _PORTAL_DRAFT_PATH.format(group=group, asset=asset_id, version=version)
        action = f"writing the page of {group}:{asset_id}:{version}"
        # Reading the draft opens one, as the CLI does before editing a page.
        _check(await self._http.get(self._base + draft), action)
        created = await self.post(draft + "/pages", json={"pagePath": _HOME_PAGE})
        if created.status_code != 409:  # 409: the page exists already
            _check(created, action)
        response = await self._http.put(
            f"{self._base}{draft}/pages/{_HOME_PAGE}",
            content=markdown.encode(),
            headers={"Content-Type": "text/markdown"},
        )
        _check(response, action)
        response = await self._http.patch(
            self._base + _PUBLISH_DRAFT_PATH.format(group=group, asset=asset_id, version=version)
        )
        _check(response, f"publishing the page of {group}:{asset_id}:{version}")

    async def update_details(self, group: str, asset_id: str, changes: dict[str, str]) -> None:
        """Set the asset's ``name`` and/or ``description`` on every version."""
        response = await self._http.patch(
            self._base + _ASSET_PATH.format(group=group, asset=asset_id), json=changes
        )
        _check(response, f"updating the name and description of {group}:{asset_id}")

    async def _asset(
        self, query: str, variables: dict[str, str], *, scope: str = _scopes.EXCHANGE_PUBLISH
    ) -> dict[str, Any] | None:
        """The ``asset`` the query selects, or ``None`` when Exchange has none.
        ``scope`` is the one a ``401``/``403`` names."""
        response = await self.post(_GRAPHQL_PATH, json={"query": query, "variables": variables})
        _check(response, "querying Exchange", scope=scope)
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


def _changed_details(asset: PlannedAsset, latest: PublishedVersion | None) -> dict[str, str]:
    """The declared name and description that differ from the existing asset's.

    A first publish sets both, so there is nothing to change; an undeclared
    description leaves the asset's as it is.
    """
    if latest is None:
        return {}
    changes: dict[str, str] = {}
    if asset.name != latest.name:
        changes["name"] = asset.name
    if asset.description and asset.description != latest.description:
        changes["description"] = asset.description
    return changes


def _endpoints(data: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(i["endpointUri"]) for i in data.get("instances") or () if i.get("endpointUri"))


def _gav(asset: PlannedAsset) -> tuple[str, str, str]:
    # A plan without issues has both; plan_publication reports either missing.
    assert asset.group_id is not None and asset.version is not None
    return asset.group_id, asset.asset_id, asset.version


def _check(
    response: httpx.Response, action: str, *, scope: str | None = _scopes.EXCHANGE_PUBLISH
) -> None:
    """Raise unless ``response`` succeeded. A ``401``/``403`` is an :class:`AuthError`
    naming ``scope``; ``None`` is for a call that sends no control-plane credential."""
    if response.is_success:
        return
    message = f"Exchange refused {action}: HTTP {response.status_code} {response.text[:300]}"
    if scope is not None and response.status_code in (401, 403):
        raise _scopes.missing_scope(response, message, scope=scope, org_id=None)
    raise RegistryError(
        message, remediation="Fix what Exchange reported, then re-run donkey publish."
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
