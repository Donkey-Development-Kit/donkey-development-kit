"""Connected-app OAuth token acquisition and caching (BG §1.1)."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from donkey_kit.core.auth import AnypointConnectedApp, ChainedAuth
from donkey_kit.core.errors import AuthError


def _assert_control_plane_remediation(error: AuthError) -> None:
    assert error.remediation is AuthError.connected_app_remediation
    assert "ANYPOINT_CLIENT_ID" in error.remediation
    assert "ANYPOINT_CLIENT_SECRET" in error.remediation
    assert "scopes" in error.remediation
    assert "docs/verified-apis.md §1" in error.remediation
    assert "DONKEY_LLM_PROXY_CLIENT_ID" not in error.remediation


class _FailingAuth:
    async def token(self) -> str:
        raise RuntimeError("provider failed")

    async def invalidate(self) -> None:
        return None


async def test_connected_app_posts_credentials_and_caches_until_safety_margin() -> None:
    now = [100.0]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": f"token-{len(requests)}", "expires_in": 120},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        auth = AnypointConnectedApp(
            client_id="client-id",
            client_secret="client-secret",
            control_plane_url="https://anypoint.example/",
            http_client=http_client,
            token_path="/oauth/token",
            clock=lambda: now[0],
        )

        assert await auth.token() == "token-1"
        now[0] = 159.0
        assert await auth.token() == "token-1"
        now[0] = 160.0
        assert await auth.token() == "token-2"

    assert len(requests) == 2
    request = requests[0]
    assert request.method == "POST"
    assert request.url == httpx.URL("https://anypoint.example/oauth/token")
    assert request.headers["accept"] == "application/json"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {
        "grant_type": ["client_credentials"],
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
    }


async def test_connected_app_invalidate_forces_refetch() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": f"token-{calls}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        auth = AnypointConnectedApp(
            client_id="client-id",
            client_secret="client-secret",
            control_plane_url="https://anypoint.example",
            http_client=http_client,
            token_path="/oauth/token",
        )

        assert await auth.token() == "token-1"
        await auth.invalidate()
        assert await auth.token() == "token-2"

    assert calls == 2


@pytest.mark.parametrize("status_code", [401, 403])
async def test_connected_app_maps_rejected_credentials_to_auth_error(status_code: int) -> None:
    response: httpx.Response | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal response
        response = httpx.Response(status_code)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        auth = AnypointConnectedApp(
            client_id="client-id",
            client_secret="client-secret",
            control_plane_url="https://anypoint.example",
            http_client=http_client,
            token_path="/oauth/token",
        )

        with pytest.raises(AuthError, match="token request rejected") as exc_info:
            await auth.token()

    assert exc_info.value.response is response
    _assert_control_plane_remediation(exc_info.value)


async def test_connected_app_maps_other_http_errors_to_auth_error() -> None:
    response: httpx.Response | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal response
        response = httpx.Response(500)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        auth = AnypointConnectedApp(
            client_id="client-id",
            client_secret="client-secret",
            control_plane_url="https://anypoint.example",
            http_client=http_client,
            token_path="/oauth/token",
        )

        with pytest.raises(AuthError, match="token endpoint returned 500") as exc_info:
            await auth.token()

    assert exc_info.value.response is response
    _assert_control_plane_remediation(exc_info.value)


async def test_connected_app_rejects_success_response_without_access_token() -> None:
    response: httpx.Response | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal response
        response = httpx.Response(200, json={"expires_in": 3600})
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        auth = AnypointConnectedApp(
            client_id="client-id",
            client_secret="client-secret",
            control_plane_url="https://anypoint.example",
            http_client=http_client,
            token_path="/oauth/token",
        )

        with pytest.raises(AuthError, match="returned no access_token") as exc_info:
            await auth.token()

    assert exc_info.value.response is response
    _assert_control_plane_remediation(exc_info.value)


async def test_chained_auth_failure_uses_provider_neutral_remediation() -> None:
    auth = ChainedAuth(_FailingAuth())

    with pytest.raises(AuthError, match="No auth provider yielded a token") as exc_info:
        await auth.token()

    assert exc_info.value.remediation is AuthError.provider_chain_remediation
    assert "each provider's credentials or token source" in exc_info.value.remediation
    assert "ANYPOINT_CLIENT_ID" not in exc_info.value.remediation
