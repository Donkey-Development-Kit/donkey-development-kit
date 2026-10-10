"""Each credential stays on its own plane (BG §1.1).

The Anypoint connected-app token authenticates control-plane calls (registry,
tools, platform APIs); the LLM proxy authenticates data-plane calls with the
``client_id``/``client_secret`` pair, or — in ``jwt`` mode — with the wallet JWT
from ``Donkey(llm_auth=...)``. These tests pin that a ``Donkey`` never mixes them:
no data-plane request fetches or carries the control-plane token, a data-plane
401 never refreshes it, and control-plane calls never carry the wallet JWT.

Driven through ``donkey.openai()`` and the shared data-plane client with
``httpx.MockTransport``, so no network is touched. The control plane is reached
through ``donkey.registry``'s client, since every registry method is still
verification-blocked.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig

_PROXY = "https://gw.example.internal/openai-sdk/"
_CONTROL_PLANE = "https://anypoint.example.internal"
_CONTROL_PLANE_TOKEN = "cp-token-123"
_WALLET_JWT = "wallet-jwt-456"

_CANNED_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hi there"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


def _client_id_cfg() -> DonkeyConfig:
    """Default client-id mode with connected-app credentials also configured."""
    return DonkeyConfig(
        client_id="cp-id",
        client_secret="cp-secret",
        base_url=_CONTROL_PLANE,
        llm_proxy_url=_PROXY,
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        correlation_header="x-correlation-id",
        call_id_header="x-donkey-request-id",
    )


def _jwt_cfg() -> DonkeyConfig:
    """Model-wallet mode with connected-app credentials also configured."""
    return DonkeyConfig(
        client_id="cp-id",
        client_secret="cp-secret",
        base_url=_CONTROL_PLANE,
        llm_proxy_url=_PROXY,
        llm_proxy_auth="jwt",
        llm_proxy_wallet_client_id="wallet-42",
        correlation_header="x-correlation-id",
        call_id_header="x-donkey-request-id",
    )


class _TokenEndpoint:
    """Stands in for the Anypoint token endpoint behind the connected-app provider,
    recording every request it receives."""

    def __init__(self, status: int = 200, *, unreachable: bool = False) -> None:
        self.status = status
        self.unreachable = unreachable
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("token endpoint unreachable", request=request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_client"})
        return httpx.Response(200, json={"access_token": _CONTROL_PLANE_TOKEN, "expires_in": 3600})


class _Recorder:
    """A data-plane (or control-plane) upstream that records each request and
    answers with ``status`` (a canned chat completion on 200)."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status == 200:
            return httpx.Response(200, json=_CANNED_COMPLETION)
        return httpx.Response(self.status)


class _CountingProvider:
    """An AuthProvider that counts ``token()`` and ``invalidate()`` calls and
    rotates its token on invalidation, like a real IdP refresh."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.generation = 1
        self.token_calls = 0
        self.invalidations = 0

    async def token(self) -> str:
        self.token_calls += 1
        return f"{self.prefix}-{self.generation}"

    async def invalidate(self) -> None:
        self.invalidations += 1
        self.generation += 1


def _wire(donkey: Donkey, token_endpoint: _TokenEndpoint, data_plane: _Recorder) -> None:
    """Point the connected-app token fetches and the data-plane client at mocks."""
    assert donkey._owned_auth_http is not None
    donkey._owned_auth_http.governed_transport.replace_inner(httpx.MockTransport(token_endpoint))
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(data_plane))


def _assert_no_control_plane_token(request: httpx.Request) -> None:
    assert _CONTROL_PLANE_TOKEN not in request.headers.get("authorization", "")


# --- client-id mode: the data plane never touches the control-plane token ----


async def test_openai_call_neither_fetches_nor_sends_the_control_plane_token() -> None:
    pytest.importorskip("openai")
    token_endpoint, upstream = _TokenEndpoint(), _Recorder()
    async with Donkey(_client_id_cfg()) as donkey:
        _wire(donkey, token_endpoint, upstream)
        resp = await donkey.openai().chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

    assert resp.choices[0].message.content == "hi there"
    assert token_endpoint.requests == []
    (request,) = upstream.requests
    _assert_no_control_plane_token(request)
    assert request.headers["client_id"] == "cid"
    assert request.headers["client_secret"] == "csecret"


async def test_llm_client_call_neither_fetches_nor_sends_the_control_plane_token() -> None:
    pytest.importorskip("openai")
    token_endpoint, upstream = _TokenEndpoint(), _Recorder()
    async with Donkey(_client_id_cfg()) as donkey:
        _wire(donkey, token_endpoint, upstream)
        await donkey.llm.client().chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

    assert token_endpoint.requests == []
    _assert_no_control_plane_token(upstream.requests[0])


async def test_anthropic_shaped_request_carries_no_authorization() -> None:
    """The Anthropic SDK authenticates with ``x-api-key`` and sets no
    Authorization header, so the transport must not add one."""
    token_endpoint, upstream = _TokenEndpoint(), _Recorder()
    async with Donkey(_client_id_cfg()) as donkey:
        _wire(donkey, token_endpoint, upstream)
        await donkey._http.post(
            f"{_PROXY}v1/messages",
            headers={"x-api-key": "sk-ant-sentinel", "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-sonnet-4",
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    (request,) = upstream.requests
    assert request.headers.get("authorization") is None
    assert token_endpoint.requests == []
    assert request.headers["x-api-key"] == "sk-ant-sentinel"


async def test_gemini_shaped_request_carries_no_authorization() -> None:
    """google-genai authenticates with ``x-goog-api-key`` and sets no
    Authorization header, so the transport must not add one."""
    token_endpoint, upstream = _TokenEndpoint(), _Recorder()
    async with Donkey(_client_id_cfg()) as donkey:
        _wire(donkey, token_endpoint, upstream)
        await donkey._http.post(
            f"{_PROXY}models/gemini-2.5-flash:generateContent",
            headers={"x-goog-api-key": "goog-sentinel"},
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )

    (request,) = upstream.requests
    assert request.headers.get("authorization") is None
    assert token_endpoint.requests == []
    assert request.headers["x-goog-api-key"] == "goog-sentinel"


async def test_raw_data_plane_request_carries_no_authorization() -> None:
    token_endpoint, upstream = _TokenEndpoint(), _Recorder()
    async with Donkey(_client_id_cfg()) as donkey:
        _wire(donkey, token_endpoint, upstream)
        await donkey._http.post(f"{_PROXY}responses", json={"model": "gpt-4o", "input": "hi"})

    (request,) = upstream.requests
    assert request.headers.get("authorization") is None
    assert token_endpoint.requests == []


# --- client-id mode: LLM calls do not depend on the control plane ------------


@pytest.mark.parametrize(
    "token_endpoint",
    [_TokenEndpoint(401), _TokenEndpoint(503), _TokenEndpoint(unreachable=True)],
    ids=["token-401", "token-503", "token-unreachable"],
)
async def test_llm_call_succeeds_when_the_control_plane_token_endpoint_fails(
    token_endpoint: _TokenEndpoint,
) -> None:
    pytest.importorskip("openai")
    token_endpoint.requests.clear()
    upstream = _Recorder()
    async with Donkey(_client_id_cfg()) as donkey:
        _wire(donkey, token_endpoint, upstream)
        resp = await donkey.openai().chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )

    assert resp.choices[0].message.content == "hi there"
    assert len(upstream.requests) == 1


# --- data-plane 401s refresh only the data-plane credential ------------------


async def test_client_id_data_plane_401_leaves_the_control_plane_provider_alone() -> None:
    control = _CountingProvider("cp")
    upstream = _Recorder(401)
    async with Donkey(_client_id_cfg(), auth=control) as donkey:
        donkey._http.governed_transport.replace_inner(httpx.MockTransport(upstream))
        resp = await donkey._http.post(f"{_PROXY}responses", json={"model": "gpt-4o"})

    assert resp.status_code == 401
    assert control.invalidations == 0
    assert control.token_calls == 0
    assert len(upstream.requests) == 1  # a client-id 401 is terminal: no re-send


async def test_jwt_data_plane_401_refreshes_the_wallet_jwt_only() -> None:
    control = _CountingProvider("cp")
    wallet = _CountingProvider("wallet")
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(401) if len(seen) == 1 else httpx.Response(200)

    async with Donkey(_jwt_cfg(), auth=control, llm_auth=wallet) as donkey:
        donkey._http.governed_transport.replace_inner(httpx.MockTransport(upstream))
        resp = await donkey._http.post(f"{_PROXY}responses", json={"model": "gpt-4o"})

    assert resp.status_code == 200
    assert seen == ["Bearer wallet-1", "Bearer wallet-2"]
    assert wallet.invalidations == 1
    assert control.invalidations == 0
    assert control.token_calls == 0


# --- the control plane keeps the connected-app token -------------------------


async def test_control_plane_request_carries_the_connected_app_token_in_client_id_mode() -> None:
    token_endpoint, platform = _TokenEndpoint(), _Recorder()
    async with Donkey(_client_id_cfg()) as donkey:
        assert donkey._owned_auth_http is not None
        donkey._owned_auth_http.governed_transport.replace_inner(
            httpx.MockTransport(token_endpoint)
        )
        donkey.registry._http.governed_transport.replace_inner(httpx.MockTransport(platform))
        await donkey.registry._http.get(f"{_CONTROL_PLANE}/exchange/api/v2/assets")

    assert len(token_endpoint.requests) == 1
    (request,) = platform.requests
    assert request.headers["authorization"] == f"Bearer {_CONTROL_PLANE_TOKEN}"


async def test_control_plane_request_carries_the_connected_app_token_in_jwt_mode() -> None:
    token_endpoint, platform = _TokenEndpoint(), _Recorder()
    async with Donkey(_jwt_cfg(), llm_auth=StaticToken(_WALLET_JWT)) as donkey:
        assert donkey._owned_auth_http is not None
        donkey._owned_auth_http.governed_transport.replace_inner(
            httpx.MockTransport(token_endpoint)
        )
        donkey.registry._http.governed_transport.replace_inner(httpx.MockTransport(platform))
        await donkey.registry._http.get(f"{_CONTROL_PLANE}/exchange/api/v2/assets")

    (request,) = platform.requests
    assert request.headers["authorization"] == f"Bearer {_CONTROL_PLANE_TOKEN}"
    assert _WALLET_JWT not in str(request.headers)
    assert "x-client-id" not in request.headers  # the wallet selector is data-plane only


async def test_connected_app_token_request_carries_no_data_plane_credentials() -> None:
    token_endpoint = _TokenEndpoint()
    async with Donkey(_jwt_cfg(), llm_auth=StaticToken(_WALLET_JWT)) as donkey:
        assert donkey._owned_auth_http is not None and donkey._auth is not None
        donkey._owned_auth_http.governed_transport.replace_inner(
            httpx.MockTransport(token_endpoint)
        )
        assert await donkey._auth.token() == _CONTROL_PLANE_TOKEN

    (token_request,) = token_endpoint.requests
    assert token_request.headers.get("authorization") is None
    assert "x-client-id" not in token_request.headers


async def test_jwt_data_plane_request_carries_the_wallet_jwt_not_the_platform_token() -> None:
    token_endpoint, upstream = _TokenEndpoint(), _Recorder()
    async with Donkey(_jwt_cfg(), llm_auth=StaticToken(_WALLET_JWT)) as donkey:
        _wire(donkey, token_endpoint, upstream)
        await donkey._http.post(f"{_PROXY}responses", json={"model": "gpt-4o"})

    assert token_endpoint.requests == []
    (request,) = upstream.requests
    assert request.headers["authorization"] == f"Bearer {_WALLET_JWT}"
    assert request.headers["x-client-id"] == "wallet-42"


# --- lifecycle ---------------------------------------------------------------


async def test_aclose_closes_the_control_plane_client() -> None:
    donkey = Donkey(_client_id_cfg())
    control_plane = donkey.registry._http

    await donkey.aclose()

    assert control_plane.is_closed
    assert donkey._http.is_closed
