"""``Publication.verify()`` for an A2A agent: the card a version was published
with against the card its gateway instance serves (BG §2.5, #1354).

Every read replays ``tests/fixtures/anypoint/a2a_card/``: the version's files
and its stored ``a2a-card``, the API Manager and Gateway Manager reads
``resolve_agent()`` makes, and the gateway's answers for the live card with a
contracted credential, with none, and with an uncontracted one. The captured
live card had drifted from the published one (the agent was redeployed with a
longer skill description), which is what the end-to-end test reports.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError, PublicationDrift, RegistryError
from donkey_kit.core.runtime import Runtime
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.exchange import ExchangeRegistry
from donkey_kit.registry.models import AgentHandle, AssetRef
from donkey_kit.registry.publication import Publication, PublicationAssetType, card_drift
from donkey_kit.tools import discovery as discovery_module
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.discovery import Discovery

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_card"
BASE_URL = "https://anypoint.mulesoft.com"
ORG = "00000000-0000-4000-8000-4256cc8e6f68"
ENV = "00000000-0000-4000-8000-5c4fd1a49fc3"
GATEWAY = "00000000-0000-4000-8000-d4b0d8dad526"
ASSET = "ddk-langgraph-agent"
REF = f"{ORG}/{ASSET}/0.1.0"
ENDPOINT = "https://shared-omni-gateway.example.invalid/ddk-langgraph-agent/"
_LINK_HOST = "exchange-files.example.invalid"
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG, base_url=BASE_URL)
_APIM = f"/apimanager/api/v1/organizations/{ORG}/environments/{ENV}"
_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "ok")])
_UNGOVERNED = GovernanceReport(
    governed=False, checks=[Check("any_policy", False, "ddk-langgraph-agent has no policy")]
)
_ENVIRONMENTS = {
    "data": [
        {"id": "00000000-0000-4000-8000-99ab98e1ae5b", "name": "Design"},
        {"id": ENV, "name": "Sandbox"},
    ]
}


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json(name: str) -> Any:
    return json.loads(_text(name))


_PUBLISHED: dict[str, Any] = _json("a2a-card.stored.json")
_LIVE: dict[str, Any] = json.loads(_json("traffic.card.contracted.200.json")["body"])


class _Anypoint:
    """The control-plane reads, the pre-signed download and the gateway's card,
    recording every request. ``card`` names the ``traffic.card.*`` capture the
    gateway answers with."""

    def __init__(self, *, files: Any = None, card: str = "contracted.200") -> None:
        self.files = files if files is not None else _json("graphql.files.json")
        self.card = card
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, Any] = {
            f"/accounts/api/organizations/{ORG}/environments": _ENVIRONMENTS,
            f"{_APIM}/apis": _json("apis.by-asset.json"),
            f"{_APIM}/apis/21206128": _json("api.describe.json"),
            f"/gatewaymanager/api/v1/organizations/{ORG}/environments/{ENV}/gateways/{GATEWAY}": {
                "id": GATEWAY,
                "configuration": {
                    "ingress": {"publicUrl": "https://shared-omni-gateway.example.invalid"}
                },
            },
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == _LINK_HOST:
            return httpx.Response(200, text=_text("a2a-card.stored.json"))
        if request.url.host == "shared-omni-gateway.example.invalid":
            assert str(request.url) == ENDPOINT + ".well-known/agent-card.json"
            captured = _json(f"traffic.card.{self.card}.json")
            return httpx.Response(captured["status"], text=captured["body"])
        if request.url.path == "/graph/api/v2/graphql":
            variables = json.loads(request.content)["variables"]
            assert variables == {"groupId": ORG, "assetId": ASSET, "version": "0.1.0"}
            return httpx.Response(200, json=self.files)
        return httpx.Response(200, json=self.routes[request.url.path])

    def to(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


@pytest.fixture
async def runtime() -> AsyncIterator[Runtime]:
    """The real control-plane client, so its credential scoping is what is tested."""
    rt = Runtime(_CFG, auth=StaticToken("control-plane"))
    yield rt
    await rt.aclose()


class _Registry(ExchangeRegistry):
    """The real registry with a fixed ``explain()`` verdict: the policy reads it
    would make are ``test_explain.py``'s."""

    report: GovernanceReport = _GOVERNED

    async def explain(self, ref: AssetRef | str, *, criteria: Any) -> GovernanceReport:
        return self.report


def _wire(
    runtime: Runtime,
    monkeypatch: pytest.MonkeyPatch,
    fake: _Anypoint,
    report: GovernanceReport = _GOVERNED,
) -> tuple[_Registry, Discovery]:
    runtime.control_http.governed_transport.replace_inner(httpx.MockTransport(fake))
    registry = _Registry(_CFG, runtime.control_http)
    registry.report = report
    monkeypatch.setattr(
        discovery_module,
        "_card_client",
        lambda timeout_s: httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    return registry, Discovery(registry, _CFG, auth=ClientIdAuth("cid", "contracted"))


class _Donkey:
    def __init__(self, registry: ExchangeRegistry, discovery: Discovery) -> None:
        self.registry = registry
        self.tools = self
        self._discovery = discovery

    async def _agent_card(self, ref: str) -> Mapping[str, Any]:
        return await self._discovery.agent_card(ref)


# ---- the published side: registry.published_card() ------------------------------


async def test_the_published_card_is_the_stored_a2a_card(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Anypoint()
    registry, _ = _wire(runtime, monkeypatch, fake)

    assert await registry.published_card(REF) == _PUBLISHED
    (graph,) = fake.to("anypoint.mulesoft.com")
    (download,) = fake.to(_LINK_HOST)
    assert graph.headers["authorization"] == "Bearer control-plane"
    assert "authorization" not in download.headers


async def test_an_a2a_v1_card_is_read_before_a_legacy_one(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _json("graphql.files.json")
    legacy = files["data"]["asset"]["files"]
    legacy.append({**legacy[0], "classifier": "a2a-v1-card", "externalLink": "https://v1.invalid/"})
    fake = _Anypoint(files=files)
    registry, _ = _wire(runtime, monkeypatch, fake)

    with pytest.raises(KeyError):  # the fake answers no v1.invalid host: it was asked for it
        await registry.published_card(REF)
    assert fake.requests[-1].url.host == "v1.invalid"


async def test_a_version_without_a_card_file_is_a_registry_error(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _json("graphql.files.json")
    asset = files["data"]["asset"]
    asset["files"] = [f for f in asset["files"] if "a2a-card" not in str(f["classifier"])]
    registry, _ = _wire(runtime, monkeypatch, _Anypoint(files=files))

    with pytest.raises(RegistryError, match="no a2a-v1-card or a2a-card file"):
        await registry.published_card(REF)


# ---- resolve_agent() ----------------------------------------------------------


async def test_an_agent_resolves_to_its_gateway_path(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, _ = _wire(runtime, monkeypatch, _Anypoint())

    handle = await registry.resolve_agent(REF)

    assert handle == AgentHandle(ref=AssetRef.parse(REF), endpoint_url=ENDPOINT)


async def test_an_mcp_instance_does_not_resolve_as_an_agent(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Anypoint()
    for row in fake.routes[f"{_APIM}/apis"]["assets"]:
        for api in row["apis"]:
            api["endpointType"] = "mcp"
    registry, _ = _wire(runtime, monkeypatch, fake)

    with pytest.raises(RegistryError, match="no A2A instance deployed"):
        await registry.resolve_agent(REF)


# ---- the live side: the card through the gateway -----------------------------------


async def test_the_live_card_is_read_with_the_gateway_credential(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Anypoint()
    _, discovery = _wire(runtime, monkeypatch, fake)

    assert await discovery.agent_card(REF) == _LIVE
    (card,) = fake.to("shared-omni-gateway.example.invalid")
    assert card.headers["client_id"] == "cid"
    assert card.headers["client_secret"] == "contracted"
    assert "authorization" not in card.headers


@pytest.mark.parametrize("capture", ["no-client-id.401", "uncontracted.401"])
async def test_a_refused_credential_is_an_auth_error(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, capture: str
) -> None:
    _, discovery = _wire(runtime, monkeypatch, _Anypoint(card=capture))

    with pytest.raises(AuthError, match="HTTP 401"):
        await discovery.agent_card(REF)


async def test_an_ungoverned_agent_is_never_sent_the_credential(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Anypoint()
    _, discovery = _wire(runtime, monkeypatch, fake, _UNGOVERNED)

    with pytest.raises(RegistryError, match="not governed.*any_policy"):
        await discovery.agent_card(REF)
    assert fake.to("shared-omni-gateway.example.invalid") == []


# ---- the comparison: card_drift() ---------------------------------------------------


def test_an_identical_card_does_not_drift() -> None:
    assert card_drift(_PUBLISHED, _PUBLISHED) == ()


def test_where_and_how_the_agent_is_served_is_not_drift() -> None:
    # The captured cards differ in url, supportedInterfaces, version and the
    # upstream's security fields; only the skill description is drift.
    assert {"url", "supportedInterfaces", "version", "securitySchemes"} <= {
        k for k in _LIVE if _LIVE[k] != _PUBLISHED.get(k)
    }
    assert card_drift(_PUBLISHED, _LIVE) == ("skill chat: the description changed",)


def test_every_difference_is_reported() -> None:
    published = {
        "description": "Triage questions.",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{"id": "a", "description": "Does a."}, {"id": "b"}],
    }
    live = {
        "description": "Triage questions.\n",
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": [],
        "skills": [{"id": "a", "description": "Does A now."}, {"id": "c"}],
    }

    assert card_drift(published, live) == (
        "the default input modes changed (+application/json)",
        "the default output modes changed (-text/plain)",
        "skill a: the description changed",
        "skill b was published but the agent no longer serves it",
        "skill c is live but was not published",
    )


# ---- verify() -----------------------------------------------------------------------


def _publication(asset_type: PublicationAssetType = PublicationAssetType.AGENT) -> Publication:
    return Publication(
        asset_type=asset_type,
        group_id=ORG,
        asset_id=ASSET,
        version="0.1.0",
        name=ASSET,
        description="A LangGraph agent.",
    )


@pytest.mark.parametrize("asset_type", [PublicationAssetType.AGENT, PublicationAssetType.A2A_AGENT])
async def test_the_captured_drift_is_reported_end_to_end(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, asset_type: PublicationAssetType
) -> None:
    donkey = _Donkey(*_wire(runtime, monkeypatch, _Anypoint()))

    assert await _publication(asset_type).verify(donkey) == ("skill chat: the description changed",)


async def test_an_added_skill_raises_publication_drift(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _Anypoint()
    donkey = _Donkey(*_wire(runtime, monkeypatch, fake))
    added = {**_PUBLISHED, "skills": [*_PUBLISHED["skills"], {"id": "refund"}]}

    async def live(ref: str) -> Mapping[str, Any]:
        return added

    monkeypatch.setattr(donkey, "_agent_card", live)

    with pytest.raises(PublicationDrift, match="skill refund is live but was not published"):
        await _publication().verify(donkey, raise_on_drift=True)
