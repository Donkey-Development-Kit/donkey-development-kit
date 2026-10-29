"""Refusal reaction handlers at the transport (BG §2.1, #208) and their safety
rule: a handler reacts but never un-refuses (#209).

Every IO case runs through both governed clients (as in
``test_transport_both_clients.py``), with ``httpx.MockTransport`` and the
captured refusal fixtures the simulator serves, so each refusal is the real
gateway shape. Sleeps are recorded, never taken.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest

from donkey_kit import (
    AgentKilled,
    ContentSafetyBlocked,
    Donkey,
    DonkeyConfig,
    DonkeyError,
    OnRefusal,
    PIIDetected,
    PolicyViolation,
    TokenBudgetExceeded,
)
from donkey_kit._testing import http_clients, swap_transport
from donkey_kit.core import RefusalContext
from donkey_kit.core.reactions import RefusalHandlers, _seconds
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient, async_client, sync_client
from donkey_kit.simulator.fixtures import replay_headers
from donkey_kit.simulator.inject import resolve_fixture

_URL = "https://proxy.example/chat/completions"
_CORRELATION = "x-correlation-id"
_CALL_ID = "x-donkey-request-id"
_CFG = DonkeyConfig(
    llm_proxy_url="https://proxy.example/",
    llm_proxy_client_id="cid-handlers",
    llm_proxy_client_secret="csecret-handlers",
    correlation_header=_CORRELATION,
    call_id_header=_CALL_ID,
    max_retries=0,
)
_BODY = {"model": "gpt-4o", "messages": [{"role": "user", "content": "my SSN is 078-05-1120"}]}
KINDS = ["async", "sync"]

Reply = Callable[[httpx.Request], httpx.Response]


def refused(error: type[DonkeyError], **headers: str) -> Reply:
    """A reply serving the captured fixture that classifies as ``error``."""
    fixture = resolve_fixture(error)

    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            fixture.status,
            headers={**replay_headers(fixture), **headers},
            content=fixture.body,
        )

    return reply


def budget(retry_after: float | None) -> Reply:
    """A token-budget 429 with exactly the reset time given (``None``: none)."""
    headers = {} if retry_after is None else {"retry-after": str(retry_after)}
    return lambda request: httpx.Response(429, headers=headers, json={"error": "budget"})


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


class Wire:
    """The scripted gateway: one reply per request, in order; records requests."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.sent: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        reply = self.replies[min(len(self.sent), len(self.replies)) - 1]
        return reply(request)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.sent]


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def record(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(async_client.asyncio, "sleep", record)
    monkeypatch.setattr(sync_client.time, "sleep", delays.append)
    return delays


def send(
    kind: str,
    handlers: RefusalHandlers,
    wire: Wire,
    *,
    body: object = _BODY,
    url: str = _URL,
) -> httpx.Response:
    transport = httpx.MockTransport(wire)
    if kind == "sync":
        with DonkeyClient(_CFG, transport=transport, reactions=handlers) as client:
            return client.post(url, json=body)

    async def run() -> httpx.Response:
        async with DonkeyAsyncClient(_CFG, None, transport=transport, reactions=handlers) as client:
            return await client.post(url, json=body)

    return asyncio.run(run())


# --- registration ----------------------------------------------------------------


@pytest.mark.parametrize("refusal", [DonkeyError, ValueError, "PIIDetected", None])
def test_only_a_policy_violation_can_be_handled(refusal: Any) -> None:
    with pytest.raises(TypeError, match="PolicyViolation subclass"):
        RefusalHandlers().on(refusal)


def test_agent_killed_cannot_be_handled() -> None:
    # BG §2.6: terminal, never retried, never handled by a 2.1 fallback.
    with pytest.raises(TypeError, match="AgentKilled is terminal"):
        RefusalHandlers().on(AgentKilled)


@pytest.mark.parametrize("max_times", [0, -1, True, 1.5])
def test_max_times_must_be_a_positive_int(max_times: Any) -> None:
    on = RefusalHandlers().on(TokenBudgetExceeded)
    with pytest.raises(ValueError, match="max_times"):
        on.wait_for_reset(max_times=max_times)
    with pytest.raises(ValueError, match="max_times"):
        on.fallback(model="m", max_times=max_times)
    with pytest.raises(ValueError, match="max_times"):
        on.call(lambda ctx: None, max_times=max_times)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("45m", 2700.0),
        ("30s", 30.0),
        ("1h", 3600.0),
        ("1h30m", 5400.0),
        ("500ms", 0.5),
        (" 2M ", 120.0),
        (timedelta(minutes=2), 120.0),
        (90, 90.0),
        (1.5, 1.5),
    ],
)
def test_max_wait_parses(value: Any, seconds: float) -> None:
    assert _seconds(value) == seconds


@pytest.mark.parametrize("value", ["", "45", "m", "45 minutes", "-5m", "0s", 0, -1, True, None])
def test_max_wait_rejects_what_is_not_a_positive_duration(value: Any) -> None:
    with pytest.raises(ValueError, match="max_wait"):
        RefusalHandlers().on(TokenBudgetExceeded).wait_for_reset(max_wait=value)


def test_bad_handler_arguments_are_refused_at_registration() -> None:
    on = RefusalHandlers().on(PIIDetected)
    with pytest.raises(ValueError, match="model"):
        on.fallback(model="")
    with pytest.raises(TypeError, match="callable"):
        on.call("mask")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="callable or a queue"):
        on.escalate(to=object())  # type: ignore[arg-type]


def test_donkey_on_registers_on_the_runtime_both_clients_share() -> None:
    donkey = Donkey(_CFG)
    try:
        builder = donkey.on(PIIDetected)
        assert isinstance(builder, OnRefusal)
        builder.escalate(to=lambda violation: None)
        async_http, sync_http = http_clients(donkey)
        assert async_http._reactions is sync_http._reactions
        assert async_http._reactions
        with pytest.raises(TypeError):
            donkey.on(AgentKilled)
    finally:
        donkey.close()


# --- the safety rule (BG §2.1, #209) ---------------------------------------------


def test_the_handler_api_has_exactly_four_verbs_and_none_suppresses() -> None:
    # Adding a verb means changing this test, on purpose (#209): a verb that
    # returns success for a refused request is client-side enforcement.
    verbs = {name for name in dir(OnRefusal) if not name.startswith("_")}
    assert verbs == {"call", "escalate", "fallback", "wait_for_reset"}
    assert {name for name in dir(RefusalContext) if not name.startswith("_")} == {
        "body",
        "resend",
        "violation",
    }
    for forbidden in ("ignore", "suppress", "allow", "accept", "succeed", "skip"):
        assert not hasattr(OnRefusal, forbidden)
        assert not hasattr(RefusalContext, forbidden)


def _register(handlers: RefusalHandlers, verb: str) -> None:
    on = handlers.on(PolicyViolation)
    if verb == "wait_for_reset":
        on.wait_for_reset(max_wait="1h", max_times=3)
    elif verb == "fallback":
        on.fallback(model="other-model", max_times=3)
    elif verb == "call":
        on.call(lambda ctx: ctx.resend(json={**(ctx.body or {}), "masked": True}), max_times=3)
    elif verb == "call-returns":
        on.call(lambda ctx: None)
    elif verb == "call-returns-a-value":
        on.call(lambda ctx: httpx.Response(200))
    elif verb == "call-raises":
        on.call(lambda ctx: 1 / 0)
    else:
        on.escalate(to=lambda violation: None)


_VERBS = [
    "wait_for_reset",
    "fallback",
    "call",
    "call-returns",
    "call-returns-a-value",
    "call-raises",
    "escalate",
]


@pytest.mark.parametrize("verb", _VERBS)
@pytest.mark.parametrize("kind", KINDS)
def test_no_verb_turns_a_refusal_into_a_success(
    kind: str, verb: str, slept: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    # The gateway refuses everything: whatever the handler does, the caller
    # gets a refusal, and every extra request was a new governed send.
    handlers = RefusalHandlers()
    _register(handlers, verb)
    wire = Wire(budget(5))
    with caplog.at_level(logging.WARNING, logger="donkey_kit"):
        response = send(kind, handlers, wire)
    assert response.status_code == 429
    assert response.request is wire.sent[-1]
    resends = 3 if verb in {"wait_for_reset", "fallback", "call"} else 0
    if verb == "fallback":
        resends = 1  # the second refusal is already from the fallback model
    assert len(wire.sent) == 1 + resends


@pytest.mark.parametrize("verb", _VERBS)
@pytest.mark.parametrize("kind", KINDS)
def test_a_success_only_ever_comes_from_a_new_governed_request(
    kind: str, verb: str, slept: list[float]
) -> None:
    # The gateway allows a second request: a 2xx reaches the caller only when a
    # handler issued one, and it is that new request's response.
    handlers = RefusalHandlers()
    _register(handlers, verb)
    wire = Wire(budget(5), ok)
    response = send(kind, handlers, wire)
    first = wire.sent[0]
    if verb in {"wait_for_reset", "fallback", "call"}:
        assert response.status_code == 200
        assert len(wire.sent) == 2
        assert response.request is wire.sent[1] is not first
        assert wire.sent[1].headers[_CALL_ID] != first.headers[_CALL_ID]
        assert wire.sent[1].headers[_CORRELATION] == first.headers[_CORRELATION]
    else:
        assert response.status_code == 429
        assert wire.sent == [first]


# --- matching --------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_the_most_specific_handler_wins(kind: str) -> None:
    handlers = RefusalHandlers()
    escalated: list[PolicyViolation] = []
    called: list[PolicyViolation] = []
    handlers.on(PolicyViolation).escalate(to=escalated.append)
    handlers.on(PIIDetected).call(lambda ctx: called.append(ctx.violation))

    send(kind, handlers, Wire(refused(PIIDetected)))
    assert [type(v) for v in called] == [PIIDetected]
    assert escalated == []

    send(kind, handlers, Wire(refused(ContentSafetyBlocked)))
    assert [type(v) for v in escalated] == [ContentSafetyBlocked]


@pytest.mark.parametrize("kind", KINDS)
def test_the_catch_all_never_sees_agent_killed(kind: str) -> None:
    handlers = RefusalHandlers()
    escalated: list[PolicyViolation] = []
    handlers.on(PolicyViolation).escalate(to=escalated.append)
    wire = Wire(refused(AgentKilled), ok)
    response = send(kind, handlers, wire)
    assert response.status_code == 403
    assert len(wire.sent) == 1
    assert escalated == []


def test_a_second_registration_replaces_the_first() -> None:
    handlers = RefusalHandlers()
    handlers.on(PIIDetected).escalate(to=lambda violation: None)
    handlers.on(PIIDetected).fallback(model="m")
    wire = Wire(refused(PIIDetected), ok)
    assert send("async", handlers, wire).status_code == 200
    assert wire.bodies()[1]["model"] == "m"


@pytest.mark.parametrize("kind", KINDS)
def test_without_handlers_the_refusal_is_returned_untouched(kind: str) -> None:
    wire = Wire(refused(PIIDetected), ok)
    response = send(kind, RefusalHandlers(), wire)
    assert response.status_code == 403
    assert len(wire.sent) == 1


# --- wait_for_reset --------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_wait_for_reset_waits_the_reported_reset_then_resends(
    kind: str, slept: list[float]
) -> None:
    handlers = RefusalHandlers()
    handlers.on(TokenBudgetExceeded).wait_for_reset(max_wait="45m")
    wire = Wire(budget(12), ok)
    response = send(kind, handlers, wire)
    assert response.status_code == 200
    assert slept == [12.0]
    assert wire.bodies() == [_BODY, _BODY]


@pytest.mark.parametrize("kind", KINDS)
def test_wait_for_reset_reraises_a_refusal_with_no_reset_time(
    kind: str, slept: list[float]
) -> None:
    # BG §2.1: never a no-op wait and a retry at zero delay.
    handlers = RefusalHandlers()
    handlers.on(TokenBudgetExceeded).wait_for_reset()
    wire = Wire(budget(None), ok)
    assert send(kind, handlers, wire).status_code == 429
    assert len(wire.sent) == 1
    assert slept == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_reset_past_max_wait_propagates(kind: str, slept: list[float]) -> None:
    handlers = RefusalHandlers()
    handlers.on(TokenBudgetExceeded).wait_for_reset(max_wait="1m")
    wire = Wire(budget(120), ok)
    assert send(kind, handlers, wire).status_code == 429
    assert slept == []


@pytest.mark.parametrize("kind", KINDS)
def test_max_wait_bounds_the_total_wait_of_one_call(kind: str, slept: list[float]) -> None:
    handlers = RefusalHandlers()
    handlers.on(TokenBudgetExceeded).wait_for_reset(max_wait=60, max_times=5)
    wire = Wire(budget(40), budget(40), ok)
    assert send(kind, handlers, wire).status_code == 429
    assert slept == [40.0]
    assert len(wire.sent) == 2


@pytest.mark.parametrize("kind", KINDS)
def test_max_times_breach_reraises_the_latest_refusal(kind: str, slept: list[float]) -> None:
    handlers = RefusalHandlers()
    handlers.on(TokenBudgetExceeded).wait_for_reset(max_times=2)
    wire = Wire(budget(1), budget(2), budget(3), ok)
    response = send(kind, handlers, wire)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "3"
    assert slept == [1.0, 2.0]
    assert len(wire.sent) == 3


@pytest.mark.parametrize("kind", KINDS)
def test_bounds_count_per_logical_call(kind: str, slept: list[float]) -> None:
    handlers = RefusalHandlers()
    handlers.on(TokenBudgetExceeded).wait_for_reset(max_times=1)
    for _ in range(2):
        assert send(kind, handlers, Wire(budget(1), ok)).status_code == 200
    assert slept == [1.0, 1.0]


# --- call ------------------------------------------------------------------------


def mask(ctx: RefusalContext) -> None:
    body = ctx.body or {}
    for message in body.get("messages", []):
        message["content"] = "my SSN is [MASKED]"
    ctx.resend(json=body)


@pytest.mark.parametrize("kind", KINDS)
def test_call_resends_the_body_the_handler_built(kind: str) -> None:
    handlers = RefusalHandlers()
    handlers.on(PIIDetected).call(mask, max_times=1)
    wire = Wire(refused(PIIDetected), ok)
    response = send(kind, handlers, wire)
    assert response.status_code == 200
    first, second = wire.bodies()
    assert first == _BODY  # the handler's copy changed nothing on the original
    assert second["messages"][0]["content"] == "my SSN is [MASKED]"
    assert int(wire.sent[1].headers["content-length"]) == len(wire.sent[1].content)


@pytest.mark.parametrize("kind", KINDS)
def test_a_masked_resend_that_is_refused_again_propagates(kind: str) -> None:
    handlers = RefusalHandlers()
    handlers.on(PIIDetected).call(mask, max_times=1)
    wire = Wire(refused(PIIDetected))
    assert send(kind, handlers, wire).status_code == 403
    assert len(wire.sent) == 2


@pytest.mark.parametrize("kind", KINDS)
def test_a_handler_that_returns_without_resending_propagates(kind: str) -> None:
    # #209 AC: no raise and no new governed call means the refusal propagates.
    handlers = RefusalHandlers()
    seen: list[PolicyViolation] = []
    handlers.on(PIIDetected).call(lambda ctx: seen.append(ctx.violation))
    wire = Wire(refused(PIIDetected), ok)
    response = send(kind, handlers, wire)
    assert response.status_code == 403
    assert len(wire.sent) == 1
    assert seen[0].response is response


@pytest.mark.parametrize("kind", KINDS)
def test_a_handler_that_raises_does_not_mask_the_refusal(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(ctx: RefusalContext) -> None:
        raise RuntimeError("the masking service is down")

    handlers = RefusalHandlers()
    handlers.on(PIIDetected).call(broken)
    wire = Wire(refused(PIIDetected), ok)
    with caplog.at_level(logging.WARNING, logger="donkey_kit.core.reactions"):
        response = send(kind, handlers, wire)
    assert response.status_code == 403
    assert len(wire.sent) == 1
    assert "on(PIIDetected).call handler raised RuntimeError" in caplog.text


@pytest.mark.parametrize("kind", KINDS)
def test_resend_twice_is_a_handler_error(kind: str) -> None:
    def twice(ctx: RefusalContext) -> None:
        ctx.resend(json={"model": "a"})
        ctx.resend(json={"model": "b"})

    handlers = RefusalHandlers()
    handlers.on(PIIDetected).call(twice)
    wire = Wire(refused(PIIDetected), ok)
    assert send(kind, handlers, wire).status_code == 403
    assert len(wire.sent) == 1


def test_an_async_handler_is_awaited_on_the_async_client() -> None:
    async def amask(ctx: RefusalContext) -> None:
        await asyncio.sleep(0)
        mask(ctx)

    handlers = RefusalHandlers()
    handlers.on(PIIDetected).call(amask)
    wire = Wire(refused(PIIDetected), ok)
    assert send("async", handlers, wire).status_code == 200


def test_an_async_handler_propagates_with_a_warning_on_the_blocking_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def amask(ctx: RefusalContext) -> None:
        mask(ctx)  # never runs: the blocking client cannot await it

    handlers = RefusalHandlers()
    handlers.on(PIIDetected).call(amask)
    wire = Wire(refused(PIIDetected), ok)
    with caplog.at_level(logging.WARNING, logger="donkey_kit.core.reactions"):
        assert send("sync", handlers, wire).status_code == 403
    assert len(wire.sent) == 1
    assert "blocking client cannot await" in caplog.text


# --- fallback --------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_fallback_sends_a_new_request_to_the_fallback_model(kind: str) -> None:
    handlers = RefusalHandlers()
    handlers.on(ContentSafetyBlocked).fallback(model="internal-safe-model")
    wire = Wire(refused(ContentSafetyBlocked), ok)
    response = send(kind, handlers, wire)
    assert response.status_code == 200
    assert [b["model"] for b in wire.bodies()] == ["gpt-4o", "internal-safe-model"]
    assert wire.bodies()[1]["messages"] == _BODY["messages"]


@pytest.mark.parametrize("kind", KINDS)
def test_fallback_without_a_body_model_propagates(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    # A native Gemini request names the model in its path, not its body.
    handlers = RefusalHandlers()
    handlers.on(ContentSafetyBlocked).fallback(model="internal-safe-model")
    wire = Wire(refused(ContentSafetyBlocked), ok)
    url = "https://proxy.example/v1beta/models/gemini-3.8-flash:generateContent"
    with caplog.at_level(logging.WARNING, logger="donkey_kit.core.reactions"):
        response = send(kind, handlers, wire, body={"contents": []}, url=url)
    assert response.status_code == 403
    assert len(wire.sent) == 1
    assert "needs a JSON body naming a model" in caplog.text


# --- escalate --------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_escalate_hands_over_the_refusal_and_lets_it_propagate(kind: str) -> None:
    hitl: queue.Queue[PolicyViolation] = queue.Queue()
    handlers = RefusalHandlers()
    handlers.on(PolicyViolation).escalate(to=hitl)
    wire = Wire(refused(PIIDetected), ok)
    response = send(kind, handlers, wire)
    assert response.status_code == 403
    assert len(wire.sent) == 1
    escalated = hitl.get_nowait()
    assert isinstance(escalated, PIIDetected)
    assert escalated.response is response


@pytest.mark.parametrize("kind", KINDS)
def test_an_escalation_sink_that_raises_does_not_mask_the_refusal(kind: str) -> None:
    def full(violation: PolicyViolation) -> None:
        raise queue.Full

    handlers = RefusalHandlers()
    handlers.on(PolicyViolation).escalate(to=full)
    assert send(kind, handlers, Wire(refused(PIIDetected))).status_code == 403


# --- streaming and the Donkey surface ---------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_streamed_refusal_is_resent_as_a_stream(kind: str) -> None:
    sse = b'data: {"choices": []}\n\ndata: [DONE]\n\n'

    def stream_ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

    handlers = RefusalHandlers()
    handlers.on(ContentSafetyBlocked).fallback(model="internal-safe-model")
    wire = Wire(refused(ContentSafetyBlocked), stream_ok)
    transport = httpx.MockTransport(wire)
    body = {**_BODY, "stream": True}
    if kind == "sync":
        with DonkeyClient(_CFG, transport=transport, reactions=handlers) as client:
            with client.stream("POST", _URL, json=body) as response:
                assert response.status_code == 200
                assert response.read() == sse
    else:

        async def run() -> None:
            async with DonkeyAsyncClient(
                _CFG, None, transport=transport, reactions=handlers
            ) as client:
                async with client.stream("POST", _URL, json=body) as response:
                    assert response.status_code == 200
                    assert await response.aread() == sse

        asyncio.run(run())
    assert [b["model"] for b in wire.bodies()] == ["gpt-4o", "internal-safe-model"]


async def test_donkey_on_applies_to_every_call_of_that_donkey() -> None:
    # Scenario A: registered once, applied at the transport, no call-site code.
    async with Donkey(_CFG) as donkey:
        donkey.on(PIIDetected).call(mask, max_times=1)
        wire = Wire(ok)
        for client in http_clients(donkey):
            swap_transport(client, httpx.MockTransport(wire))
        with donkey.simulate(PIIDetected, times=1):
            response = await donkey.http_client().post(_URL, json=_BODY)
        assert response.status_code == 200
        assert len(wire.sent) == 1  # the simulator served the refusal, the wire the resend
        assert json.loads(wire.sent[0].content)["messages"][0]["content"] == "my SSN is [MASKED]"
