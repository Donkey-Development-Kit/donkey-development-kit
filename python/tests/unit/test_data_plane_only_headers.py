"""LLM-proxy-only headers stay on the data plane (BG §1.1, #833).

The attribution (application, business group), cost-tag and ``x-cache-*``
semantic-cache headers are meant for the LLM proxy (docs/verified-apis.md §3);
no Anypoint platform API reads them (docs/verified-apis.md §12.2). These tests
pin that a registry call and the connected-app token request carry none of
them, while a model call carries all of them. The correlation and per-call IDs
go on both planes.

Driven with ``httpx.MockTransport``, so no network is touched.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import _verify
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.cost import CostTags


@pytest.fixture(autouse=True)
def _isolate_unverified_warnings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The attribution header names are UNVERIFIED and warn once per process;
    keep this module from using up that warning for later tests."""
    monkeypatch.setattr(_verify, "_warned", set())


_PROXY = "https://gw.example.internal/openai-sdk/"
_CONTROL_PLANE = "https://anypoint.example.internal"

#: Marker values for every LLM-proxy-only header; none may reach the control plane.
_APP = "app-marker"
_GROUP = "group-marker"
_RUN_TEAM = "run-team-marker"
_CFG_PROJECT = "cfg-project-marker"
_ENDUSER = "enduser-marker"
_PRINCIPAL = "principal-marker"
_MARKERS = {_APP, _GROUP, _RUN_TEAM, _CFG_PROJECT, _ENDUSER, _PRINCIPAL}


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        client_id="cp-id",
        client_secret="cp-secret",
        base_url=_CONTROL_PLANE,
        llm_proxy_url=_PROXY,
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        correlation_header="x-correlation-id",
        call_id_header="x-donkey-request-id",
        application_name=_APP,
        business_group=_GROUP,
        cost=CostTags(project=_CFG_PROJECT),
        send_cost_headers=True,
    )


class _Recorder:
    """Records each request; answers like the token endpoint (harmless elsewhere)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"access_token": "cp-token", "expires_in": 3600})


def _proxy_only_headers(request: httpx.Request) -> dict[str, str]:
    """The headers on ``request`` that only the LLM proxy reads."""
    return {
        name: value
        for name, value in request.headers.items()
        if value in _MARKERS or name.startswith("x-cache-") or name.startswith("x-anypoint-cost-")
    }


async def _send_all() -> tuple[httpx.Request, httpx.Request, httpx.Request]:
    """One model call, one registry call and one token request, inside a run
    with a cost override and a cache principal. Returns them in that order."""
    token_endpoint, platform, proxy = _Recorder(), _Recorder(), _Recorder()
    async with Donkey(_cfg()) as donkey:
        assert donkey._owned_auth_http is not None
        donkey._owned_auth_http._swap_transport(httpx.MockTransport(token_endpoint))
        donkey.registry._http._swap_transport(httpx.MockTransport(platform))
        donkey._http._swap_transport(httpx.MockTransport(proxy))
        async with donkey.run(team=_RUN_TEAM, enduser_id=_ENDUSER):
            with donkey.cache(skip=True, principal_id=_PRINCIPAL):
                await donkey._http.post(f"{_PROXY}chat/completions", json={"model": "gpt-4o"})
                await donkey.registry._http.get(f"{_CONTROL_PLANE}/exchange/api/v2/assets")

    (model,) = proxy.requests
    (registry,) = platform.requests
    (token,) = token_endpoint.requests
    return model, registry, token


async def test_model_call_carries_every_proxy_only_header() -> None:
    model, _, _ = await _send_all()

    assert set(_proxy_only_headers(model).values()) >= _MARKERS
    assert model.headers["x-anypoint-cost-team"] == _RUN_TEAM
    assert model.headers["x-anypoint-cost-project"] == _CFG_PROJECT
    assert model.headers["x-anypoint-cost-enduser-id"] == _ENDUSER
    assert model.headers["x-cache-principal-id"] == _PRINCIPAL


async def test_registry_call_carries_no_proxy_only_header() -> None:
    _, registry, _ = await _send_all()

    assert _proxy_only_headers(registry) == {}
    assert registry.headers["x-correlation-id"]
    assert registry.headers["x-donkey-request-id"]


async def test_token_request_carries_no_proxy_only_header() -> None:
    _, _, token = await _send_all()

    assert _proxy_only_headers(token) == {}
    assert token.headers["x-correlation-id"]


async def test_sync_data_plane_client_carries_every_proxy_only_header() -> None:
    proxy = _Recorder()
    async with Donkey(_cfg()) as donkey:
        client = donkey._sync_http_client()
        client._swap_transport(httpx.MockTransport(proxy))
        async with donkey.run(team=_RUN_TEAM, enduser_id=_ENDUSER):
            with donkey.cache(principal_id=_PRINCIPAL):
                client.post(f"{_PROXY}chat/completions", json={"model": "gpt-4o"})

    (model,) = proxy.requests
    assert set(_proxy_only_headers(model).values()) >= _MARKERS
