"""On-behalf-of identity: ``donkey.as_user(token=...)`` (BG §2.4, #215, #216, #1350, #1420).

The user's token is forwarded unchanged: no token exchange, no control-plane
request, no connected-app credentials needed (the gateway's OBO policy runs
the exchange). Covers the contextvar scope (task isolation, nesting), where the
token rides (data-plane sends on both clients, never the control plane or an
unchecked origin), and the hard failure (#216): a missing user token is an
``AuthError``, never a request sent as the service identity.

Driven with ``httpx.MockTransport``, so no network is touched.
"""

from __future__ import annotations

import asyncio
import dataclasses

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import config
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError, ConfigError
from donkey_kit.core.header_names import user_token_header_problem
from donkey_kit.core.identity import UserScope, current_user
from donkey_kit.core.transport.headers import PROXY_API_KEY_SENTINEL

_PROXY = "https://gw.example.internal/openai-sdk/"
_CONTROL_PLANE = "https://anypoint.example.internal"
_TOKEN = "token-alice"
_SENTINEL = f"Bearer {PROXY_API_KEY_SENTINEL}"


def _cfg(**kw: object) -> DonkeyConfig:
    base: dict[str, object] = {
        "client_id": "cp-id",
        "client_secret": "cp-secret-value",
        "base_url": _CONTROL_PLANE,
        "llm_proxy_url": _PROXY,
        "llm_proxy_client_id": "cid",
        "llm_proxy_client_secret": "csecret",
    }
    base.update(kw)
    return DonkeyConfig(**base)  # type: ignore[arg-type]


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"access_token": "cp-token", "expires_in": 3600})


def _wire(donkey: Donkey) -> _Recorder:
    """Point the data plane at a recorder."""
    proxy = _Recorder()
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(proxy))
    return proxy


def _wire_platform(donkey: Donkey) -> _Recorder:
    """Point the control plane and the connected-app token fetch at a recorder."""
    platform = _Recorder()
    donkey._runtime.control_http.governed_transport.replace_inner(httpx.MockTransport(platform))
    if donkey._owned_auth_http is not None:
        donkey._owned_auth_http.governed_transport.replace_inner(httpx.MockTransport(platform))
    return platform


async def _model_call(donkey: Donkey, url: str = f"{_PROXY}chat/completions") -> None:
    # The OpenAI SDK pre-sets its api_key slot as the Authorization bearer.
    await donkey._http.post(url, json={"model": "m"}, headers={"Authorization": _SENTINEL})


# --- config checks ------------------------------------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"llm_proxy_auth": "jwt", "llm_proxy_wallet_client_id": "w"},
        {"llm_proxy_auth": "bearer"},
    ],
)
def test_a_token_mode_refuses_the_authorization_header(kw: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match="user_token_header"):
        UserScope(_cfg(**kw), _TOKEN)


def test_a_token_mode_with_a_custom_user_header_is_allowed() -> None:
    UserScope(_cfg(llm_proxy_auth="bearer", user_token_header="X-User-Token"), _TOKEN)


def test_user_token_header_names() -> None:
    assert user_token_header_problem("Authorization") is None
    assert user_token_header_problem("X-User-Token") is None
    for name in ("client_id", "client_secret", "X-Client-Id", "cookie", "apikey"):
        assert user_token_header_problem(name) == "it carries the consumer's own credentials"
    assert user_token_header_problem("content-type") == "the SDK already sets it"
    assert user_token_header_problem("host") is not None
    with pytest.raises(ConfigError, match="user_token_header"):
        _cfg(user_token_header="client_secret")
    with pytest.raises(ConfigError, match="already uses it"):
        _cfg(user_token_header="X-Key", llm_proxy_key_header="X-Key")


async def test_as_user_takes_the_token_by_keyword_only() -> None:
    """``token=`` is the only spelling (#1420): ``id_token=`` never shipped in a
    final release, so it has no alias."""
    async with Donkey(_cfg()) as donkey:
        with pytest.raises(TypeError, match="id_token"):
            donkey.as_user(id_token=_TOKEN)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            donkey.as_user(_TOKEN)  # type: ignore[misc]


def test_there_is_no_token_exchange_setting() -> None:
    """The SDK runs no exchange, so nothing configures one (#1350, #1420)."""
    names = {field.name for field in dataclasses.fields(DonkeyConfig)}
    envs = {spec.env for spec in config._FIELDS}
    assert "token_exchange_path" not in names
    assert "DONKEY_TOKEN_EXCHANGE_PATH" not in envs


# --- the scope and the transport ----------------------------------------------


async def test_a_model_call_in_the_scope_carries_the_user_token_unchanged() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy, platform = _wire(donkey), _wire_platform(donkey)
        await _model_call(donkey)
        async with donkey.as_user(token=_TOKEN):
            await _model_call(donkey)
        await _model_call(donkey)
    before, inside, after = proxy.requests
    assert before.headers["Authorization"] == _SENTINEL
    # The sentinel is overridden: yielding to it would send the service identity.
    assert inside.headers["Authorization"] == f"Bearer {_TOKEN}"
    assert after.headers["Authorization"] == _SENTINEL
    # No exchange: the scope makes no control-plane request for the user.
    assert platform.requests == []


async def test_as_user_needs_no_connected_app_credentials() -> None:
    async with Donkey(_cfg(client_id=None, client_secret=None)) as donkey:
        proxy = _wire(donkey)
        async with donkey.as_user(token=_TOKEN):
            await _model_call(donkey)
    assert proxy.requests[0].headers["Authorization"] == f"Bearer {_TOKEN}"


async def test_a_custom_user_token_header_carries_the_raw_token() -> None:
    async with Donkey(_cfg(user_token_header="X-User-Token")) as donkey:
        proxy = _wire(donkey)
        async with donkey.as_user(token=_TOKEN):
            await _model_call(donkey)
    (request,) = proxy.requests
    assert request.headers["X-User-Token"] == _TOKEN
    assert request.headers["Authorization"] == _SENTINEL


async def test_the_user_token_stays_off_the_control_plane_and_unchecked_origins() -> None:
    async with Donkey(_cfg(user_token_header="X-User-Token")) as donkey:
        platform = _wire_platform(donkey)
        elsewhere = _wire(donkey)
        async with donkey.as_user(token=_TOKEN):
            await donkey._runtime.control_http.get(f"{_CONTROL_PLANE}/accounts/api/me")
            await donkey._http.post(
                "https://elsewhere.example/v1/chat", json={}, headers={"X-User-Token": "planted"}
            )
    for request in [*platform.requests, *elsewhere.requests]:
        assert "x-user-token" not in request.headers
        assert _TOKEN not in str(request.headers)


async def test_concurrent_tasks_never_see_each_others_user() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def as_user(token: str) -> None:
            async with donkey.as_user(token=token):
                entered.set()
                await release.wait()
                await donkey._http.post(
                    f"{_PROXY}chat", json={"model": "m"}, headers={"X-Who": token}
                )

        async def anonymous() -> None:
            await entered.wait()
            await donkey._http.post(f"{_PROXY}chat", json={"model": "m"}, headers={"X-Who": "-"})
            release.set()

        await asyncio.gather(as_user("id-a"), as_user("id-b"), anonymous())
    seen = {r.headers["X-Who"]: r.headers.get("Authorization") for r in proxy.requests}
    assert seen == {"id-a": "Bearer id-a", "id-b": "Bearer id-b", "-": None}


async def test_nested_scopes_restore_the_outer_user() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey)
        async with donkey.as_user(token="id-outer"):
            async with donkey.as_user(token="id-inner"):
                await _model_call(donkey)
            await _model_call(donkey)
        assert current_user() is None
    assert [r.headers["Authorization"] for r in proxy.requests] == [
        "Bearer id-inner",
        "Bearer id-outer",
    ]


async def test_the_blocking_client_stamps_the_user_token() -> None:
    async with Donkey(_cfg()) as donkey:
        sync = donkey._sync_http_client()
        proxy = _Recorder()
        sync.governed_transport.replace_inner(httpx.MockTransport(proxy))
        async with donkey.as_user(token=_TOKEN):
            sync.post(f"{_PROXY}chat", json={"model": "m"}, headers={"Authorization": _SENTINEL})
        sync.post(f"{_PROXY}chat", json={"model": "m"}, headers={"Authorization": _SENTINEL})
    inside, after = proxy.requests
    assert inside.headers["Authorization"] == f"Bearer {_TOKEN}"
    assert after.headers["Authorization"] == _SENTINEL


# --- no silent fallback to the service identity (#216) ------------------------


@pytest.mark.parametrize("token", ["", "  ", None])
def test_a_missing_token_is_an_auth_error_that_names_it(token: object) -> None:
    with pytest.raises(AuthError, match="no token") as info:
        UserScope(_cfg(), token)  # type: ignore[arg-type]
    assert "service" in info.value.remediation


async def test_a_missing_token_binds_nothing_and_sends_nothing() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey)
        with pytest.raises(AuthError):
            async with donkey.as_user(token=""):
                await _model_call(donkey)  # pragma: no cover - never reached
        assert current_user() is None
    assert proxy.requests == []


async def test_every_request_through_the_openai_clients_carries_the_user_token() -> None:
    """Both real OpenAI clients: each request that reaches the proxy from
    inside the block carries the user token, never only the service's."""
    pytest.importorskip("openai")

    messages = [{"role": "user", "content": "hi"}]
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey)
        sync_http = donkey._sync_http_client()
        sync_http.governed_transport.replace_inner(httpx.MockTransport(proxy))
        openai, openai_sync = donkey.llm.client(), donkey.llm.client(sync=True)
        async with donkey.as_user(token=_TOKEN):
            await openai.chat.completions.create(model="m", messages=messages)  # type: ignore[arg-type]
            openai_sync.chat.completions.create(model="m", messages=messages)  # type: ignore[arg-type]
    assert len(proxy.requests) == 2
    for request in proxy.requests:
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"


def test_no_setting_enables_a_fallback_to_the_service_identity() -> None:
    """Pins the absence: a fallback switch must not be added (#216)."""
    names = [field.name for field in dataclasses.fields(DonkeyConfig)]
    words = ("fallback", "service_identity", "downgrade", "allow_service")
    assert [name for name in names if any(word in name for word in words)] == []
