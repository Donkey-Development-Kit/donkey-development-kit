"""A stateful fake Exchange for ``donkey publish`` tests (#218).

It answers the calls the publisher makes (the multipart publish, the GraphQL
``asset`` query, the POM and ``mcp-metadata`` downloads, the external-instance POST, the portal
draft, page and draft-publish calls, and the asset PATCH) by replaying the
bodies captured in ``tests/fixtures/anypoint/exchange_publish/`` (an ``agent``
asset's GraphQL and POM from its own ``agent.*`` captures, #1260), with the
version, digest, name, description, instances and pages of what has been
published so far. As Exchange does, an asset keeps the name and description of
its first version until the asset PATCH changes them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "exchange_publish"
BASE_URL = "https://anypoint.mulesoft.com"
ORG = "00000000-0000-4000-8000-306f0858eed3"
_POM_HOST = "exchange-files.example.invalid"
_CAPTURED_DIGEST = "sha256:" + "5" * 64


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@dataclass
class Published:
    """A published version: its digest, its ``mcp-metadata`` or ``agent-metadata``
    file, and, for one published through the fake, every file and form field."""

    digest: str | None
    metadata: dict[str, Any]
    type: str = "mcp"
    files: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class FakeExchange:
    """Published versions per ``(group, asset)``, in publish order, plus the
    endpoints registered per version group, the published Home page per
    version, and each asset's name and description. ``requests`` records every
    call and ``metadata_reads`` every ``mcp-metadata`` download; ``fail_pages``,
    ``fail_details`` and ``fail_instances`` answer the page write, the asset
    PATCH and the external-instance POST with a ``500``."""

    assets: dict[tuple[str, str], dict[str, Published]] = field(default_factory=dict)
    instances: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)
    pages: dict[tuple[str, str, str], str] = field(default_factory=dict)
    details: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    fail_pages: bool = False
    fail_details: bool = False
    fail_instances: bool = False
    metadata_reads: list[tuple[str, str, str]] = field(default_factory=list)
    _drafts: dict[tuple[str, str, str], str | None] = field(default_factory=dict)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: self.handle(request))

    def publish(
        self,
        group: str,
        asset: str,
        version: str,
        digest: str | None,
        *,
        name: str | None = None,
        description: str = "",
        kind: str = "mcp",
    ) -> None:
        self.details.setdefault((group, asset), {"name": name or asset, "description": description})
        self.assets.setdefault((group, asset), {})[version] = Published(digest, {}, kind)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == _POM_HOST:
            return self._pom(request)
        if path == "/graph/api/v2/graphql":
            return self._graphql(json.loads(request.content))
        publish = re.fullmatch(
            r"/exchange/api/v2/organizations/[^/]+/assets/([^/]+)/([^/]+)/([^/]+)", path
        )
        if publish and request.method == "POST":
            return self._publish(request, *publish.groups())
        instance = re.fullmatch(
            r"/exchange/api/v2/assets/([^/]+)/([^/]+)/versionGroups/([^/]+)/instances/external",
            path,
        )
        if instance and request.method == "POST":
            if self.fail_instances:
                return httpx.Response(500, json={"message": "instance creation failed"})
            endpoint = json.loads(request.content)["endpointUri"]
            self.instances.setdefault(instance.groups(), []).append(endpoint)
            return httpx.Response(201, text=fixture("instance.201.json"))
        portal = re.fullmatch(
            r"/exchange/api/v2/assets/([^/]+)/([^/]+)/([^/]+)/portal/draft(/pages(?:/home)?)?",
            path,
        )
        if portal:
            return self._portal(request, (portal[1], portal[2], portal[3]), portal[4])
        details = re.fullmatch(r"/exchange/api/v2/assets/([^/]+)/([^/]+)", path)
        if details and request.method == "PATCH":
            return self._details(request, (details[1], details[2]))
        draft = re.fullmatch(r"/exchange/api/v1/assets/([^/]+)/([^/]+)/([^/]+)", path)
        if draft and request.method == "PATCH":
            key = (draft[1], draft[2], draft[3])
            assert key in self._drafts, "PATCH before the draft was opened"
            content = self._drafts.pop(key)
            if content is not None:
                self.pages[key] = content
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    def _portal(
        self, request: httpx.Request, key: tuple[str, str, str], page: str | None
    ) -> httpx.Response:
        assert key[1] in {a for _, a in self.assets} and key[2] in self.assets[key[:2]]
        if page is None and request.method == "GET":
            draft = self._drafts.setdefault(key, self.pages.get(key))
            body = json.loads(fixture("portal-draft.200.json"))
            # Captured: a version with a published Home page. Its pages without
            # one, and a status other than PUBLISHED for a draft with unpublished
            # edits, are this fake's own (not captured).
            if draft is None:
                body["pages"] = []
            if draft != self.pages.get(key):
                body["draftStatus"] = "UNPUBLISHED-EDITS"
            return httpx.Response(200, json=body)
        assert key in self._drafts, "page call before the draft was opened"
        if page == "/pages" and request.method == "POST":
            assert json.loads(request.content) == {"pagePath": "home"}
            if self._drafts[key] is not None:
                return httpx.Response(409, text=fixture("page.409.json"))
            self._drafts[key] = ""
            return httpx.Response(201, text=fixture("page.201.json"))
        if page == "/pages/home" and request.method == "PUT":
            if self.fail_pages:
                return httpx.Response(500, json={"message": "page write failed"})
            assert request.headers["content-type"] == "text/markdown"
            assert self._drafts[key] is not None, "PUT before the page was created"
            self._drafts[key] = request.content.decode()
            return httpx.Response(204)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {request.url}"})

    def _details(self, request: httpx.Request, key: tuple[str, str]) -> httpx.Response:
        assert key in self.assets, "PATCH of an asset that is not published"
        if self.fail_details:
            return httpx.Response(500, json={"message": "asset update failed"})
        changes = json.loads(request.content)
        assert changes and set(changes) <= {"name", "description"}
        self.details[key].update(changes)
        return httpx.Response(204)

    def _publish(
        self, request: httpx.Request, group: str, asset: str, version: str
    ) -> httpx.Response:
        assert request.headers["x-sync-publication"] == "true"
        if version in self.assets.get((group, asset), {}):
            return httpx.Response(409, text=fixture("publish.409.json"))
        fields, files = _multipart(request)
        kind = fields["type"]
        assert kind in ("mcp", "agent")
        self.details.setdefault(
            (group, asset), {"name": fields["name"], "description": fields["description"]}
        )
        parsed = {name: json.loads(body) for name, body in files.items()}
        self.assets.setdefault((group, asset), {})[version] = Published(
            fields.get("properties.ddkDigest"),
            parsed[f"files.{kind}-metadata.json"],
            kind,
            parsed,
            fields,
        )
        prefix = "agent." if kind == "agent" else ""
        return httpx.Response(201, text=fixture(f"{prefix}publish.201.json"))

    def _graphql(self, body: dict[str, Any]) -> httpx.Response:
        variables = body["variables"]
        versions = self.assets.get((variables["groupId"], variables["assetId"]))
        if not versions:
            return httpx.Response(200, text=fixture("graphql.absent.json"))
        version = variables.get("version") or list(versions)[-1]
        if version not in versions:
            return httpx.Response(200, text=fixture("graphql.absent.json"))
        published = versions[version]
        prefix = "agent." if published.type == "agent" else ""
        data = json.loads(fixture(f"{prefix}graphql.latest.json"))
        asset = data["data"]["asset"]
        group_name = "v" + ".".join(version.split(".")[:2])
        asset["version"], asset["versionGroup"] = version, group_name
        asset.update(self.details[(variables["groupId"], variables["assetId"])])
        for f in asset["files"]:
            f["externalLink"] = (
                f"https://{_POM_HOST}/presigned?g={variables['groupId']}"
                f"&a={variables['assetId']}&v={version}&p={f['packaging']}"
                f"&c={f['classifier'] or ''}"
            )
        key = (variables["groupId"], variables["assetId"], group_name)
        asset["instances"] = [{"endpointUri": e} for e in self.instances.get(key, [])]
        asset["otherVersions"] = [{"version": v} for v in versions if v != version]
        return httpx.Response(200, json=data)

    def _pom(self, request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        q = request.url.params
        published = self.assets[(q["g"], q["a"])][q["v"]]
        if q["c"] == "mcp-metadata" and q["p"] == "json":
            self.metadata_reads.append((q["g"], q["a"], q["v"]))
            return httpx.Response(200, json=published.metadata)
        pom = fixture("agent.pom.xml" if published.type == "agent" else "pom.xml")
        if published.digest is None:
            pom = re.sub(r"\s*<ddkDigest>.*</ddkDigest>", "", pom)
        else:
            pom = pom.replace(_CAPTURED_DIGEST, published.digest)
        return httpx.Response(200, text=pom)


def _multipart(request: httpx.Request) -> tuple[dict[str, str], dict[str, bytes]]:
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    fields: dict[str, str] = {}
    files: dict[str, bytes] = {}
    for part in request.content.split(b"--" + boundary)[1:-1]:
        head, _, value = part.strip(b"\r\n").partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head)
        assert name is not None
        if b"filename=" in head:
            files[name.group(1).decode()] = value
        else:
            fields[name.group(1).decode()] = value.decode()
    return fields, files
