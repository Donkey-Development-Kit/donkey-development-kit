"""In jwt / model-wallet auth mode, an adapter sends the JWT or refuses (#828).

The JWT is added per-send only by the shared data-plane client. An adapter that
sends through it must carry ``Authorization: Bearer <jwt>``, never the
``client-id-enforced`` api-key placeholder. One that can't — CrewAI, whose
provider builds its own clients, or any adapter whose shared client has no
``AuthProvider`` (a ``Donkey`` without ``llm_auth``, the module-level
factories) — raises ``ConfigError`` before anything is sent.

The call tests skip unless their framework's extra is installed.
"""

from __future__ import annotations

import contextlib
import importlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter, default_adapter

_JWT = "FAKE.JWT"
_WALLET = "wallet-42"


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy/",
        llm_proxy_auth="jwt",
        llm_proxy_wallet_client_id=_WALLET,
        max_retries=0,
    )


def _adapter_cls(name: str) -> type[Adapter]:
    spec = ADAPTERS[name]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls


def _chat_completion(request: httpx.Request) -> httpx.Response:
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return httpx.Response(200, json=body, request=request)


async def _llamaindex(donkey: Donkey) -> None:
    pytest.importorskip("llama_index.llms.openai_like")
    await donkey.llamaindex.llm("m").acomplete("hi")


async def _agent_framework(donkey: Donkey) -> None:
    pytest.importorskip("agent_framework.openai")
    await donkey.agent_framework.chat_client("m").get_response("hi")


async def _adk_model(donkey: Donkey) -> None:
    pytest.importorskip("google.adk.models.lite_llm")
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    model = donkey.adk.model("m")
    request = LlmRequest(
        model=model.model,
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
    )
    async for _ in model.generate_content_async(request):
        pass


@pytest.mark.parametrize(
    "call",
    [_llamaindex, _agent_framework, _adk_model],
    ids=["llamaindex", "agent_framework", "adk_model"],
)
async def test_async_call_carries_the_jwt(call: Callable[[Donkey], Awaitable[None]]) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _chat_completion(request)

    donkey = Donkey(_cfg(), llm_auth=StaticToken(_JWT))
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(respond))
    async with donkey:
        # The response body is only chat-completions-shaped; what is asserted is
        # what was sent. A refusal before sending leaves ``seen`` empty.
        with contextlib.suppress(Exception):
            await call(donkey)

    assert seen, "the call never reached the shared client"
    assert [r.headers.get("authorization") for r in seen] == [f"Bearer {_JWT}"] * len(seen)
    assert {r.headers.get("x-client-id") for r in seen} == {_WALLET}


async def test_crewai_refuses_jwt_mode_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    sends: list[httpx.Request] = []

    def refuse(_self: object, request: httpx.Request) -> httpx.Response:
        sends.append(request)
        raise httpx.ConnectError("network disabled in this test", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    donkey = Donkey(_cfg(), llm_auth=StaticToken(_JWT))
    adapter = _adapter_cls("crewai")(donkey._cfg, donkey._http, donkey._sync_http_client)
    async with donkey:
        # Both forms, and with or without CrewAI installed: llm() refuses before
        # its framework import.
        with pytest.raises(ConfigError, match="CrewAI does not support llm_proxy_auth='jwt'"):
            adapter.connection_kwargs()
        with pytest.raises(ConfigError, match="CrewAI does not support llm_proxy_auth='jwt'"):
            adapter.llm("m")  # type: ignore[attr-defined]
    assert sends == []


_SHARED_CLIENT_ADAPTERS = sorted(set(ADAPTERS) - {"crewai"})


@pytest.mark.parametrize("name", _SHARED_CLIENT_ADAPTERS)
async def test_adapter_without_jwt_provider_refuses(name: str) -> None:
    # Without llm_auth the shared client has no JWT to add, so the request would
    # carry the api-key placeholder as its bearer (#828).
    donkey = Donkey(_cfg())
    adapter = _adapter_cls(name)(donkey._cfg, donkey._http, donkey._sync_http_client)
    async with donkey:
        with pytest.raises(ConfigError, match="requires an AuthProvider"):
            adapter.connection_kwargs()


def test_module_level_factory_refuses_jwt_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The process-default runtime is configured from the environment, which has
    # no way to supply an AuthProvider.
    monkeypatch.chdir(tmp_path)  # no stray .donkey-kit.toml
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://proxy/")
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "jwt")
    monkeypatch.setenv("DONKEY_LLM_PROXY_WALLET_CLIENT_ID", _WALLET)
    adapter: Any = default_adapter(_adapter_cls("langgraph"))
    with pytest.raises(ConfigError, match="requires an AuthProvider"):
        adapter.connection_kwargs()
