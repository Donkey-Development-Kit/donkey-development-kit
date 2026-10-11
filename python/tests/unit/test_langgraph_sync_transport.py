"""LangGraph's sync entry points go through the governed transport (#736, BG §1.8).

``ChatOpenAI`` holds two OpenAI clients: an async one for ``ainvoke``/``astream``
and a sync one for ``invoke``/``stream``. The adapter hands it the SDK's client
for both (``http_async_client`` / ``http_client``), so a sync call gets the same
correlation id, ``last_call``, ``simulate()`` and jwt handling as an async one.
Each contract below runs over the sync, async and streaming variants.

Skipped where the ``langgraph`` extra is absent.
"""

from __future__ import annotations

import asyncio
import json
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
from langchain_core.messages import HumanMessage


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


def _is_chat(request: httpx.Request) -> bool:
    """The adapter defaults to ``/chat/completions`` (#1043); ``use_responses_api=True``
    sends ``/responses``. Serve each route its own recorded body."""
    return request.url.path.endswith("/chat/completions")


def _success(request: httpx.Request) -> httpx.Response:
    fixture = load("chat-success" if _is_chat(request) else "success")
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


def _sse(*events: dict[str, Any]) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


# The committed stream sample stops after ``response.created``, so a streamed
# call needs a complete (if minimal) Responses API event sequence.
_RESPONSE = {
    "id": "resp_1",
    "object": "response",
    "created_at": 0,
    "model": "gpt-4o",
    "status": "completed",
    "output": [],
}
_STREAM = _sse(
    {"type": "response.created", "sequence_number": 0, "response": _RESPONSE},
    {
        "type": "response.output_text.delta",
        "sequence_number": 1,
        "item_id": "msg_1",
        "output_index": 0,
        "content_index": 0,
        "delta": "hi",
    },
    {
        "type": "response.completed",
        "sequence_number": 2,
        "response": {
            **_RESPONSE,
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        },
    },
)


def _success_or_stream(request: httpx.Request) -> httpx.Response:
    if not json.loads(request.content).get("stream"):
        return _success(request)
    if _is_chat(request):
        chat = load("chat-stream")
        headers = replay_headers(chat)
        headers["content-type"] = "text/event-stream"
        return httpx.Response(200, headers=headers, content=chat.body, request=request)
    headers = replay_headers(load("stream"))
    headers["content-type"] = "text/event-stream"
    return httpx.Response(200, headers=headers, content=_STREAM, request=request)


@pytest.mark.parametrize("call", _ALL, ids=_ALL_IDS)
async def test_last_call_observed_after_call(call: _Call) -> None:
    # ainvoke() runs the request in a task asyncio.gather() spawns with a copy
    # of this context; the record must still reach the caller (#850).
    donkey = Donkey(_cfg())
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(_success_or_stream))
    donkey._sync_http_client().governed_transport.replace_inner(
        httpx.MockTransport(_success_or_stream)
    )
    async with donkey:
        await call(donkey.langgraph("gpt-4o"))
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
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(respond))
    async with donkey:
        await donkey.langgraph("gpt-4o").ainvoke("hi")

    assert [r.headers.get("authorization") for r in seen] == ["Bearer jwt"]


# --- ainvoke() reaches last_call without breaking fan-out isolation (#850) ---


def _echo_rid(request: httpx.Request) -> httpx.Response:
    """The success fixture, with ``x-request-id`` set to the prompt text, so each
    call's record names the call that made it."""
    response = _success(request)
    sent = json.loads(request.content)
    rid = sent["messages" if _is_chat(request) else "input"][0]["content"]
    response.headers["x-request-id"] = rid if isinstance(rid, str) else rid[0]["text"]
    return response


def _echo_donkey() -> Donkey:
    donkey = Donkey(_cfg())
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(_echo_rid))
    return donkey


async def test_ainvoke_reports_the_call_it_made() -> None:
    donkey = _echo_donkey()
    async with donkey:
        model = donkey.langgraph("gpt-4o")
        await model.ainvoke("first")
        assert donkey.last_call.request_id == "first"
        await model.ainvoke("second")
        assert donkey.last_call.request_id == "second"


async def test_concurrent_ainvokes_each_read_their_own_call() -> None:
    donkey = _echo_donkey()
    async with donkey:
        model = donkey.langgraph("gpt-4o")

        async def one(rid: str) -> str | None:
            await model.ainvoke(rid)
            await asyncio.sleep(0)
            return donkey.last_call.request_id

        assert await asyncio.gather(one("a"), one("b")) == ["a", "b"]
        # The parent scattered the calls and made none itself (hazard #2).
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED


async def test_a_later_fan_out_does_not_overwrite_the_caller_s_record() -> None:
    donkey = _echo_donkey()
    async with donkey:
        model = donkey.langgraph("gpt-4o")
        await model.ainvoke("mine")
        await asyncio.gather(model.ainvoke("child-a"), model.ainvoke("child-b"))
        assert donkey.last_call.request_id == "mine"


async def test_a_batch_does_not_report_a_sibling_s_call() -> None:
    donkey = _echo_donkey()
    async with donkey:
        model = donkey.langgraph("gpt-4o")
        await model.agenerate([[HumanMessage("a")], [HumanMessage("b")]])
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED


async def test_caller_callbacks_are_kept() -> None:
    from langchain_core.callbacks import BaseCallbackHandler

    seen: list[str] = []

    class Spy(BaseCallbackHandler):
        def on_llm_end(self, response: Any, **kwargs: Any) -> None:
            seen.append("end")

    spy = Spy()
    callbacks = [spy]
    donkey = _echo_donkey()
    async with donkey:
        model = donkey.langgraph("gpt-4o", callbacks=callbacks)
        await model.ainvoke("hi")
        assert donkey.last_call.request_id == "hi"
    assert seen == ["end"]
    assert callbacks == [spy]  # the caller's list is not mutated
