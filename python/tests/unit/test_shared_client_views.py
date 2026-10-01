"""Frameworks get a non-owning view of the shared client, never the client (#733).

Several frameworks own the lifecycle of the client they are given: Strands runs
``async with AsyncOpenAI(**client_args)`` per request, and ``async with
donkey.openai()`` closes its ``http_client`` on exit. Before #733 that closed the
one shared ``DonkeyAsyncClient`` for the whole ``Donkey``. These tests pin that a
framework closing what it was handed leaves the shared client open, that requests
through a view are governed exactly like requests through the shared client, and
that only ``Donkey.aclose()``/``close()`` end the pool.

Framework-free at module level (the base-only job): the openai and Strands cases
import their package inside the test.
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import (
    DonkeyAsyncClient,
    DonkeyAsyncClientView,
    DonkeyClient,
    DonkeyClientView,
)
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter

CFG = DonkeyConfig(
    llm_proxy_url="https://proxy.example.com/p/",
    llm_proxy_client_id="cid",
    llm_proxy_client_secret="secret",
)
MESSAGES = [{"role": "user", "content": "ping"}]


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


def _donkey(seen: list[httpx.Request] | None = None) -> Donkey:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return _completion(request)

    donkey = Donkey(CFG)
    donkey._http._swap_transport(httpx.MockTransport(handler))
    donkey._sync_http_client()._swap_transport(httpx.MockTransport(handler))
    return donkey


# --- the views themselves ----------------------------------------------------


async def test_closing_the_async_view_leaves_the_shared_client_open() -> None:
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    view = donkey.http_client()
    assert isinstance(view, DonkeyAsyncClientView)
    assert not isinstance(view, DonkeyAsyncClient)

    async with view:
        await view.post("https://proxy.example.com/p/chat/completions", json={"model": "m"})
    await view.aclose()

    assert not donkey._http.is_closed
    assert not view.is_closed
    response = await view.post("https://proxy.example.com/p/chat/completions", json={"model": "m"})
    assert response.status_code == 200
    # Sent through the shared client, so its governed headers are on the wire.
    # The per-call id is pinned in DonkeyAsyncClient.send(), not by an event hook.
    assert seen[-1].headers["x-correlation-id"]
    assert seen[-1].headers["x-donkey-request-id"]

    await donkey.aclose()
    assert donkey._http.is_closed
    assert view.is_closed  # reports the shared client's state


def test_closing_the_sync_view_leaves_the_shared_client_open() -> None:
    seen: list[httpx.Request] = []
    donkey = _donkey(seen)
    view = donkey.http_client(sync=True)
    assert isinstance(view, DonkeyClientView)
    assert not isinstance(view, DonkeyClient)

    with view:
        view.post("https://proxy.example.com/p/chat/completions", json={"model": "m"})
    view.close()

    shared = donkey._sync_http_client()
    assert not shared.is_closed
    assert view.post("https://proxy.example.com/p/x", json={"model": "m"}).status_code == 200
    assert seen[-1].headers["x-donkey-request-id"]

    donkey.close()
    assert shared.is_closed


def test_the_sync_view_refuses_in_jwt_mode_like_the_shared_client() -> None:
    from donkey_kit.core.auth import StaticToken
    from donkey_kit.core.errors import ConfigError

    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy.example.com/p/",
        llm_proxy_auth="jwt",
        llm_proxy_wallet_client_id="wallet-42",
    )
    donkey = Donkey(cfg, llm_auth=StaticToken("jwt"))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    donkey._sync_http_client()._swap_transport(httpx.MockTransport(handler))

    with pytest.raises(ConfigError):
        donkey.http_client(sync=True).post("https://proxy.example.com/p/x", json={})
    assert not seen


async def test_one_cached_view_per_shared_client() -> None:
    donkey = _donkey()
    assert donkey.http_client() is donkey.http_client() is donkey._http.view()
    assert donkey.http_client(sync=True) is donkey._sync_http_client().view()
    # Built directly: ``donkey.strands`` refuses when Strands isn't installed.
    from donkey_kit.integrations.strands import StrandsAdapter

    assert StrandsAdapter(CFG, donkey._http).http_client() is donkey.http_client()
    await donkey.aclose()


async def test_event_hooks_added_to_the_view_run_for_its_requests() -> None:
    # The provisioning app records last_call from a response hook when a
    # framework calls from a task it spawned; the hook belongs on the view.
    donkey = _donkey()
    view = donkey.http_client()
    requests: list[httpx.Request] = []
    responses: list[httpx.Response] = []

    async def on_request(request: httpx.Request) -> None:
        requests.append(request)

    async def on_response(response: httpx.Response) -> None:
        responses.append(response)

    view.event_hooks = {"request": [on_request], "response": [on_response]}
    await view.post("https://proxy.example.com/p/chat/completions", json={"model": "m"})

    assert len(requests) == len(responses) == 1
    assert responses[0].status_code == 200
    await donkey.aclose()


async def test_simulate_still_applies_through_the_view() -> None:
    from donkey_kit.core.errors import PIIDetected

    donkey = _donkey()
    view = donkey.http_client()
    with donkey.simulate(PIIDetected):
        response = await view.post(
            "https://proxy.example.com/p/chat/completions", json={"model": "m"}
        )
    assert response.status_code == 403
    assert response.headers["x-donkey-simulator"] == "true"
    await donkey.aclose()


# --- donkey.openai() / donkey.llm ----------------------------------------------


async def test_async_with_donkey_openai_leaves_the_donkey_usable() -> None:
    pytest.importorskip("openai")
    donkey = _donkey()
    async with donkey.openai() as llm:
        await llm.chat.completions.create(model="m", messages=MESSAGES)  # type: ignore[arg-type]
    assert not donkey._http.is_closed

    reply = await donkey.openai().chat.completions.create(model="m", messages=MESSAGES)  # type: ignore[arg-type]
    assert reply.choices[0].message.content == "PONG"
    await donkey.aclose()
    assert donkey._http.is_closed


def test_with_donkey_openai_sync_leaves_the_donkey_usable() -> None:
    pytest.importorskip("openai")
    donkey = _donkey()
    with donkey.openai(sync=True) as llm:
        llm.chat.completions.create(model="m", messages=MESSAGES)  # type: ignore[arg-type]
    assert not donkey._sync_http_client().is_closed

    reply = donkey.openai(sync=True).chat.completions.create(model="m", messages=MESSAGES)  # type: ignore[arg-type]
    assert reply.choices[0].message.content == "PONG"
    donkey.close()


# --- the adapter contract: every transport-routed adapter ----------------------


def _http_clients(value: Any) -> list[httpx.AsyncClient | httpx.Client]:
    """Every httpx client in a ``connection_kwargs()`` mapping, including the one
    inside a pre-built OpenAI client (``openai_agents``)."""
    if isinstance(value, httpx.AsyncClient | httpx.Client):
        return [value]
    if isinstance(value, dict):
        return [c for v in value.values() for c in _http_clients(v)]
    inner = getattr(value, "_client", None)
    if isinstance(inner, httpx.AsyncClient | httpx.Client):
        return [inner]
    return []


def _handed_clients(adapter: Adapter, attr: str) -> list[httpx.AsyncClient | httpx.Client]:
    kwargs: list[dict[str, Any]] = [adapter.connection_kwargs()]  # type: ignore[attr-defined]
    if attr == "adk":
        kwargs.append(adapter.gemini_connection_kwargs())  # type: ignore[attr-defined]
    return [c for kw in kwargs for c in _http_clients(kw)]


def _routed_adapters() -> set[str]:
    routed = {"langgraph", "strands", "anthropic", "llamaindex", "adk"}
    if importlib.util.find_spec("openai") is not None:
        routed |= {"openai_agents", "agent_framework"}
    return routed


@pytest.mark.parametrize("attr", sorted(ADAPTERS))
async def test_closing_what_an_adapter_hands_out_never_closes_the_shared_client(
    attr: str,
) -> None:
    if attr == "openai_agents":
        # Its only governed value is a pre-built AsyncOpenAI (no base-only install).
        pytest.importorskip("openai")
    spec = ADAPTERS[attr]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    donkey = _donkey()
    adapter: Adapter = getattr(module, spec.cls)(CFG, donkey._http, donkey._sync_http_client)

    clients = _handed_clients(adapter, attr)
    if attr in _routed_adapters():
        assert clients, f"{attr} is transport-routed but hands out no http client"
    else:
        assert not clients, f"{attr} now hands out an http client: add it to the routed set"
    for client in clients:
        assert not isinstance(client, DonkeyAsyncClient | DonkeyClient), (
            f"{attr} hands a framework the owning shared client"
        )
        if isinstance(client, httpx.AsyncClient):
            await client.aclose()
        else:
            client.close()

    assert not donkey._http.is_closed
    assert not donkey._sync_http_client().is_closed
    response = await donkey._http.post("https://proxy.example.com/p/x", json={"model": "m"})
    assert response.status_code == 200
    await donkey.aclose()


# --- real Strands ----------------------------------------------------------------


def _strands_agent(donkey: Donkey) -> Any:
    strands = pytest.importorskip("strands")
    pytest.importorskip("strands.models.openai")
    return strands.Agent(model=donkey.strands.model("gpt-x"), callback_handler=None)


async def test_strands_calls_keep_the_shared_client_open() -> None:
    donkey = _donkey()
    agent = _strands_agent(donkey)

    await agent.invoke_async("one")
    await agent.invoke_async("two")
    assert not donkey._http.is_closed
    pytest.importorskip("openai")
    reply = await donkey.openai().chat.completions.create(model="m", messages=MESSAGES)  # type: ignore[arg-type]
    assert reply.choices[0].message.content == "PONG"
    await donkey.aclose()


def test_two_sync_strands_calls_in_a_row_succeed() -> None:
    donkey = _donkey()
    agent = _strands_agent(donkey)

    agent("one")
    agent("two")
    assert not donkey._http.is_closed


def test_strands_does_not_stream_by_default() -> None:
    # #830: a Gemini-routed OpenAI-format proxy sends one whole chat.completion for
    # a streamed request, and Strands fails on it; streaming is opt-in.
    from donkey_kit.integrations.strands import StrandsAdapter

    donkey = _donkey()
    assert StrandsAdapter(CFG, donkey._http).connection_kwargs()["stream"] is False
    pytest.importorskip("strands.models.openai")
    assert donkey.strands.model("gpt-x").get_config()["stream"] is False
    assert donkey.strands.model("gpt-x", stream=True).get_config()["stream"] is True
