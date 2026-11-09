"""Credential provenance, request reuse and secret-free diagnostics (#1693)."""

from __future__ import annotations

import logging
import os
import traceback
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import pytest

from donkey_kit.core.auth import ChainedAuth, StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AuthError, ConfigError
from donkey_kit.core.identity import UserScope
from donkey_kit.core.reactions import Invoke, ReactionRun
from donkey_kit.core.runtime import Runtime
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient

_URL = "https://proxy.example.test/chat"
_SECRET = "sensitive-provider-token-1693"


@pytest.fixture
def project_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DonkeyConfig:
    for key in list(os.environ):
        if key.startswith(("DONKEY_", "ANYPOINT_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-user-config"))
    (tmp_path / ".donkey-kit.toml").write_text(
        '[donkey]\nllm_proxy_url = "https://proxy.example.test/"\n'
    )
    (tmp_path / ".donkey-kit.local.toml").write_text(
        '[donkey]\nllm_proxy_client_id = "project-id"\nllm_proxy_client_secret = "project-secret"\n'
    )
    return DonkeyConfig.from_env()


@pytest.mark.parametrize("mode", ["jwt", "bearer"])
async def test_runtime_checks_token_endpoint_on_every_fetch(
    project_config: DonkeyConfig, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    cfg = project_config.with_overrides(llm_proxy_auth=mode, llm_proxy_wallet_client_id="wallet")
    runtime = Runtime(cfg, llm_auth=StaticToken(_SECRET))
    seen: list[dict[str, str]] = []
    runtime.http.governed_transport.replace_inner(
        httpx.MockTransport(lambda r: seen.append(dict(r.headers)) or httpx.Response(200))
    )
    try:
        monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")
        await runtime.http.get(_URL)
        monkeypatch.delenv("DONKEY_TRUST_PROJECT_CONFIG")
        with pytest.raises(ConfigError):
            await runtime.http.get(_URL)
        assert len(seen) == 1
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("sync", [False, True])
async def test_user_injection_checks_receiving_config_every_time(
    project_config: DonkeyConfig, monkeypatch: pytest.MonkeyPatch, sync: bool
) -> None:
    scope = UserScope(DonkeyConfig(llm_proxy_url=_URL), _SECRET)
    seen: list[dict[str, str]] = []
    transport = httpx.MockTransport(lambda r: seen.append(dict(r.headers)) or httpx.Response(200))
    client = (
        DonkeyClient(project_config, transport=transport)
        if sync
        else DonkeyAsyncClient(project_config, None, transport=transport)
    )
    try:
        with scope:
            monkeypatch.setenv("DONKEY_TRUST_PROJECT_CONFIG", "1")
            if sync:
                client.get(_URL)
            else:
                await client.get(_URL)
            monkeypatch.delenv("DONKEY_TRUST_PROJECT_CONFIG")
            with pytest.raises(ConfigError):
                if sync:
                    client.get(_URL)
                else:
                    await client.get(_URL)
        assert len(seen) == 1
    finally:
        if sync:
            client.close()
        else:
            await client.aclose()


@pytest.mark.parametrize("sync", [False, True])
@pytest.mark.parametrize("header", ["Authorization", "X-User-Token"])
@pytest.mark.parametrize("caller", [None, "caller-supplied"])
async def test_reused_request_restores_caller_header(
    sync: bool, header: str, caller: str | None
) -> None:
    cfg = DonkeyConfig(llm_proxy_url=_URL, user_token_header=header)
    seen: list[dict[str, str]] = []
    transport = httpx.MockTransport(lambda r: seen.append(dict(r.headers)) or httpx.Response(200))
    client = (
        DonkeyClient(cfg, transport=transport)
        if sync
        else DonkeyAsyncClient(cfg, None, transport=transport)
    )
    request = httpx.Request("GET", _URL, headers={header: caller} if caller else {})
    try:
        with UserScope(cfg, _SECRET):
            if sync:
                client.send(request)
            else:
                await client.send(request)
        assert _SECRET not in repr(request.extensions)
        if sync:
            client.send(request)
        else:
            await client.send(request)
        assert seen[0][header.lower()] == (
            f"Bearer {_SECRET}" if header == "Authorization" else _SECRET
        )
        assert seen[1].get(header.lower()) == caller
    finally:
        if sync:
            client.close()
        else:
            await client.aclose()


async def test_reused_request_clears_old_config_user_header() -> None:
    first_cfg = DonkeyConfig(llm_proxy_url=_URL, user_token_header="X-First-User")
    next_cfg = DonkeyConfig(llm_proxy_url=_URL, user_token_header="X-Next-User")
    seen: list[dict[str, str]] = []
    transport = httpx.MockTransport(lambda r: seen.append(dict(r.headers)) or httpx.Response(200))
    request = httpx.Request("GET", _URL)
    async with DonkeyAsyncClient(first_cfg, None, transport=transport) as first:
        with UserScope(first_cfg, _SECRET):
            await first.send(request)
    async with DonkeyAsyncClient(next_cfg, None, transport=transport) as second:
        with UserScope(next_cfg, "next-user"):
            await second.send(request)
    assert "x-first-user" not in seen[1]
    assert seen[1]["x-next-user"] == "next-user"


async def test_provider_failure_is_not_in_public_error_or_traceback() -> None:
    class Failing:
        async def token(self) -> str:
            raise RuntimeError(_SECRET)

        async def invalidate(self) -> None:
            return None

    with pytest.raises(AuthError) as caught:
        await ChainedAuth(Failing()).token()
    assert _SECRET not in str(caught.value)
    assert _SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("sync", [False, True])
async def test_reused_request_honors_caller_edits_and_strips_unchecked_origin(sync: bool) -> None:
    cfg = DonkeyConfig(llm_proxy_url=_URL, user_token_header="X-User-Token")
    seen: list[dict[str, str]] = []
    transport = httpx.MockTransport(lambda r: seen.append(dict(r.headers)) or httpx.Response(200))
    client = (
        DonkeyClient(cfg, transport=transport)
        if sync
        else DonkeyAsyncClient(cfg, None, transport=transport)
    )
    request = httpx.Request("GET", _URL, headers={"X-User-Token": "original"})
    try:
        with UserScope(cfg, _SECRET):
            if sync:
                client.send(request)
            else:
                await client.send(request)
        request.headers["X-User-Token"] = "edited-by-caller"
        if sync:
            client.send(request)
        else:
            await client.send(request)
        assert seen[-1]["x-user-token"] == "edited-by-caller"
        with UserScope(cfg, _SECRET):
            if sync:
                client.send(request)
            else:
                await client.send(request)
        request.url = httpx.URL("https://unchecked.example.test/")
        if sync:
            client.send(request)
        else:
            await client.send(request)
        assert "x-user-token" not in seen[-1]
    finally:
        if sync:
            client.close()
        else:
            await client.aclose()


@pytest.mark.parametrize("mode", ["jwt", "bearer"])
async def test_receiving_token_mode_does_not_replace_service_token(mode: str) -> None:
    scope_cfg = DonkeyConfig(llm_proxy_url=_URL)
    cfg = scope_cfg.with_overrides(llm_proxy_auth=mode, llm_proxy_wallet_client_id="wallet")
    seen: list[httpx.Request] = []
    async with DonkeyAsyncClient(
        cfg,
        StaticToken("service-token"),
        transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)),
    ) as client:
        with UserScope(scope_cfg, _SECRET), pytest.raises(ConfigError, match="user_token_header"):
            await client.get(_URL)
    assert seen == []


def test_handler_failure_does_not_log_exception_details(caplog: pytest.LogCaptureFixture) -> None:
    step = Invoke(lambda _: None, None, "on(PolicyViolation).call")
    with caplog.at_level(logging.WARNING):
        try:
            raise RuntimeError(_SECRET)
        except RuntimeError as exc:
            ReactionRun.failed(step, exc)
    assert "RuntimeError" in caplog.text
    assert _SECRET not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("first_sync", [False, True])
@pytest.mark.parametrize("second_sync", [False, True])
@pytest.mark.parametrize("destination", ["unchecked", "original", "receiving", "first-only"])
@pytest.mark.parametrize("restore_first", [False, True])
async def test_custom_user_header_provenance_survives_cross_client_reuse(
    first_sync: bool, second_sync: bool, destination: str, restore_first: bool
) -> None:
    first_cfg = DonkeyConfig(llm_proxy_url=_URL, user_token_header="X-User-Token")
    target = "https://attacker.example/chat" if destination == "unchecked" else _URL
    if destination == "receiving":
        target = "https://other-proxy.example.test/chat"
    second_cfg = DonkeyConfig(llm_proxy_url=target if destination != "unchecked" else _URL)
    if destination == "first-only":
        second_cfg = DonkeyConfig(llm_proxy_url="https://other-proxy.example.test/chat")
    seen: list[dict[str, str]] = []
    original = "original-user-secret"
    request = httpx.Request("GET", _URL, headers={"X-User-Token": original})

    async def send(client: DonkeyClient | DonkeyAsyncClient) -> None:
        if isinstance(client, DonkeyClient):
            client.send(request)
        else:
            await client.send(request)

    async with AsyncExitStack() as stack:
        clients: list[DonkeyClient | DonkeyAsyncClient] = []
        for sync, cfg in ((first_sync, first_cfg), (second_sync, second_cfg)):
            transport = httpx.MockTransport(
                lambda r: seen.append(dict(r.headers)) or httpx.Response(200)
            )
            if sync:
                clients.append(stack.enter_context(DonkeyClient(cfg, transport=transport)))
            else:
                clients.append(
                    await stack.enter_async_context(
                        DonkeyAsyncClient(cfg, None, transport=transport)
                    )
                )
        first, second = clients
        with UserScope(first_cfg, _SECRET):
            await send(first)
        assert _SECRET not in repr(request.extensions)
        assert original not in repr(request.extensions)
        if restore_first:
            await send(first)
            assert seen[-1]["x-user-token"] == original
        request.url = httpx.URL(target)
        await send(second)
        if destination in {"unchecked", "first-only"}:
            assert "x-user-token" not in seen[-1]
            # Provenance must also survive stripping and a later caller write.
            request.headers["X-User-Token"] = original
            await send(second)
            assert "x-user-token" not in seen[-1]
        else:
            assert seen[-1]["x-user-token"] == original
        assert _SECRET not in repr(request.extensions)
        assert original not in repr(request.extensions)
