"""URL overrides passed in code go through the same https check as the config.

``donkey.llm.client(base_url=...)`` and every adapter factory accept an
endpoint override in code. Code is trusted, so the override may receive the
configured credentials, but it must still be ``https://`` (loopback hosts, or
any host with ``DONKEY_ALLOW_HTTP=1`` in the environment, excepted), exactly as
``with_overrides(llm_proxy_url=...)`` is. Once checked it becomes an endpoint
the shared client sends credentials to.

The check runs before the framework is imported, so every refusal here is
asserted on the base install too.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.errors import ConfigError, ConfigWarning
from donkey_kit.core.transport import DonkeyAsyncClient
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter

_HTTP_OVERRIDE = "http://other.example.internal/v1/"
_HTTPS_OVERRIDE = "https://other.example.internal/v1/"


def _cfg(**kw: object) -> DonkeyConfig:
    base: dict[str, object] = {
        "llm_proxy_url": "https://proxy.example.internal/openai/",
        "llm_proxy_client_id": "proxy-client-id",
        "llm_proxy_client_secret": "proxy-client-secret",
    }
    base.update(kw)
    return DonkeyConfig(**base)  # type: ignore[arg-type]


def _jwt_cfg() -> DonkeyConfig:
    return _cfg(llm_proxy_auth="jwt", llm_proxy_wallet_client_id="wallet-client-id")


def _adapter(name: str, cfg: DonkeyConfig | None = None) -> Adapter:
    spec = ADAPTERS[name]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cfg = cfg or _cfg()
    return getattr(module, spec.cls)(cfg, DonkeyAsyncClient(cfg, None))  # type: ignore[no-any-return]


# Every code-level URL override the factories accept, applied to ``url``.
_OVERRIDES: dict[str, Callable[[str], Any]] = {
    "langgraph.chat_model(base_url)": lambda u: _adapter("langgraph").chat_model("m", base_url=u),
    "langgraph.chat_model(openai_api_base)": lambda u: _adapter("langgraph").chat_model(
        "m", openai_api_base=u
    ),
    "adk.model(api_base)": lambda u: _adapter("adk").model("m", api_base=u),
    "adk.model(base_url)": lambda u: _adapter("adk").model("m", base_url=u),
    "adk.gemini(base_url)": lambda u: _adapter("adk").gemini("m", base_url=u),
    "adk.gemini_connection_kwargs(base_url)": lambda u: _adapter("adk").gemini_connection_kwargs(
        base_url=u
    ),
    "strands.model(client_args.base_url)": lambda u: _adapter("strands").model(
        "m", client_args={"base_url": u, "api_key": "k"}
    ),
    "agent_framework.chat_client(base_url)": lambda u: _adapter("agent_framework").chat_client(
        "m", base_url=u
    ),
    "anthropic.client(base_url)": lambda u: _adapter("anthropic").client(base_url=u),
    "crewai.llm(base_url)": lambda u: _adapter("crewai").llm("m", base_url=u),
    "crewai.llm(api_base)": lambda u: _adapter("crewai").llm("m", api_base=u),
    "llamaindex.llm(api_base)": lambda u: _adapter("llamaindex").llm("m", api_base=u),
}


@pytest.mark.parametrize("override", sorted(_OVERRIDES))
def test_adapter_factories_refuse_a_plain_http_override(override: str) -> None:
    with pytest.raises(ConfigError, match="must be an https:// URL"):
        _OVERRIDES[override](_HTTP_OVERRIDE)


@pytest.mark.parametrize("sync", [False, True])
def test_raw_client_refuses_a_plain_http_override(sync: bool) -> None:
    pytest.importorskip("openai")
    donkey = Donkey(_cfg())
    with pytest.raises(ConfigError, match="base_url must be an https:// URL"):
        donkey.llm.client(sync=sync, base_url=_HTTP_OVERRIDE)


def test_raw_client_refuses_a_plain_http_override_in_jwt_mode() -> None:
    pytest.importorskip("openai")
    donkey = Donkey(_jwt_cfg(), llm_auth=StaticToken("wallet-jwt-value"))
    with pytest.raises(ConfigError, match="base_url must be an https:// URL"):
        donkey.llm.client(base_url=_HTTP_OVERRIDE)


def test_plain_http_override_is_allowed_with_the_env_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("openai")
    monkeypatch.setenv("DONKEY_ALLOW_HTTP", "1")
    donkey = Donkey(_cfg())
    with pytest.warns(ConfigWarning, match="plain http://"):
        client = donkey.llm.client(base_url=_HTTP_OVERRIDE)
    assert str(client.base_url) == _HTTP_OVERRIDE


def test_loopback_http_override_is_allowed() -> None:
    pytest.importorskip("openai")
    client = Donkey(_cfg()).llm.client(base_url="http://127.0.0.1:8080/")
    assert str(client.base_url) == "http://127.0.0.1:8080/"


async def test_an_https_override_is_used_and_receives_the_credentials() -> None:
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"object": "list", "data": []})

    donkey = Donkey(_cfg())
    donkey._http._swap_transport(httpx.MockTransport(handler))
    try:
        await donkey.llm.client(base_url=_HTTPS_OVERRIDE).models.list()
    finally:
        await donkey.aclose()

    [request] = seen
    assert str(request.url).startswith(_HTTPS_OVERRIDE)
    assert request.headers["client_secret"] == "proxy-client-secret"


async def test_an_https_override_in_jwt_mode_receives_the_jwt() -> None:
    pytest.importorskip("openai")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"object": "list", "data": []})

    donkey = Donkey(_jwt_cfg(), llm_auth=StaticToken("wallet-jwt-value"))
    donkey._http._swap_transport(httpx.MockTransport(handler))
    try:
        await donkey.llm.client(base_url=_HTTPS_OVERRIDE).models.list()
    finally:
        await donkey.aclose()

    [request] = seen
    assert request.headers["authorization"] == "Bearer wallet-jwt-value"
    assert request.headers["x-client-id"] == "wallet-client-id"


def test_an_https_override_through_an_adapter_is_accepted() -> None:
    pytest.importorskip("langchain_openai")
    model = _adapter("langgraph").chat_model("m", base_url=_HTTPS_OVERRIDE)
    assert model.openai_api_base == _HTTPS_OVERRIDE
