"""``Publication.verify()``: the published ``mcp-metadata`` tools against the live
server's ``tools/list`` (BG §2.5, #991).

The published side replays the captures in
``tests/fixtures/anypoint/exchange_publish/`` (``graphql.files.json``, the
version's files, and ``mcp-metadata.stored.json``, the file downloaded from its
pre-signed link). The live side is a ``ToolSet`` whose ``list_tools`` is
replaced, as in ``test_discover.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from _fake_exchange import BASE_URL, ORG, fixture

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import PublicationDrift, RegistryError
from donkey_kit.core.runtime import Runtime
from donkey_kit.donkey import ToolsFacade
from donkey_kit.registry import publication as publication_module
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.exchange import ExchangeRegistry
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.registry.publication import Publication, PublicationAssetType, tool_drift
from donkey_kit.tools import session as session_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.session import ToolSet, UngovernedMcpServer

ASSET = "ddk-verify-991-fixture"
REF = f"{ORG}/{ASSET}/1.1.0"
_LINK_HOST = "exchange-files.example.invalid"
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG, base_url=BASE_URL)
_PUBLISHED: list[dict[str, Any]] = json.loads(fixture("mcp-metadata.stored.json"))["tools"]
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])
_UNGOVERNED = GovernanceReport(
    governed=False, checks=[Check("client_id_enforcement", False, "no policy")]
)


@dataclass
class _Exchange:
    """The files query and the pre-signed download, recording every request."""

    files: str = field(default_factory=lambda: fixture("graphql.files.json"))
    link: str = f"https://{_LINK_HOST}/presigned"
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == _LINK_HOST:
            return httpx.Response(200, text=fixture("mcp-metadata.stored.json"))
        assert request.url.path == "/graph/api/v2/graphql"
        variables = json.loads(request.content)["variables"]
        if variables != {"groupId": ORG, "assetId": ASSET, "version": "1.1.0"}:
            return httpx.Response(200, text=fixture("graphql.absent.json"))
        body = json.loads(self.files)
        for f in body["data"]["asset"]["files"]:
            f["externalLink"] = self.link
        return httpx.Response(200, json=body)


@pytest.fixture
async def runtime() -> AsyncIterator[Runtime]:
    """The real control-plane client, so its credential scoping is what is tested."""
    rt = Runtime(_CFG, auth=StaticToken("control-plane"))
    yield rt
    await rt.aclose()


def _registry(runtime: Runtime, exchange: _Exchange) -> ExchangeRegistry:
    runtime.control_http.governed_transport.replace_inner(httpx.MockTransport(exchange.handle))
    return ExchangeRegistry(_CFG, runtime.control_http)


# ---- the published side: registry.published_tools() ------------------------------


async def test_published_tools_are_the_stored_mcp_metadata_tools(runtime: Runtime) -> None:
    exchange = _Exchange()
    tools = await _registry(runtime, exchange).published_tools(REF)

    assert tools == _PUBLISHED
    assert [t["name"] for t in tools] == ["lookup_sku"]


async def test_the_control_plane_credential_never_goes_to_the_presigned_link(
    runtime: Runtime,
) -> None:
    exchange = _Exchange()
    await _registry(runtime, exchange).published_tools(REF)

    graph, download = exchange.requests
    assert graph.headers["authorization"] == "Bearer control-plane"
    assert download.url.host == _LINK_HOST
    assert "authorization" not in download.headers


async def test_an_absent_version_is_a_registry_error(runtime: Runtime) -> None:
    with pytest.raises(RegistryError, match="no asset version"):
        await _registry(runtime, _Exchange()).published_tools(f"{ORG}/{ASSET}/9.9.9")


async def test_a_version_without_an_mcp_metadata_file_is_a_registry_error(runtime: Runtime) -> None:
    body = json.loads(fixture("graphql.files.json"))
    asset = body["data"]["asset"]
    asset["files"] = [f for f in asset["files"] if f["classifier"] != "mcp-metadata"]
    exchange = _Exchange(files=json.dumps(body))

    with pytest.raises(RegistryError, match="no mcp-metadata file"):
        await _registry(runtime, exchange).published_tools(REF)


async def test_a_non_https_link_is_never_followed(runtime: Runtime) -> None:
    exchange = _Exchange(link=f"http://{_LINK_HOST}/presigned")

    with pytest.raises(RegistryError, match="non-https"):
        await _registry(runtime, exchange).published_tools(REF)
    assert len(exchange.requests) == 1


# ---- the comparison: tool_drift() -------------------------------------------------


def _tool(
    name: str, description: str | None = "Does a thing well.", *params: str
) -> dict[str, Any]:
    tool: dict[str, Any] = {
        "name": name,
        "inputSchema": {"type": "object", "properties": {p: {"type": "string"} for p in params}},
    }
    if description is not None:
        tool["description"] = description
    return tool


def test_identical_tools_do_not_drift() -> None:
    assert tool_drift(_PUBLISHED, _PUBLISHED) == ()


def test_parameter_types_and_whitespace_are_not_drift() -> None:
    live = [
        {
            "name": "lookup_sku",
            "description": _PUBLISHED[0]["description"] + "\n",
            "inputSchema": {
                "type": "object",
                "properties": {"sku": {"type": "string", "title": "Sku"}},
                "required": ["sku"],
            },
        }
    ]
    assert tool_drift(_PUBLISHED, live) == ()


def test_every_difference_is_reported_in_name_order() -> None:
    published = [_tool("a", None, "x"), _tool("b"), _tool("c", "Old words here.", "x", "y")]
    live = [_tool("a", "", "x"), _tool("d"), _tool("c", "New words here.", "x", "z")]

    assert tool_drift(published, live) == (
        "tool b was published but the server no longer lists it",
        "tool c: the description changed",
        "tool c: the parameters changed (+z, -y)",
        "tool d is live but was not published",
    )


# ---- verify() ---------------------------------------------------------------------


def _donkey(
    runtime: Runtime,
    exchange: _Exchange,
    monkeypatch: pytest.MonkeyPatch,
    report: GovernanceReport = _GOVERNED,
) -> tuple[SimpleNamespace, list[str]]:
    """A ``Donkey`` stand-in: the real registry and the real ``ToolsFacade``,
    whose discovery resolves ``ref`` to one server with ``report``."""
    registry = _registry(runtime, exchange)
    tools = ToolsFacade(registry, _CFG, None)
    resolved: list[str] = []

    async def resolve(ref: str, *, auth: Any) -> ToolSet:
        resolved.append(ref)
        handle = McpServerHandle(
            ref=AssetRef.parse(ref), endpoint_url="https://gw.example.test/sku/", governance=report
        )
        return ToolSet([handle], _auth=ClientIdAuth("cid", "secret"))

    monkeypatch.setattr(tools._discovery, "resolve", resolve)
    return SimpleNamespace(registry=registry, tools=tools), resolved


def _live(monkeypatch: pytest.MonkeyPatch, tools: list[dict[str, Any]]) -> list[Mapping[str, str]]:
    seen: list[Mapping[str, str]] = []

    async def fake_list(url: str, headers: Mapping[str, str], *, server: str) -> Any:
        seen.append(headers)
        return tuple(tools)

    monkeypatch.setattr(session_module, "list_tools", fake_list)
    return seen


def _publication(asset_type: PublicationAssetType = PublicationAssetType.MCP_SERVER) -> Publication:
    return Publication(
        asset_type=asset_type,
        group_id=ORG,
        asset_id=ASSET,
        version="1.1.0",
        name="SKU lookup",
        description="Looks up products.",
    )


async def test_an_unchanged_server_verifies_clean(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    seen = _live(monkeypatch, _PUBLISHED)
    donkey, resolved = _donkey(runtime, _Exchange(), monkeypatch)

    with caplog.at_level(logging.WARNING, logger=publication_module.__name__):
        assert await _publication().verify(donkey) == ()
    assert resolved == [REF]
    assert seen == [{"client_id": "cid", "client_secret": "secret"}]
    assert not caplog.records


async def test_an_added_tool_warns_by_default(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _live(monkeypatch, [*_PUBLISHED, _tool("delete_sku", "Deletes a product by SKU.", "sku")])
    donkey, _ = _donkey(runtime, _Exchange(), monkeypatch)

    with caplog.at_level(logging.WARNING, logger=publication_module.__name__):
        differences = await _publication().verify(donkey)
    assert differences == ("tool delete_sku is live but was not published",)
    (record,) = caplog.records
    assert record.getMessage() == (
        f"Publication drift on {REF}: tool delete_sku is live but was not published"
    )


async def test_an_added_tool_on_the_live_server_raises_publication_drift(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # publication_verify_drift (#991): a tool the server added since its
    # version was published is drift.
    _live(monkeypatch, [*_PUBLISHED, _tool("delete_sku", "Deletes a product by SKU.", "sku")])
    donkey, _ = _donkey(runtime, _Exchange(), monkeypatch)

    with pytest.raises(PublicationDrift, match="no longer matches its Exchange descriptor") as info:
        await _publication().verify(donkey, raise_on_drift=True)
    assert str(info.value).endswith(": tool delete_sku is live but was not published")
    assert "Republish the descriptor" in info.value.remediation


async def test_an_ungoverned_server_is_never_sent_the_credential(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _live(monkeypatch, _PUBLISHED)
    donkey, _ = _donkey(runtime, _Exchange(), monkeypatch, _UNGOVERNED)

    with pytest.raises(UngovernedMcpServer):
        await _publication().verify(donkey)
    assert seen == []


async def test_an_api_asset_is_not_verified(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    donkey, _ = _donkey(runtime, _Exchange(), monkeypatch)

    with pytest.raises(NotImplementedError, match=r"^blocked on verification:.*'api'"):
        await _publication(PublicationAssetType.API).verify(donkey)
