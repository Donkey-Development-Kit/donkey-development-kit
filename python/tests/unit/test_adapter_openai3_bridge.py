"""LangGraph, LlamaIndex and Strands hand openai>=3 the core httpx2 bridge (#728).

Each of these frameworks builds its own ``AsyncOpenAI`` (and, for LangGraph and
LlamaIndex, ``OpenAI``) from an ``http_client`` kwarg: ``ChatOpenAI``'s
``http_async_client`` / ``http_client``, ``OpenAILike``'s ``async_http_client`` /
``http_client``, and Strands' ``client_args["http_client"]``. On ``openai>=3``,
which types that kwarg as an ``httpx2`` client, the adapters hand over the same
core bridge ``donkey.llm`` uses, chosen in one place
(``llm.client.openai_http_client``). On ``openai<3`` they hand over the shared
client's non-owning view, unchanged.

The bridge they get is reusable: Strands closes the ``AsyncOpenAI`` it builds
around ``client_args`` after every request, and a plain ``httpx2`` client refuses
every send once closed.

The wiring tests force each branch, so both run whichever openai is installed;
the end-to-end tests drive the installed framework and openai.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport.views import built_on_httpx2
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter
from donkey_kit.llm import client as llm_client

CFG = DonkeyConfig(
    llm_proxy_url="https://proxy.example.com/p/",
    llm_proxy_client_id="cid",
    llm_proxy_client_secret="secret",
)

# Where each adapter puts the async and sync clients in connection_kwargs().
_SLOTS: dict[str, tuple[tuple[str, ...], tuple[str, ...] | None]] = {
    "langgraph": (("http_async_client",), ("http_client",)),
    "llamaindex": (("async_http_client",), ("http_client",)),
    "strands": (("client_args", "http_client"), None),
}


def _openai_on_httpx2() -> bool:
    openai = pytest.importorskip("openai")
    return built_on_httpx2(getattr(openai, "DefaultAsyncHttpxClient", None))


def _completion(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    return httpx.Response(
        200,
        json={
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": body.get("model", "m"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "PONG"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    )


def _donkey(seen: list[httpx.Request], cfg: DonkeyConfig = CFG, **kw: Any) -> Donkey:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _completion(request)

    donkey = Donkey(cfg, **kw)
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(handler))
    if cfg.llm_proxy_auth not in ("jwt", "bearer"):
        donkey._sync_http_client().governed_transport.replace_inner(httpx.MockTransport(handler))
    return donkey


def _adapter(name: str, donkey: Donkey) -> Adapter:
    spec = ADAPTERS[name]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls(donkey._cfg, donkey._http, donkey._sync_http_client)


def _at(kwargs: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = kwargs
    for key in path:
        value = value[key]
    return value


def _forwards_to(client: Any) -> Any:
    """The shared client a bridged ``httpx2`` client's transport sends through."""
    return client._transport._client


# --- the wiring, both branches -------------------------------------------------


@pytest.mark.parametrize("name", sorted(_SLOTS))
async def test_openai3_gets_the_reusable_bridge(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    httpx2 = pytest.importorskip("httpx2")
    monkeypatch.setattr(llm_client, "_openai_on_httpx2", lambda: True)
    donkey = _donkey([])
    kwargs = _adapter(name, donkey).connection_kwargs()
    async_path, sync_path = _SLOTS[name]

    bridged = _at(kwargs, async_path)
    assert isinstance(bridged, httpx2.AsyncClient)
    assert type(bridged._transport).__name__ == "DonkeyForwardingTransport"
    assert _forwards_to(bridged) is donkey._http
    if sync_path is not None:
        sync_bridged = _at(kwargs, sync_path)
        assert isinstance(sync_bridged, httpx2.Client)
        assert type(sync_bridged._transport).__name__ == "DonkeyForwardingSyncTransport"
        assert _forwards_to(sync_bridged) is donkey._sync_http_client()
    await donkey.aclose()


@pytest.mark.parametrize("name", sorted(_SLOTS))
async def test_openai_before_3_still_gets_the_view(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_client, "_openai_on_httpx2", lambda: False)
    donkey = _donkey([])
    kwargs = _adapter(name, donkey).connection_kwargs()
    async_path, sync_path = _SLOTS[name]

    assert _at(kwargs, async_path) is donkey._http.view()
    if sync_path is not None:
        assert _at(kwargs, sync_path) is donkey._sync_http_client().view()
    await donkey.aclose()


@pytest.mark.parametrize("name", sorted(_SLOTS))
async def test_the_bridge_is_one_per_adapter(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # As the view is one per shared client: a factory and connection_kwargs()
    # hand the framework the same object, so they cannot drift.
    pytest.importorskip("httpx2")
    monkeypatch.setattr(llm_client, "_openai_on_httpx2", lambda: True)
    donkey = _donkey([])
    adapter = _adapter(name, donkey)
    first, second = adapter.connection_kwargs(), adapter.connection_kwargs()
    for path in filter(None, _SLOTS[name]):
        assert _at(first, path) is _at(second, path)
    await donkey.aclose()


async def test_closing_the_reusable_bridge_leaves_it_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("httpx2")
    monkeypatch.setattr(llm_client, "_openai_on_httpx2", lambda: True)
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    adapter = _adapter("langgraph", donkey)
    bridged = adapter._openai_kwarg_http_client()
    sync_bridged = adapter._openai_kwarg_sync_http_client()

    async with bridged:
        pass
    await bridged.aclose()
    with sync_bridged:
        pass
    sync_bridged.close()

    assert not bridged.is_closed and not sync_bridged.is_closed
    assert (await bridged.post("https://proxy.example.com/p/x", json={})).status_code == 200
    assert sync_bridged.post("https://proxy.example.com/p/x", json={}).status_code == 200
    assert len(seen) == 2
    assert not donkey._http.is_closed
    await donkey.aclose()


# --- the installed framework and openai, end to end ----------------------------


def _assert_governed(seen: list[httpx.Request], n: int) -> None:
    assert len(seen) == n
    for request in seen:
        assert isinstance(request, httpx.Request)
        assert request.headers["client_id"] == "cid"
        assert request.headers["client_secret"] == "secret"


def _holds_the_bridge(openai_client: Any, shared: Any) -> None:
    """``openai_client`` sends through ``shared``: via the bridge on openai>=3,
    the view before."""
    inner = openai_client._client
    if _openai_on_httpx2():
        assert _forwards_to(inner) is shared
    else:
        assert inner is shared.view()


async def test_langgraph_async_and_sync_calls_go_through_the_bridge() -> None:
    pytest.importorskip("langchain_openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    model = donkey.langgraph("gpt-4o", use_responses_api=False)
    _holds_the_bridge(model.root_async_client, donkey._http)
    _holds_the_bridge(model.root_client, donkey._sync_http_client())

    assert (await model.ainvoke("hi")).content == "PONG"
    assert model.invoke("hi").content == "PONG"
    _assert_governed(seen, 2)
    await donkey.aclose()


async def test_llamaindex_async_and_sync_calls_go_through_the_bridge() -> None:
    pytest.importorskip("llama_index.llms.openai_like")
    from llama_index.core.llms import ChatMessage

    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    llm = donkey.llamaindex.llm("gpt-4o")
    _holds_the_bridge(llm._get_aclient(), donkey._http)
    _holds_the_bridge(llm._get_client(), donkey._sync_http_client())

    message = [ChatMessage(role="user", content="hi")]
    assert (await llm.achat(message)).message.content == "PONG"
    assert llm.chat(message).message.content == "PONG"
    _assert_governed(seen, 2)
    await donkey.aclose()


async def test_strands_survives_its_per_request_close_of_the_bridge() -> None:
    strands = pytest.importorskip("strands")
    pytest.importorskip("strands.models.openai")
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    model = donkey.strands.model("gpt-4o")
    if _openai_on_httpx2():
        assert _forwards_to(model.client_args["http_client"]) is donkey._http
    agent = strands.Agent(model=model, callback_handler=None)

    # Strands closes the AsyncOpenAI (and so its http_client) after each call.
    await agent.invoke_async("one")
    await agent.invoke_async("two")
    _assert_governed(seen, 2)
    assert not donkey._http.is_closed
    await donkey.aclose()


async def test_a_sync_call_in_a_token_mode_is_refused_before_sending() -> None:
    # Parity with the async path (#509): the sync bridge sends through the
    # blocking shared client, which refuses in a token mode, so nothing goes out
    # unauthenticated.
    pytest.importorskip("llama_index.llms.openai_like")
    from llama_index.core.llms import ChatMessage

    seen: list[httpx.Request] = []
    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy.example.com/p/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
        llm_proxy_auth="jwt",
        llm_proxy_wallet_client_id="wallet-42",
    )
    donkey = _donkey(seen, cfg, llm_auth=StaticToken("jwt"))
    llm = donkey.llamaindex.llm("gpt-4o")
    with pytest.raises(ConfigError, match="async-only"):
        llm.chat([ChatMessage(role="user", content="hi")])
    assert seen == []
    assert (await llm.achat([ChatMessage(role="user", content="hi")])).message.content == "PONG"
    assert [r.headers.get("authorization") for r in seen] == ["Bearer jwt"]
    await donkey.aclose()
