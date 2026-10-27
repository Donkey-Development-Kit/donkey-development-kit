"""The ``donkey publish --dry-run`` plan: what direct Exchange publish would
register, computed offline (BG §2.5, #218).

:func:`plan_publication` turns a :class:`~donkey_kit.registry.scan.ScanResult`
into one :class:`PlannedAsset` per published service, an ``mcp`` asset per MCP
server and an ``agent`` asset per agent, and makes **no network call**.
:mod:`~donkey_kit.registry.exchange_publish` publishes them
(docs/verified-apis.md §7, §12.3).

Exchange coordinates are not in code, so they are declared, the way
``[donkey.mcp_servers]`` declares how a server is reached (#1193). A
``[donkey.publication]`` table in the scanned root's ``.donkey-kit.toml``, with
the ``.donkey-kit.local.toml`` beside it overriding key by key, sets them::

    [donkey.publication]
    group_id = "<org id>"   # default: the configured org_id (ANYPOINT_ORG_ID)
    version  = "1.3.0"      # semver, required
    protocol_version = "2025-06-18"                                  # optional
    provider = { organization = "Acme", url = "https://acme.test" }  # optional

    [donkey.publication.assets.hr-tools]   # a service ID; every key is optional
    asset_id    = "hr-tools-mcp"           # default: the service ID
    name        = "HR Tools"               # default: the service ID
    description = "Employee lookup tools." # an agent defaults to its own
    version     = "1.4.0"                  # default: the table's version
    documentation = "README.md"            # Markdown for the asset's page
    # protocol_version and provider override the table's, as version does

``protocol_version`` is the MCP protocol revision the server speaks and
``provider`` who provides it; both go in the asset's ``mcp-metadata`` file
(#1269), so they apply to MCP servers only: an agent's table may not set them.
An agent's protocol is declared with the scan, in ``[donkey.agents]`` (#1260).
``documentation`` is a Markdown file, relative to the scanned root and inside
it, that opens the asset's Exchange page. Each is part of the digest when it
is set, so changing one needs a new version.

Everything wrong with a declaration is a :class:`~donkey_kit.registry.scan.ScanIssue`,
never a raise, so the dry run reports every problem at once.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import LOCAL_TOML_NAME, TOML_NAME
from .publication import content_digest
from .scan import SCAN_FORMAT, ScanIssue, ScanResult

if sys.version_info >= (3, 11):
    import tomllib
else:  # 3.10 has no stdlib tomllib; the [core] dep ``tomli`` backfills it.
    import tomli as tomllib

__all__ = [
    "MCP_PROTOCOL_VERSIONS",
    "PLAN_FORMAT",
    "PlannedAsset",
    "Provider",
    "PublishPlan",
    "plan_publication",
]

# Bumped when the JSON shape changes incompatibly, as SCAN_FORMAT is.
PLAN_FORMAT = 1

_TABLE_KEYS = frozenset({"group_id", "version", "protocol_version", "provider", "assets"})
_ASSET_KEYS = frozenset(
    {"asset_id", "name", "description", "version", "protocol_version", "provider", "documentation"}
)

#: The MCP protocol revisions an ``mcp-metadata`` file may name: the
#: ``protocolVersion`` enum of Agent Fabric's MCP metadata schema (#1269).
MCP_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

# Semantic Versioning 2.0.0's own pattern: Exchange versions are semver
# (docs/verified-apis.md §7, GAV coordinates).
_SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?"
)


@dataclass(frozen=True)
class Provider:
    """Who provides an MCP server: the ``provider`` of its ``mcp-metadata``.

    Agent Fabric shows ``organization`` as the asset's provider; ``url`` is
    where Exchange links it. Both are required by the metadata schema.
    """

    organization: str
    url: str


@dataclass(frozen=True)
class PlannedAsset:
    """One Exchange asset the publish would register.

    ``type`` is the Exchange asset type, ``mcp`` or ``agent`` (the Exchange
    Experience API's type enum; ``mcp`` is confirmed by a live publish).
    ``group_id`` or ``version`` is ``None`` when it is undeclared, which is
    also reported as an issue. ``digest`` covers what would be published for
    this asset, not its version, so ``--if-changed`` can compare it with the
    latest published version's.

    ``protocol_version`` and ``provider`` are ``None`` when undeclared, and
    always for an agent. ``protocol`` is an agent's Agent Fabric protocol, its
    declared one or ``other``, and ``None`` for an MCP server.
    ``documentation`` is the declared Markdown file's path and
    ``documentation_text`` its content, which is not part of :meth:`PublishPlan.to_dict`.
    """

    service_id: str
    type: str
    group_id: str | None
    asset_id: str
    version: str | None
    name: str
    description: str | None
    endpoint: str | None
    tools: tuple[str, ...]
    digest: str
    protocol_version: str | None = None
    provider: Provider | None = None
    documentation: str | None = None
    documentation_text: str | None = field(default=None, repr=False)
    protocol: str | None = None


@dataclass(frozen=True)
class PublishPlan:
    """The dry-run result: the planned assets, and the scan's issues plus the
    ``[donkey.publication]`` declaration's. A plan with issues is not published.

    A declaration adds ``missing-group-id``, ``missing-version``,
    ``invalid-version`` (not semver), ``invalid-publication`` (a malformed
    table, an unknown key, a protocol version or provider the metadata schema
    rejects, a protocol version or provider on an agent's table, or a
    documentation file that cannot be read) and
    ``unknown-service`` (an ``assets`` table for a service ID no agent or MCP
    server has); their ``path`` is the TOML file and their ``line`` is ``0``.
    """

    assets: tuple[PlannedAsset, ...] = ()
    issues: tuple[ScanIssue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The plan as plain JSON-ready data."""
        return {
            "plan_format": PLAN_FORMAT,
            "assets": [
                {
                    "service_id": a.service_id,
                    "type": a.type,
                    "group_id": a.group_id,
                    "asset_id": a.asset_id,
                    "version": a.version,
                    "name": a.name,
                    "description": a.description,
                    "endpoint": a.endpoint,
                    "tools": list(a.tools),
                    "digest": a.digest,
                    "protocol_version": a.protocol_version,
                    "provider": (
                        {"organization": a.provider.organization, "url": a.provider.url}
                        if a.provider
                        else None
                    ),
                    "documentation": a.documentation,
                    "protocol": a.protocol,
                }
                for a in self.assets
            ],
            "issues": [
                {
                    "kind": i.kind,
                    "subject": i.subject,
                    "path": i.path,
                    "line": i.line,
                    "detail": i.detail,
                }
                for i in self.issues
            ],
        }

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, two-space indent, trailing newline."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"


def plan_publication(
    result: ScanResult, root: str | Path, *, default_group_id: str | None = None
) -> PublishPlan:
    """Plan the Exchange assets for ``result`` (scanned from ``root``) offline.

    ``default_group_id`` is the group used when ``[donkey.publication]`` sets
    none: the org ID, which Exchange uses as the default Maven group
    (docs/verified-apis.md §7). Makes no network call.
    """
    base = Path(root)
    directory = base.parent if base.is_file() else base
    declared = _Declaration(directory)
    group_id = declared.group_id or default_group_id
    if not group_id:
        declared.issue(
            "missing-group-id",
            "donkey.publication",
            "set group_id in [donkey.publication], or configure org_id (ANYPOINT_ORG_ID)",
        )
    if declared.version is None and not declared.reported_version:
        declared.issue(
            "missing-version", "donkey.publication", "set version in [donkey.publication]"
        )

    published = result.to_dict()
    tools_of: dict[str, list[dict[str, Any]]] = {}
    for tool in published["tools"]:
        if tool["server"] is not None:
            del tool["path"], tool["line"]
            tools_of.setdefault(tool["server"], []).append(tool)

    services: list[tuple[str, str, dict[str, Any]]] = []
    for server in published["mcp_servers"]:
        content = dict(
            server, tools=sorted(tools_of.get(server["id"], []), key=lambda t: t["name"])
        )
        services.append((server["id"], "mcp", content))
    for agent in published["agents"]:
        del agent["path"], agent["line"]
        services.append((agent["name"], "agent", agent))
    services.sort(key=lambda s: (s[0], s[1]))

    assets: list[PlannedAsset] = []
    known: set[str] = set()
    for service_id, kind, content in services:
        known.add(service_id)
        entry = declared.asset(service_id)
        name = entry.get("name") or service_id
        description = entry.get("description") or content.get("description")
        protocol_version: str | None = None
        provider: Provider | None = None
        protocol: str | None = None
        if kind == "mcp":
            protocol_version = entry.get("protocol_version") or declared.protocol_version
            provider = entry.get("provider") or declared.provider
        else:
            protocol = content.get("protocol") or "other"
            for key in sorted({"protocol_version", "provider"} & set(entry)):
                declared.issue(
                    "invalid-publication",
                    f"publication asset {service_id}",
                    f"{key} describes an MCP server, and {service_id!r} is an agent; remove it",
                    path=declared.declared_in[service_id],
                )
        documentation = entry.get("documentation")
        text = declared.documentation_text.get(service_id)
        metadata: dict[str, Any] = {
            "scan_format": SCAN_FORMAT,
            "type": kind,
            "name": name,
            "description": description,
        }
        # Only what is set, so an asset that declares none keeps its digest.
        if protocol_version:
            metadata["protocol_version"] = protocol_version
        if provider:
            metadata["provider"] = {"organization": provider.organization, "url": provider.url}
        if text is not None:
            metadata["documentation"] = text
        assets.append(
            PlannedAsset(
                service_id=service_id,
                type=kind,
                group_id=group_id,
                asset_id=entry.get("asset_id") or service_id,
                version=entry.get("version") or declared.version,
                name=name,
                description=description,
                endpoint=content.get("endpoint"),
                tools=tuple(t["name"] for t in content.get("tools", ())),
                digest=content_digest(content, metadata),
                protocol_version=protocol_version,
                provider=provider,
                documentation=documentation if text is not None else None,
                documentation_text=text,
                protocol=protocol,
            )
        )
    for service_id in sorted(set(declared.assets) - known):
        declared.issue(
            "unknown-service",
            f"publication asset {service_id}",
            f"no agent or MCP server has service ID {service_id!r}, so this table "
            "describes nothing; fix the ID or remove the table",
            path=declared.declared_in[service_id],
        )
    issues = sorted(
        (*result.issues, *declared.issues), key=lambda i: (i.path, i.line, i.kind, i.subject)
    )
    return PublishPlan(tuple(assets), tuple(issues))


class _Declaration:
    """The ``[donkey.publication]`` tables beside the scanned code.

    Read as ``[donkey.mcp_servers]`` is (scan's ``_Declarations``): the project
    file first, the local file merged over it key by key, a user-wide file never.
    """

    def __init__(self, directory: Path) -> None:
        self.issues: list[ScanIssue] = []
        self.group_id: str | None = None
        self.version: str | None = None
        self.reported_version = False
        self.protocol_version: str | None = None
        self.provider: Provider | None = None
        self.assets: dict[str, dict[str, Any]] = {}
        self.declared_in: dict[str, str] = {}
        self.documentation_text: dict[str, str] = {}
        self._directory = directory
        self._file = TOML_NAME
        for name in (TOML_NAME, LOCAL_TOML_NAME):
            path = directory / name
            if path.is_file():
                self._read(path, name)

    def issue(self, kind: str, subject: str, detail: str, *, path: str | None = None) -> None:
        self.issues.append(ScanIssue(kind, subject, path or self._file, 0, detail))

    def asset(self, service_id: str) -> dict[str, Any]:
        return self.assets.get(service_id, {})

    def _read(self, path: Path, name: str) -> None:
        # Unreadable or malformed files are already scan issues, from the
        # [donkey.mcp_servers] reader of the same two files.
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return
        donkey = data.get("donkey", {})
        table = donkey.get("publication") if isinstance(donkey, dict) else None
        if table is None:
            return
        self._file = name
        if not isinstance(table, dict):
            self.issue("invalid-publication", "donkey.publication", "must be a table", path=name)
            return
        for key in sorted(set(table) - _TABLE_KEYS):
            self.issue(
                "invalid-publication",
                "donkey.publication",
                f"unknown key {key!r}; the keys are {', '.join(sorted(_TABLE_KEYS))}",
                path=name,
            )
        if "group_id" in table:
            value = table["group_id"]
            if isinstance(value, str) and value.strip():
                self.group_id = value
            else:
                self.issue(
                    "invalid-publication",
                    "donkey.publication",
                    f"group_id must be a non-empty string, not {value!r}",
                    path=name,
                )
        if "version" in table:
            self.version = self._version(table["version"], "donkey.publication", name)
        if "protocol_version" in table:
            self.protocol_version = self._protocol_version(
                table["protocol_version"], "donkey.publication", name
            )
        if "provider" in table:
            self.provider = self._provider(table["provider"], "donkey.publication", name)
        assets = table.get("assets", {})
        if not isinstance(assets, dict):
            self.issue(
                "invalid-publication",
                "donkey.publication.assets",
                "must be a table of service IDs",
                path=name,
            )
            return
        for service_id, entry in assets.items():
            self._asset(service_id, entry, name)

    def _asset(self, service_id: str, entry: object, name: str) -> None:
        subject = f"publication asset {service_id}"
        if not isinstance(entry, dict):
            self.issue("invalid-publication", subject, "must be a table", path=name)
            return
        merged = self.assets.setdefault(service_id, {})
        self.declared_in.setdefault(service_id, name)
        for key, value in entry.items():
            if key not in _ASSET_KEYS:
                self.issue(
                    "invalid-publication",
                    subject,
                    f"unknown key {key!r}; the keys are {', '.join(sorted(_ASSET_KEYS))}",
                    path=name,
                )
            elif key == "version":
                version = self._version(value, subject, name)
                if version is not None:
                    merged[key] = version
            elif key == "protocol_version":
                protocol_version = self._protocol_version(value, subject, name)
                if protocol_version is not None:
                    merged[key] = protocol_version
            elif key == "provider":
                provider = self._provider(value, subject, name)
                if provider is not None:
                    merged[key] = provider
            elif key == "documentation":
                text = self._documentation(value, subject, name)
                if text is not None:
                    merged[key] = value
                    self.documentation_text[service_id] = text
            elif isinstance(value, str) and value.strip():
                merged[key] = value
            else:
                self.issue(
                    "invalid-publication",
                    subject,
                    f"{key} must be a non-empty string, not {value!r}",
                    path=name,
                )

    def _protocol_version(self, value: object, subject: str, name: str) -> str | None:
        if isinstance(value, str) and value in MCP_PROTOCOL_VERSIONS:
            return value
        self.issue(
            "invalid-publication",
            subject,
            f"protocol_version must be one of {', '.join(MCP_PROTOCOL_VERSIONS)}, not {value!r}",
            path=name,
        )
        return None

    def _provider(self, value: object, subject: str, name: str) -> Provider | None:
        if isinstance(value, dict) and set(value) == {"organization", "url"}:
            organization, url = value["organization"], value["url"]
            if (
                isinstance(organization, str)
                and organization.strip()
                and isinstance(url, str)
                and url.startswith(("https://", "http://"))
            ):
                return Provider(organization, url)
        self.issue(
            "invalid-publication",
            subject,
            "provider must be a table with exactly organization (a non-empty string) "
            f"and url (an http or https URL), not {value!r}",
            path=name,
        )
        return None

    def _documentation(self, value: object, subject: str, name: str) -> str | None:
        root = self._directory.resolve()
        path = (root / value).resolve() if isinstance(value, str) and value.strip() else None
        if path is None or not path.is_relative_to(root):
            problem = f"documentation must be a path inside the scanned root, not {value!r}"
        else:
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                reason = getattr(exc, "strerror", None) or exc
                problem = f"documentation {value!r} cannot be read: {reason}"
        self.issue("invalid-publication", subject, problem, path=name)
        return None

    def _version(self, value: object, subject: str, name: str) -> str | None:
        if isinstance(value, str) and _SEMVER.fullmatch(value):
            return value
        self.reported_version = True
        self.issue(
            "invalid-version",
            subject,
            f"version must be a semantic version such as 1.3.0, not {value!r}",
            path=name,
        )
        return None
