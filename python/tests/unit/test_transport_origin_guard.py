"""The shared clients only send credentials to the endpoint checked for them.

Each shared client knows the origins (scheme, host, port) it was checked for:
the configured LLM proxy URL on the data plane, the control-plane URL on the
control plane, plus any URL passed in code through :meth:`allow_endpoint`
once it passes the same https check as the config. A request to any other
origin — including a redirect hop, which runs the request hooks again — gets
none of the SDK's credentials and loses any credential header set on it.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient

_PROXY = "https://proxy.example.internal"
_CONTROL = "https://control.example.internal"
_OTHER = "https://other.example.internal"
_JWT = "wallet-jwt-value"
_PLATFORM_TOKEN = "platform-token-value"

# Credential headers a framework client sets from connection_kwargs().
_SET_BY_FRAMEWORK = {
    "client_id": "proxy-client-id",
    "client_secret": "proxy-client-secret",
    "Authorization": "Bearer proxy-key-value",
    "x-api-key": "proxy-key-value",
}
_CREDENTIAL_NAMES = ("client_id", "client_secret", "authorization", "x-api-key", "x-client-id")


def _cfg(**kw: object) -> DonkeyConfig:
    base: dict[str, object] = {
        "llm_proxy_url": f"{_PROXY}/openai/",
        "llm_proxy_client_id": "proxy-client-id",
        "llm_proxy_client_secret": "proxy-client-secret",
        "base_url": _CONTROL,
    }
    base.update(kw)
    return DonkeyConfig(**base)  # type: ignore[arg-type]


def _jwt_cfg() -> DonkeyConfig:
    return _cfg(llm_proxy_auth="jwt", llm_proxy_wallet_client_id="wallet-client-id")


class _Recorder:
    """A mock transport that records each request and can redirect one origin."""

    def __init__(self, redirect_from: str | None = None, redirect_to: str | None = None) -> None:
        self.seen: list[httpx.Request] = []
        self._from = redirect_from
        self._to = redirect_to

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self._from and str(request.url).startswith(self._from) and self._to:
            return httpx.Response(307, headers={"location": f"{self._to}/landed"})
        return httpx.Response(200, json={})

    def at(self, origin: str) -> list[httpx.Request]:
        return [r for r in self.seen if str(r.url).startswith(origin)]


def _credentials(request: httpx.Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() in _CREDENTIAL_NAMES}


def _async(
    cfg: DonkeyConfig,
    rec: _Recorder,
    auth: StaticToken | None = None,
    *,
    control_plane: bool = False,
) -> DonkeyAsyncClient:
    return DonkeyAsyncClient(
        cfg, auth, control_plane=control_plane, transport=httpx.MockTransport(rec)
    )


# --- data plane ------------------------------------------------------------------


async def test_the_configured_proxy_origin_keeps_the_credential_headers() -> None:
    rec = _Recorder()
    async with _async(_cfg(), rec) as http:
        await http.get(f"{_PROXY}/openai/responses", headers=_SET_BY_FRAMEWORK)

    assert _credentials(rec.seen[0])["client_secret"] == "proxy-client-secret"


async def test_another_origin_gets_no_credential_headers() -> None:
    rec = _Recorder()
    async with _async(_cfg(), rec) as http:
        await http.get(f"{_OTHER}/responses", headers=_SET_BY_FRAMEWORK)

    assert _credentials(rec.seen[0]) == {}


@pytest.mark.parametrize(
    "url",
    [
        "https://proxy.example.internal:8443/openai/",  # another port
        "http://proxy.example.internal/openai/",  # another scheme
        "https://sub.proxy.example.internal/openai/",  # another host
    ],
)
async def test_origin_means_scheme_host_and_port(url: str) -> None:
    rec = _Recorder()
    async with _async(_cfg(), rec) as http:
        await http.get(url, headers=_SET_BY_FRAMEWORK)

    assert _credentials(rec.seen[0]) == {}


async def test_jwt_mode_sends_the_jwt_and_wallet_header_only_to_the_proxy() -> None:
    rec = _Recorder()
    async with _async(_jwt_cfg(), rec, StaticToken(_JWT)) as http:
        await http.get(f"{_PROXY}/openai/responses")
        await http.get(f"{_OTHER}/responses")

    [proxy], [other] = rec.at(_PROXY), rec.at(_OTHER)
    assert proxy.headers["authorization"] == f"Bearer {_JWT}"
    assert proxy.headers["x-client-id"] == "wallet-client-id"
    assert _credentials(other) == {}


async def test_a_redirect_to_another_origin_carries_no_credentials() -> None:
    rec = _Recorder(redirect_from=_PROXY, redirect_to=_OTHER)
    async with _async(_jwt_cfg(), rec, StaticToken(_JWT)) as http:
        await http.get(
            f"{_PROXY}/openai/responses",
            headers={"client_secret": "proxy-client-secret"},
            follow_redirects=True,
        )

    [first], [hop] = rec.at(_PROXY), rec.at(_OTHER)
    assert first.headers["authorization"] == f"Bearer {_JWT}"
    assert _credentials(hop) == {}


async def test_a_same_origin_redirect_keeps_the_credentials() -> None:
    rec = _Recorder(redirect_from=f"{_PROXY}/openai/old", redirect_to=f"{_PROXY}/openai")
    async with _async(_jwt_cfg(), rec, StaticToken(_JWT)) as http:
        await http.get(f"{_PROXY}/openai/old", follow_redirects=True)

    hop = rec.seen[-1]
    assert str(hop.url).endswith("/landed")
    assert hop.headers["authorization"] == f"Bearer {_JWT}"
    assert hop.headers["x-client-id"] == "wallet-client-id"


# --- endpoints allowed in code ----------------------------------------------------


async def test_an_allowed_https_endpoint_receives_the_credentials() -> None:
    rec = _Recorder()
    async with _async(_jwt_cfg(), rec, StaticToken(_JWT)) as http:
        http.allow_endpoint(f"{_OTHER}/v2/", name="base_url")
        await http.get(f"{_OTHER}/v2/responses", headers={"client_secret": "s"})

    assert rec.seen[0].headers["authorization"] == f"Bearer {_JWT}"
    assert rec.seen[0].headers["client_secret"] == "s"


def test_allow_endpoint_refuses_plain_http_to_a_remote_host() -> None:
    http = DonkeyAsyncClient(_cfg(), None)
    with pytest.raises(ConfigError, match="base_url must be an https:// URL"):
        http.allow_endpoint("http://other.example.internal/", name="base_url")


def test_allow_endpoint_accepts_loopback_http() -> None:
    http = DonkeyAsyncClient(_cfg(), None)
    http.allow_endpoint("http://127.0.0.1:8080/", name="base_url")


# --- control plane ----------------------------------------------------------------


async def test_the_platform_token_goes_only_to_the_control_plane_origin() -> None:
    rec = _Recorder()
    async with _async(_cfg(), rec, StaticToken(_PLATFORM_TOKEN), control_plane=True) as http:
        await http.get(f"{_CONTROL}/exchange/api/v2/assets")
        await http.get(f"{_OTHER}/exchange/api/v2/assets")

    [control], [other] = rec.at(_CONTROL), rec.at(_OTHER)
    assert control.headers["authorization"] == f"Bearer {_PLATFORM_TOKEN}"
    assert _credentials(other) == {}


async def test_a_control_plane_redirect_to_another_origin_carries_no_token() -> None:
    rec = _Recorder(redirect_from=_CONTROL, redirect_to=_OTHER)
    async with _async(_cfg(), rec, StaticToken(_PLATFORM_TOKEN), control_plane=True) as http:
        await http.get(f"{_CONTROL}/exchange/api/v2/assets", follow_redirects=True)

    [hop] = rec.at(_OTHER)
    assert _credentials(hop) == {}


async def test_the_region_default_control_plane_url_is_the_checked_origin() -> None:
    rec = _Recorder()
    cfg = _cfg(base_url=None)
    async with _async(cfg, rec, StaticToken(_PLATFORM_TOKEN), control_plane=True) as http:
        await http.get(f"{cfg.control_plane_url.rstrip('/')}/accounts/api/me")

    assert rec.seen[0].headers["authorization"] == f"Bearer {_PLATFORM_TOKEN}"


async def test_the_data_plane_client_does_not_trust_the_control_plane_origin() -> None:
    rec = _Recorder()
    async with _async(_cfg(), rec) as http:
        await http.get(f"{_CONTROL}/x", headers=_SET_BY_FRAMEWORK)

    assert _credentials(rec.seen[0]) == {}


# --- the blocking client ----------------------------------------------------------


def test_the_blocking_client_strips_credentials_for_another_origin() -> None:
    rec = _Recorder(redirect_from=_PROXY, redirect_to=_OTHER)
    with DonkeyClient(_cfg(), transport=httpx.MockTransport(rec)) as http:
        http.get(f"{_OTHER}/responses", headers=_SET_BY_FRAMEWORK)
        http.get(f"{_PROXY}/openai/responses", headers=_SET_BY_FRAMEWORK, follow_redirects=True)

    direct, _first, hop = rec.seen
    assert _credentials(direct) == {}
    assert _credentials(_first)["client_secret"] == "proxy-client-secret"
    assert _credentials(hop) == {}


def test_the_blocking_client_shares_allowed_endpoints_with_its_async_twin() -> None:
    http = DonkeyAsyncClient(_cfg(), None)
    rec = _Recorder()
    with DonkeyClient(
        _cfg(), origins=http.checked_origins, transport=httpx.MockTransport(rec)
    ) as sync_http:
        http.allow_endpoint(f"{_OTHER}/", name="base_url")
        sync_http.get(f"{_OTHER}/responses", headers=_SET_BY_FRAMEWORK)

    assert _credentials(rec.seen[0])["client_secret"] == "proxy-client-secret"
