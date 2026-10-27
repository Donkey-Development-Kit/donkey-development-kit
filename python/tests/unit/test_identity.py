"""On-behalf-of identity: ``donkey.as_user(id_token=...)`` (BG §2.4, #215, #216).

The RFC 8693 exchange (form shape, caching to expiry minus the margin, typed
failures that never echo a token), the contextvar scope (task isolation,
nesting), and where the exchanged token rides: data-plane sends on both
clients, never the control plane or an unchecked origin. And the hard failure
(#216): a missing or expired user token is an ``AuthError``, never a request
sent as the service identity.

Driven with ``httpx.MockTransport``, so no network is touched.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from urllib.parse import parse_qs

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import _verify
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError, ConfigError
from donkey_kit.core.header_names import user_token_header_problem
from donkey_kit.core.identity import TokenExchange, UserScope, current_user
from donkey_kit.core.transport.headers import PROXY_API_KEY_SENTINEL

_PROXY = "https://gw.example.internal/openai-sdk/"
_CONTROL_PLANE = "https://anypoint.example.internal"
_ID_TOKEN = "id-token-alice"
_SECRET = "cp-secret-value"
_SENTINEL = f"Bearer {PROXY_API_KEY_SENTINEL}"


def _cfg(**kw: object) -> DonkeyConfig:
    base: dict[str, object] = {
        "client_id": "cp-id",
        "client_secret": _SECRET,
        "base_url": _CONTROL_PLANE,
        "llm_proxy_url": _PROXY,
        "llm_proxy_client_id": "cid",
        "llm_proxy_client_secret": "csecret",
    }
    base.update(kw)
    return DonkeyConfig(**base)  # type: ignore[arg-type]


class _Exchanger:
    """Answers the token exchange: ``user-<id_token>`` for each subject token."""

    def __init__(self, *, expires_in: float | None = 3600, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.expires_in = expires_in
        self.status = status

    def form(self, index: int = -1) -> dict[str, str]:
        parsed = parse_qs(self.requests[index].content.decode())
        return {key: values[0] for key, values in parsed.items()}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_grant"})
        subject = parse_qs(request.content.decode())["subject_token"][0]
        body: dict[str, object] = {"access_token": f"user-{subject}"}
        if self.expires_in is not None:
            body["expires_in"] = self.expires_in
        return httpx.Response(200, json=body)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _exchange(handler: _Exchanger, *, clock: _Clock | None = None, **kw: object) -> TokenExchange:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    if clock is None:
        return TokenExchange(_cfg(**kw), http_client=http)
    return TokenExchange(_cfg(**kw), http_client=http, clock=clock)


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"access_token": "cp-token", "expires_in": 3600})


def _wire(donkey: Donkey, exchanger: _Exchanger) -> _Recorder:
    """Point the exchange at ``exchanger`` and the data plane at a recorder."""
    donkey._runtime.token_exchange()
    exchange_http = donkey._runtime._exchange_http
    assert exchange_http is not None
    exchange_http.governed_transport.replace_inner(httpx.MockTransport(exchanger))
    proxy = _Recorder()
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(proxy))
    return proxy


async def _model_call(donkey: Donkey, url: str = f"{_PROXY}chat/completions") -> None:
    # The OpenAI SDK pre-sets its api_key slot as the Authorization bearer.
    await donkey._http.post(url, json={"model": "m"}, headers={"Authorization": _SENTINEL})


# --- the exchange -------------------------------------------------------------


async def test_the_exchange_posts_an_rfc_8693_grant_to_the_control_plane() -> None:
    handler = _Exchanger()
    with pytest.warns(_verify.UnverifiedValueWarning, match="identity.token_exchange_path"):
        token = await _exchange(handler).token(_ID_TOKEN)
    assert token == f"user-{_ID_TOKEN}"
    (request,) = handler.requests
    assert str(request.url) == f"{_CONTROL_PLANE}{_verify.TOKEN_EXCHANGE_PATH.placeholder}"
    assert handler.form() == {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": _ID_TOKEN,
        "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        "client_id": "cp-id",
        "client_secret": _SECRET,
    }


async def test_token_exchange_path_overrides_the_path_but_not_the_host() -> None:
    handler = _Exchanger()
    await _exchange(handler, token_exchange_path="/custom/exchange").token(_ID_TOKEN)
    assert str(handler.requests[0].url) == f"{_CONTROL_PLANE}/custom/exchange"


async def test_tokens_are_cached_until_expiry_minus_the_margin() -> None:
    handler, clock = _Exchanger(expires_in=600), _Clock()
    exchange = _exchange(handler, clock=clock)
    await exchange.token(_ID_TOKEN)
    clock.now += 539
    await exchange.token(_ID_TOKEN)
    assert len(handler.requests) == 1
    clock.now += 1  # 600 s less the 60 s margin
    await exchange.token(_ID_TOKEN)
    assert len(handler.requests) == 2


async def test_a_token_without_expires_in_is_not_cached() -> None:
    handler = _Exchanger(expires_in=None)
    exchange = _exchange(handler)
    await exchange.token(_ID_TOKEN)
    await exchange.token(_ID_TOKEN)
    assert len(handler.requests) == 2


async def test_each_user_gets_their_own_cached_token() -> None:
    handler = _Exchanger()
    exchange = _exchange(handler)
    assert await exchange.token("id-a") == "user-id-a"
    assert await exchange.token("id-b") == "user-id-b"
    assert await exchange.token("id-a") == "user-id-a"
    assert len(handler.requests) == 2


async def test_concurrent_misses_share_one_exchange() -> None:
    handler = _Exchanger()
    exchange = _exchange(handler)
    tokens = await asyncio.gather(*(exchange.token(_ID_TOKEN) for _ in range(5)))
    assert set(tokens) == {f"user-{_ID_TOKEN}"}
    assert len(handler.requests) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 500])
async def test_a_refused_exchange_is_an_auth_error_that_names_no_token(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="donkey_kit")
    with pytest.raises(AuthError) as info:
        await _exchange(_Exchanger(status=status)).token(_ID_TOKEN)
    err = info.value
    assert str(status) in str(err)
    assert "token_exchange_path" in err.remediation
    for secret in (_ID_TOKEN, _SECRET):
        assert secret not in str(err)
        assert secret not in err.remediation
        assert secret not in caplog.text


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["a list"]),
        httpx.Response(200, json={"expires_in": 60}),
        httpx.Response(200, json={"access_token": 7}),
        httpx.Response(200, json={"access_token": "t", "expires_in": "soon"}),
        httpx.Response(200, json={"access_token": "t", "expires_in": "nan"}),
    ],
)
async def test_a_malformed_exchange_response_is_an_auth_error(response: httpx.Response) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(AuthError, match="malformed"):
        await TokenExchange(_cfg(), http_client=http).token(_ID_TOKEN)


# --- config checks ------------------------------------------------------------


@pytest.mark.parametrize(
    ("kw", "match"),
    [
        ({"client_id": None}, "client_id and client_secret"),
        ({"token_exchange_path": "https://elsewhere/x"}, "must be a path"),
        ({"llm_proxy_auth": "jwt", "llm_proxy_wallet_client_id": "w"}, "user_token_header"),
        ({"llm_proxy_auth": "bearer"}, "user_token_header"),
    ],
)
def test_check_refuses_a_config_the_exchange_cannot_run_under(
    kw: dict[str, object], match: str
) -> None:
    with pytest.raises(ConfigError, match=match):
        TokenExchange(_cfg(**kw), http_client=httpx.AsyncClient()).check()


def test_a_token_mode_with_a_custom_user_header_is_allowed() -> None:
    _exchange(_Exchanger(), llm_proxy_auth="bearer", user_token_header="X-User-Token").check()


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


# --- the scope and the transport ----------------------------------------------


async def test_a_model_call_in_the_scope_carries_the_user_token() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey, _Exchanger())
        await _model_call(donkey)
        async with donkey.as_user(id_token=_ID_TOKEN):
            await _model_call(donkey)
        await _model_call(donkey)
    before, inside, after = proxy.requests
    assert before.headers["Authorization"] == _SENTINEL
    # The sentinel is overridden: yielding to it would send the service identity.
    assert inside.headers["Authorization"] == f"Bearer user-{_ID_TOKEN}"
    assert after.headers["Authorization"] == _SENTINEL


async def test_a_custom_user_token_header_carries_the_raw_token() -> None:
    async with Donkey(_cfg(user_token_header="X-User-Token")) as donkey:
        proxy = _wire(donkey, _Exchanger())
        async with donkey.as_user(id_token=_ID_TOKEN):
            await _model_call(donkey)
    (request,) = proxy.requests
    assert request.headers["X-User-Token"] == f"user-{_ID_TOKEN}"
    assert request.headers["Authorization"] == _SENTINEL


async def test_the_user_token_stays_off_the_control_plane_and_unchecked_origins() -> None:
    async with Donkey(_cfg(user_token_header="X-User-Token")) as donkey:
        _wire(donkey, _Exchanger())
        platform = _Recorder()
        donkey._runtime.control_http.governed_transport.replace_inner(httpx.MockTransport(platform))
        assert donkey._owned_auth_http is not None
        donkey._owned_auth_http.governed_transport.replace_inner(httpx.MockTransport(platform))
        elsewhere = _Recorder()
        donkey._http.governed_transport.replace_inner(httpx.MockTransport(elsewhere))
        async with donkey.as_user(id_token=_ID_TOKEN):
            await donkey._runtime.control_http.get(f"{_CONTROL_PLANE}/accounts/api/me")
            await donkey._http.post(
                "https://elsewhere.example/v1/chat", json={}, headers={"X-User-Token": "planted"}
            )
    for request in [*platform.requests, *elsewhere.requests]:
        assert "x-user-token" not in request.headers
        assert f"user-{_ID_TOKEN}" not in str(request.headers)


async def test_a_refused_exchange_raises_on_entry_and_binds_nothing() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey, _Exchanger(status=401))
        with pytest.raises(AuthError):
            async with donkey.as_user(id_token=_ID_TOKEN):
                await _model_call(donkey)  # pragma: no cover - never reached
        assert current_user() is None
    assert proxy.requests == []


async def test_concurrent_tasks_never_see_each_others_user() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey, _Exchanger())
        entered = asyncio.Event()
        release = asyncio.Event()

        async def as_user(id_token: str) -> None:
            async with donkey.as_user(id_token=id_token):
                entered.set()
                await release.wait()
                await donkey._http.post(
                    f"{_PROXY}chat", json={"model": "m"}, headers={"X-Who": id_token}
                )

        async def anonymous() -> None:
            await entered.wait()
            await donkey._http.post(f"{_PROXY}chat", json={"model": "m"}, headers={"X-Who": "-"})
            release.set()

        await asyncio.gather(as_user("id-a"), as_user("id-b"), anonymous())
    seen = {r.headers["X-Who"]: r.headers.get("Authorization") for r in proxy.requests}
    assert seen == {"id-a": "Bearer user-id-a", "id-b": "Bearer user-id-b", "-": None}


async def test_nested_scopes_restore_the_outer_user() -> None:
    async with Donkey(_cfg()) as donkey:
        proxy = _wire(donkey, _Exchanger())
        async with donkey.as_user(id_token="id-outer"):
            async with donkey.as_user(id_token="id-inner"):
                await _model_call(donkey)
            await _model_call(donkey)
        assert current_user() is None
    assert [r.headers["Authorization"] for r in proxy.requests] == [
        "Bearer user-id-inner",
        "Bearer user-id-outer",
    ]


async def test_the_async_client_exchanges_again_after_expiry() -> None:
    clock = _Clock()
    async with Donkey(_cfg()) as donkey:
        exchanger = _Exchanger(expires_in=120)
        proxy = _wire(donkey, exchanger)
        donkey._runtime.token_exchange()._clock = clock
        async with donkey.as_user(id_token=_ID_TOKEN):
            await _model_call(donkey)
            clock.now += 61
            await _model_call(donkey)
    assert len(exchanger.requests) == 2
    assert len(proxy.requests) == 2


async def test_the_blocking_client_stamps_the_token_until_it_expires() -> None:
    clock = _Clock()
    async with Donkey(_cfg()) as donkey:
        _wire(donkey, _Exchanger(expires_in=120))
        donkey._runtime.token_exchange()._clock = clock
        sync = donkey._sync_http_client()
        proxy = _Recorder()
        sync.governed_transport.replace_inner(httpx.MockTransport(proxy))
        async with donkey.as_user(id_token=_ID_TOKEN):
            sync.post(f"{_PROXY}chat", json={"model": "m"}, headers={"Authorization": _SENTINEL})
            clock.now += 61
            with pytest.raises(AuthError, match="expired"):
                sync.post(f"{_PROXY}chat", json={"model": "m"})
    (request,) = proxy.requests
    assert request.headers["Authorization"] == f"Bearer user-{_ID_TOKEN}"


async def test_the_blocking_client_stamps_an_unexpiring_token() -> None:
    async with Donkey(_cfg()) as donkey:
        _wire(donkey, _Exchanger(expires_in=None))
        sync = donkey._sync_http_client()
        proxy = _Recorder()
        sync.governed_transport.replace_inner(httpx.MockTransport(proxy))
        async with donkey.as_user(id_token=_ID_TOKEN):
            sync.post(f"{_PROXY}chat", json={"model": "m"})
    assert proxy.requests[0].headers["Authorization"] == f"Bearer user-{_ID_TOKEN}"


async def test_as_user_needs_connected_app_credentials() -> None:
    async with Donkey(_cfg(client_id=None, client_secret=None)) as donkey:
        with pytest.raises(ConfigError, match="client_id and client_secret"):
            donkey.as_user(id_token=_ID_TOKEN)


# --- no silent fallback to the service identity (#216) ------------------------


@pytest.mark.parametrize("id_token", ["", "  ", None])
def test_a_missing_id_token_is_an_auth_error_that_names_it(id_token: object) -> None:
    with pytest.raises(AuthError, match="id_token is missing") as info:
        UserScope(_exchange(_Exchanger()), id_token)  # type: ignore[arg-type]
    assert "id_token" in info.value.remediation


async def test_a_failed_re_exchange_after_expiry_sends_nothing() -> None:
    clock = _Clock()
    async with Donkey(_cfg()) as donkey:
        exchanger = _Exchanger(expires_in=120)
        proxy = _wire(donkey, exchanger)
        donkey._runtime.token_exchange()._clock = clock
        async with donkey.as_user(id_token=_ID_TOKEN):
            await _model_call(donkey)
            clock.now += 61
            exchanger.status = 401  # the id_token has expired upstream
            with pytest.raises(AuthError, match="no request was sent as the service"):
                await _model_call(donkey)
    (request,) = proxy.requests
    assert request.headers["Authorization"] == f"Bearer user-{_ID_TOKEN}"


async def test_no_request_leaves_as_user_with_only_service_credentials() -> None:
    """Every way a model call can leave the block, through the real OpenAI
    clients: each request that reaches the proxy carries the user token, and
    each failure raises ``AuthError`` with nothing sent."""
    pytest.importorskip("openai")
    from donkey_kit.core.refusals import translate

    clock = _Clock()
    messages = [{"role": "user", "content": "hi"}]
    async with Donkey(_cfg()) as donkey:
        exchanger = _Exchanger(expires_in=120)
        proxy = _wire(donkey, exchanger)
        donkey._runtime.token_exchange()._clock = clock
        sync_http = donkey._sync_http_client()
        sync_http.governed_transport.replace_inner(httpx.MockTransport(proxy))
        openai, openai_sync = donkey.llm.client(), donkey.llm.client(sync=True)

        def create() -> object:
            return openai_sync.chat.completions.create(model="m", messages=messages)  # type: ignore[arg-type]

        async def acreate() -> object:
            return await openai.chat.completions.create(model="m", messages=messages)  # type: ignore[arg-type]

        async with donkey.as_user(id_token=_ID_TOKEN):
            await acreate()
            create()
            clock.now += 61  # expired: the blocking client can't exchange again
            with pytest.raises(Exception) as info:
                create()
            assert isinstance(translate(info.value), AuthError)
            await acreate()  # the async client exchanges again
            clock.now += 61
            exchanger.status = 401  # and now the id_token is refused
            with pytest.raises(Exception) as info:
                await acreate()
            assert isinstance(translate(info.value), AuthError)

        with pytest.raises(AuthError):
            async with donkey.as_user(id_token="id-refused"):
                pass  # pragma: no cover - entry raises
    assert len(proxy.requests) == 3
    for request in proxy.requests:
        assert request.headers["Authorization"] == f"Bearer user-{_ID_TOKEN}"


def test_no_setting_enables_a_fallback_to_the_service_identity() -> None:
    """Pins the absence: a fallback switch must not be added (#216)."""
    names = [field.name for field in dataclasses.fields(DonkeyConfig)]
    words = ("fallback", "service_identity", "downgrade", "allow_service")
    assert [name for name in names if any(word in name for word in words)] == []
