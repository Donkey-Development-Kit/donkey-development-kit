"""LangGraph's sync entry points go through the governed transport (#736, BG §1.8).

``ChatOpenAI`` holds two OpenAI clients: an async one for ``ainvoke``/``astream``
and a sync one for ``invoke``/``stream``. The adapter hands it the SDK's client
for both (``http_async_client`` / ``http_client``), so a sync call gets the same
correlation id, ``last_call``, ``simulate()`` and jwt handling as an async one.
Each contract below runs over the sync, async and streaming variants.

Skipped where the ``langgraph`` extra is absent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.errors import ConfigError, PIIDetected
from donkey_kit.core.lastcall import LastCallStatus
from donkey_kit.integrations.langgraph import typed_refusals
from donkey_kit.simulator.fixtures import load, replay_headers

pytest.importorskip("langchain_openai")


def _cfg(**kw: Any) -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        **kw,
    )


async def _invoke(model: Any) -> Any:
    return model.invoke("hi")


async def _ainvoke(model: Any) -> Any:
    return await model.ainvoke("hi")


async def _stream(model: Any) -> Any:
    return list(model.stream("hi"))


async def _astream(model: Any) -> Any:
    return [chunk async for chunk in model.astream("hi")]


_Call = Callable[[Any], Any]
_ALL = [_invoke, _ainvoke, _stream, _astream]
_ALL_IDS = ["invoke", "ainvoke", "stream", "astream"]
_SYNC = [_invoke, _stream]
_SYNC_IDS = ["invoke", "stream"]


@pytest.fixture
def network_sends(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """Record (and refuse) every request that reaches httpx's real network
    transports, sync or async."""
    sends: list[httpx.Request] = []

    def sync_send(_self: object, request: httpx.Request) -> httpx.Response:
        sends.append(request)
        raise httpx.ConnectError("network disabled in this test", request=request)

    async def async_send(_self: object, request: httpx.Request) -> httpx.Response:
        sends.append(request)
        raise httpx.ConnectError("network disabled in this test", request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", sync_send)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_send)
    yield sends


def _success(request: httpx.Request) -> httpx.Response:
    fixture = load("success")
    headers = replay_headers(fixture)
    headers["content-type"] = "application/json"
    return httpx.Response(200, headers=headers, content=fixture.body, request=request)


@pytest.mark.parametrize("call", _ALL, ids=_ALL_IDS)
async def test_simulated_refusal_is_typed_with_no_network(
    call: _Call, network_sends: list[httpx.Request]
) -> None:
    donkey = Donkey(_cfg())
    async with donkey:
        with donkey.simulate(PIIDetected):
            # Built inside the block, so the blocking client is first asked for
            # here: simulate() must already have swapped it.
            model = donkey.langgraph("gpt-4o")
            with pytest.raises(PIIDetected) as excinfo:
                with donkey.run(id="run-736"), typed_refusals():
                    await call(model)

    assert excinfo.value.correlation_id == "run-736"
    assert network_sends == []


async def test_last_call_observed_after_invoke() -> None:
    donkey = Donkey(_cfg())
    donkey._sync_http_client()._swap_transport(httpx.MockTransport(_success))
    async with donkey:
        donkey.langgraph("gpt-4o").invoke("hi")
        assert donkey.last_call.status is LastCallStatus.OBSERVED


def _jwt_donkey() -> Donkey:
    cfg = _cfg(llm_proxy_auth="jwt", llm_proxy_wallet_client_id="wallet-42")
    return Donkey(cfg, llm_auth=StaticToken("jwt"))


@pytest.mark.parametrize("call", _SYNC, ids=_SYNC_IDS)
async def test_jwt_sync_call_raises_same_config_error_as_llm_client(
    call: _Call, network_sends: list[httpx.Request]
) -> None:
    donkey = _jwt_donkey()
    async with donkey:
        with pytest.raises(ConfigError) as from_llm:
            donkey.llm.client(sync=True)
        with pytest.raises(ConfigError) as from_langgraph:
            await call(donkey.langgraph("gpt-4o"))

    assert str(from_langgraph.value) == str(from_llm.value)
    assert "async-only" in str(from_langgraph.value)
    assert network_sends == []


async def test_jwt_async_call_still_works() -> None:
    # The sync guard fails at call time, not when the model is built, so the
    # same ChatOpenAI stays usable through ainvoke() in jwt mode.
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success(request)

    donkey = _jwt_donkey()
    donkey._http._swap_transport(httpx.MockTransport(respond))
    async with donkey:
        await donkey.langgraph("gpt-4o").ainvoke("hi")

    assert [r.headers.get("authorization") for r in seen] == ["Bearer jwt"]
