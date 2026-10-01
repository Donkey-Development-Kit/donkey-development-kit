"""Adapters in jwt / model-wallet auth mode refuse forms that would send no JWT (#835).

In jwt mode the rotating JWT is added per send by the shared data-plane client,
from the ``Donkey(llm_auth=...)`` provider. Two kinds of form would reach the
proxy without it and get a bare ``401``: an adapter whose framework builds its
own client from a header snapshot (CrewAI), and any adapter on a shared client
with no provider (the module-level factories' default runtime). Both raise
``ConfigError`` before any request, like ``donkey.llm.client()`` does.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from donkey_kit import Donkey
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient, missing_jwt_provider_error
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter, default_adapter
from donkey_kit.integrations.crewai import CrewAIAdapter


def _cfg(**kw: object) -> DonkeyConfig:
    base: dict[str, object] = {
        "llm_proxy_url": "https://proxy.example.internal/openai/",
        "llm_proxy_auth": "jwt",
        "llm_proxy_wallet_client_id": "wallet-client-id",
    }
    base.update(kw)
    return DonkeyConfig(**base)  # type: ignore[arg-type]


def _adapter_cls(name: str) -> type[Adapter]:
    spec = ADAPTERS[name]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls


def _shared_client_adapters() -> list[str]:
    return [name for name in ADAPTERS if _adapter_cls(name).carries_jwt]


@pytest.fixture
def jwt_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no stray .donkey-kit.toml
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://proxy.example.internal/openai/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "jwt")
    monkeypatch.setenv("DONKEY_LLM_PROXY_WALLET_CLIENT_ID", "wallet-client-id")


def test_crewai_is_the_only_adapter_that_cannot_carry_the_jwt() -> None:
    assert [name for name in ADAPTERS if not _adapter_cls(name).carries_jwt] == ["crewai"]


# --- CrewAI: never carries the JWT, even with a provider ----------------------


async def test_crewai_connection_kwargs_raise_in_jwt_mode_with_a_provider() -> None:
    donkey = Donkey(_cfg(), llm_auth=StaticToken("wallet-jwt"))
    async with donkey:
        adapter = CrewAIAdapter(donkey._cfg, donkey._http, donkey._sync_http_client)
        with pytest.raises(ConfigError, match="crewai adapter cannot carry") as exc:
            adapter.connection_kwargs()
    message = str(exc.value)
    assert "llm_proxy_auth='jwt'" in message
    for supported in ("donkey.llm.client()", "LangGraph", "Strands", "OpenAI Agents", "ADK"):
        assert supported in message


async def test_crewai_llm_raises_in_jwt_mode_with_a_provider() -> None:
    pytest.importorskip("crewai")
    donkey = Donkey(_cfg(), llm_auth=StaticToken("wallet-jwt"))
    async with donkey:
        with pytest.raises(ConfigError, match="crewai adapter cannot carry"):
            donkey.crewai.llm("gpt-4o")


async def test_crewai_still_builds_in_client_id_mode() -> None:
    cfg = _cfg(
        llm_proxy_auth="client-id",
        llm_proxy_client_id="proxy-client-id",
        llm_proxy_client_secret="proxy-client-secret",
    )
    adapter = CrewAIAdapter(cfg, DonkeyAsyncClient(cfg, None))
    assert adapter.connection_kwargs()["base_url"] == cfg.llm_proxy_url


# --- No provider on the shared client -----------------------------------------


@pytest.mark.parametrize("name", _shared_client_adapters())
def test_connection_kwargs_raise_in_jwt_mode_without_a_provider(name: str) -> None:
    cfg = _cfg()
    adapter = _adapter_cls(name)(cfg, DonkeyAsyncClient(cfg, None))
    with pytest.raises(ConfigError) as exc:
        adapter.connection_kwargs()
    assert str(exc.value) == str(missing_jwt_provider_error())


@pytest.mark.parametrize("name", _shared_client_adapters())
async def test_connection_kwargs_build_in_jwt_mode_with_a_provider(name: str) -> None:
    if name == "openai_agents":
        pytest.importorskip("openai")  # its only governed value is an AsyncOpenAI
    donkey = Donkey(_cfg(), llm_auth=StaticToken("wallet-jwt"))
    async with donkey:
        adapter = _adapter_cls(name)(donkey._cfg, donkey._http, donkey._sync_http_client)
        assert adapter.connection_kwargs()


def test_raw_client_and_adapters_share_the_missing_provider_error() -> None:
    donkey = Donkey(_cfg())
    with pytest.raises(ConfigError) as exc:
        donkey.llm.client()
    assert str(exc.value) == str(missing_jwt_provider_error())


@pytest.mark.parametrize("name", _shared_client_adapters())
def test_default_adapter_raises_in_jwt_mode(name: str, jwt_env: None) -> None:
    with pytest.raises(ConfigError) as exc:
        default_adapter(_adapter_cls(name)).connection_kwargs()
    assert "Donkey(llm_auth=" in str(exc.value)
    assert "module-level adapter factories" in str(exc.value)


def test_module_level_factory_raises_in_jwt_mode(jwt_env: None) -> None:
    pytest.importorskip("langchain_openai")
    from donkey_kit.integrations.langgraph import chat_model

    with pytest.raises(ConfigError, match=r"Donkey\(llm_auth="):
        chat_model("gpt-4o")


def test_crewai_module_level_factory_raises_in_jwt_mode(jwt_env: None) -> None:
    pytest.importorskip("crewai")
    from donkey_kit.integrations.crewai import llm

    with pytest.raises(ConfigError, match="crewai adapter cannot carry"):
        llm("gpt-4o")
