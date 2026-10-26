"""A stateful fake Exchange for ``donkey publish`` tests (#218).

It answers the four calls the publisher makes (the multipart publish, the
GraphQL ``asset`` query, the POM download and the external-instance POST) by
replaying the bodies captured in ``tests/fixtures/anypoint/exchange_publish/``,
with the version, digest and instances of what has been published so far.
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
    digest: str | None
    metadata: dict[str, Any]


@dataclass
class FakeExchange:
    """Published versions per ``(group, asset)``, in publish order, plus the
    endpoints registered per version group. ``requests`` records every call."""

    assets: dict[tuple[str, str], dict[str, Published]] = field(default_factory=dict)
    instances: dict[tuple[str, str, str], list[str]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(lambda request: self.handle(request))

    def publish(self, group: str, asset: str, version: str, digest: str | None) -> None:
        self.assets.setdefault((group, asset), {})[version] = Published(digest, {})

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
            endpoint = json.loads(request.content)["endpointUri"]
            self.instances.setdefault(instance.groups(), []).append(endpoint)
            return httpx.Response(201, text=fixture("instance.201.json"))
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})

    def _publish(
        self, request: httpx.Request, group: str, asset: str, version: str
    ) -> httpx.Response:
        assert request.headers["x-sync-publication"] == "true"
        if version in self.assets.get((group, asset), {}):
            return httpx.Response(409, text=fixture("publish.409.json"))
        fields, files = _multipart(request)
        assert fields["type"] == "mcp"
        self.assets.setdefault((group, asset), {})[version] = Published(
            fields.get("properties.ddkDigest"), json.loads(files["files.mcp-metadata.json"])
        )
        return httpx.Response(201, text=fixture("publish.201.json"))

    def _graphql(self, body: dict[str, Any]) -> httpx.Response:
        variables = body["variables"]
        versions = self.assets.get((variables["groupId"], variables["assetId"]))
        if not versions:
            return httpx.Response(200, text=fixture("graphql.absent.json"))
        version = variables.get("version") or list(versions)[-1]
        if version not in versions:
            return httpx.Response(200, text=fixture("graphql.absent.json"))
        data = json.loads(fixture("graphql.latest.json"))
        asset = data["data"]["asset"]
        group_name = "v" + ".".join(version.split(".")[:2])
        asset["version"], asset["versionGroup"] = version, group_name
        for f in asset["files"]:
            f["externalLink"] = (
                f"https://{_POM_HOST}/presigned?g={variables['groupId']}"
                f"&a={variables['assetId']}&v={version}&p={f['packaging']}"
            )
        key = (variables["groupId"], variables["assetId"], group_name)
        asset["instances"] = [{"endpointUri": e} for e in self.instances.get(key, [])]
        asset["otherVersions"] = [{"version": v} for v in versions if v != version]
        return httpx.Response(200, json=data)

    def _pom(self, request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        q = request.url.params
        published = self.assets[(q["g"], q["a"])][q["v"]]
        pom = fixture("pom.xml")
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
