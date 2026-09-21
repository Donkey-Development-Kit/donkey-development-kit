"""LLMClient guards for jwt / model-wallet auth mode (BG §1.1, #509).

Two things config alone cannot check are enforced in ``LLMClient.client()``,
where the attached ``AuthProvider`` is known:

  * jwt mode is async-only (Proposal 6 / AC 7) — the blocking client takes no
    provider and cannot await a rotating JWT, so ``sync=True`` raises.
  * jwt mode with no provider attached raises with actionable guidance (AC 1).

Both guards fire BEFORE the OpenAI SDK import, so they need no ``[llm]`` extra;
the one positive path that constructs a real client ``importorskip``s openai.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.llm.client import LLMClient


def _jwt_cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_auth="jwt",
        llm_proxy_url="https://proxy",
        llm_proxy_wallet_client_id="wallet-42",
    )


def _http(cfg: DonkeyConfig, auth) -> DonkeyAsyncClient:
    # A MockTransport keeps construction network-free; the guards raise before
    # any request is ever sent, so the handler is never invoked.
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    return DonkeyAsyncClient(cfg, auth, transport=transport)


async def test_jwt_sync_client_is_rejected_as_async_only() -> None:
    # Even WITH a provider attached, sync=True is refused: the blocking client
    # cannot await the rotating JWT (Proposal 6 / AC 7).
    client = _http(_jwt_cfg(), StaticToken("jwt"))
    try:
        llm = LLMClient(_jwt_cfg(), client)
        with pytest.raises(ConfigError, match="async-only"):
            llm.client(sync=True)
    finally:
        await client.aclose()


async def test_jwt_without_provider_is_rejected_with_guidance() -> None:
    client = _http(_jwt_cfg(), None)  # no AuthProvider attached
    try:
        llm = LLMClient(_jwt_cfg(), client)
        with pytest.raises(ConfigError, match="requires an AuthProvider") as exc:
            llm.client()
    finally:
        await client.aclose()
    # Actionable: names the Donkey(llm_auth=...) entry point (AC 1).
    assert "llm_auth" in str(exc.value)


async def test_jwt_async_with_provider_passes_the_guards() -> None:
    # The positive path constructs a real AsyncOpenAI, so it needs the SDK.
    openai = pytest.importorskip("openai")
    client = _http(_jwt_cfg(), StaticToken("jwt"))
    try:
        llm = LLMClient(_jwt_cfg(), client)
        result = llm.client()  # no ConfigError — provider present, async
    finally:
        await client.aclose()
    assert isinstance(result, openai.AsyncOpenAI)
