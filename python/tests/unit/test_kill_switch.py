"""Kill-switch awareness: the gateway kills, the SDK stops cleanly (BG §2.6, #220).

The Agent Kill Switch's ``403`` (VERIFIED LIVE, docs/verified-apis.md §4, #694)
classifies as :class:`AgentKilled`. These pin what happens next:

* the ``Budget`` latches it, so ``pace()`` and ``wait_for_reset()`` raise it,
  and a task already asleep in ``wait_for_reset()`` wakes and raises it, from
  any thread;
* on both governed clients the kill is sent once, never retried, handed to the
  budget, and its span is exported before ``send()`` returns.

No wall clock: the long resets here are cut short by the kill itself.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from donkey_kit.core import spanflush, telemetry
from donkey_kit.core.budget import Budget
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import AgentKilled, BudgetReserveReached, DonkeyError, classify
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient, async_client, sync_client
from donkey_kit.simulator import fixtures

_URL = "https://proxy.example/chat/completions"
_CFG = DonkeyConfig(
    llm_proxy_url="https://proxy.example/",
    llm_proxy_client_id="cid-kill",
    llm_proxy_client_secret="csecret-kill",
    max_retries=3,
)
KINDS = ["async", "sync"]


def _captured(shape: str) -> httpx.Response:
    """The committed live capture for ``shape``, as the gateway sent it."""
    fx = fixtures.load(shape)
    headers = fixtures.replay_headers(fx)
    if fx.content_type is not None:
        headers["content-type"] = fx.content_type
    return httpx.Response(fx.status, headers=headers, content=fx.body)


def _typed(shape: str) -> DonkeyError:
    return classify(_captured(shape))


def _killed() -> AgentKilled:
    error = _typed("agent-killed")
    assert isinstance(error, AgentKilled)
    return error


def _exhausted(reset_ms: int = 3_600_000) -> Budget:
    """A budget whose token window is spent and resets an hour from now."""
    budget = Budget()
    budget.observe(
        httpx.Response(
            429,
            headers={
                "x-token-limit": "100",
                "x-token-remaining": "0",
                "x-token-reset": str(reset_ms),
            },
        )
    )
    return budget


async def _pace_loop(budget: Budget, work: Callable[[], Any]) -> None:
    """The documented pacing loop (``Budget.pace``), verbatim."""
    while True:
        try:
            async with budget.pace():
                await work()
        except BudgetReserveReached as exc:
            if exc.reset_at is None:
                raise
            await budget.wait_for_reset(window=exc.window)
            continue
        break


async def _until_asleep(budget: Budget) -> None:
    while not budget._sleeps:
        await asyncio.sleep(0)


# --- Budget ----------------------------------------------------------------------


async def test_once_killed_pace_raises_and_never_runs_the_block() -> None:
    budget = Budget()
    budget.observe_refusal(_killed())
    ran = False
    with pytest.raises(AgentKilled):
        async with budget.pace():
            ran = True
    assert ran is False


async def test_a_kill_is_not_a_reserve_refusal_so_the_pace_loop_ends() -> None:
    budget = Budget()
    calls = 0

    async def work() -> None:
        nonlocal calls
        calls += 1
        budget.observe_refusal(_killed())  # the transport does this on the 403
        raise _killed()

    with pytest.raises(AgentKilled):
        await asyncio.wait_for(_pace_loop(budget, work), timeout=5)
    assert calls == 1


async def test_other_refusals_leave_pacing_alone() -> None:
    budget = Budget()
    budget.observe_refusal(_typed("pii-detected"))
    budget.observe_refusal(_typed("token-rate-limit"))
    async with budget.pace():
        pass
    await budget.wait_for_reset()


async def test_wait_for_reset_raises_once_killed() -> None:
    budget = _exhausted()
    budget.observe_refusal(_killed())
    with pytest.raises(AgentKilled):
        await asyncio.wait_for(budget.wait_for_reset(), timeout=5)


async def test_a_kill_wakes_a_task_asleep_in_wait_for_reset() -> None:
    budget = _exhausted()

    async def work() -> None:
        raise AssertionError("the spent window must refuse before the work runs")

    sibling = asyncio.ensure_future(_pace_loop(budget, work))
    await _until_asleep(budget)
    budget.observe_refusal(_killed())
    with pytest.raises(AgentKilled):
        await asyncio.wait_for(sibling, timeout=5)  # not the hour the reset is due in
    assert not budget._sleeps


async def test_a_kill_from_another_thread_wakes_the_sleep() -> None:
    # The sync client feeds the same Budget from the caller's thread.
    budget = _exhausted()
    sleeper = asyncio.ensure_future(budget.wait_for_reset())
    await _until_asleep(budget)
    await asyncio.to_thread(budget.observe_refusal, _killed())
    with pytest.raises(AgentKilled):
        await asyncio.wait_for(sleeper, timeout=5)


async def test_a_cancelled_sleep_is_forgotten() -> None:
    budget = _exhausted()
    sleeper = asyncio.ensure_future(budget.wait_for_reset())
    await _until_asleep(budget)
    sleeper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sleeper
    assert not budget._sleeps
    budget.observe_refusal(_killed())  # nothing left to wake: no error


async def test_each_raise_is_its_own_copy_of_the_first_kill() -> None:
    budget = Budget()
    first = _killed()
    budget.observe_refusal(first)
    budget.observe_refusal(AgentKilled("a later kill"))
    raised = []
    for _ in range(2):
        with pytest.raises(AgentKilled) as info:
            async with budget.pace():
                pass
        raised.append(info.value)
    assert raised[0] is not raised[1]
    assert first not in raised
    assert {str(e) for e in raised} == {str(first)}
    assert {e.remediation for e in raised} == {first.remediation}


# --- both governed clients -------------------------------------------------------


@pytest.fixture
def flushes(monkeypatch: pytest.MonkeyPatch) -> list[None]:
    calls: list[None] = []
    monkeypatch.setattr(spanflush, "flush", lambda: calls.append(None))
    monkeypatch.setattr(async_client.asyncio, "sleep", _instant)
    monkeypatch.setattr(sync_client.time, "sleep", lambda _delay: None)
    return calls


async def _instant(_delay: float) -> None:
    return None


def _send(
    kind: str, reply: httpx.Response, budget: Budget, cfg: DonkeyConfig = _CFG
) -> tuple[httpx.Response, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    transport = httpx.MockTransport(handler)
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    if kind == "sync":
        with DonkeyClient(cfg, budget=budget, transport=transport) as client:
            return client.post(_URL, json=body), sent

    async def run() -> httpx.Response:
        async with DonkeyAsyncClient(cfg, None, budget=budget, transport=transport) as client:
            return await client.post(_URL, json=body)

    return asyncio.run(run()), sent


@pytest.mark.parametrize("kind", KINDS)
def test_a_kill_is_sent_once_latched_and_flushed(kind: str, flushes: list[None]) -> None:
    budget = Budget()
    response, sent = _send(kind, _captured("agent-killed"), budget)
    assert response.status_code == 403
    assert len(sent) == 1  # max_retries=3, and still never re-sent
    assert response.headers["x-should-retry"] == "false"  # nor by the provider SDK
    assert flushes == [None]
    with pytest.raises(AgentKilled):
        asyncio.run(budget.wait_for_reset())


@pytest.mark.parametrize("kind", KINDS)
def test_another_refusal_is_neither_latched_nor_flushed(kind: str, flushes: list[None]) -> None:
    budget = Budget()
    response, _ = _send(kind, _captured("pii-detected"), budget)
    assert response.status_code == 403
    assert flushes == []
    asyncio.run(budget.wait_for_reset())


@pytest.mark.parametrize("kind", KINDS)
def test_with_telemetry_off_a_kill_latches_but_has_no_spans_to_flush(
    kind: str, flushes: list[None]
) -> None:
    budget = Budget()
    cfg = dataclasses.replace(_CFG, telemetry=False)
    _send(kind, _captured("agent-killed"), budget, cfg)
    assert flushes == []
    with pytest.raises(AgentKilled):
        asyncio.run(budget.wait_for_reset())


@pytest.mark.parametrize("kind", KINDS)
def test_the_kill_span_is_exported_before_send_returns(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real batch processor that would not export for an hour: only the
    flush on the kill path gets the span out before the caller unwinds."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=3_600_000))
    monkeypatch.setattr(telemetry, "_scoped_tracer", spanflush.keep(provider).get_tracer("t"))
    monkeypatch.setattr(telemetry, "_host_provider_set", lambda: False)
    try:
        _send(kind, _captured("agent-killed"), Budget())
        spans = exporter.get_finished_spans()
        assert [s.attributes[telemetry.DONKEY_POLICY_TYPE] for s in spans] == ["agent_killed"]
    finally:
        provider.shutdown()


# --- spanflush -------------------------------------------------------------------


class _Provider:
    def __init__(self, error: Exception | None = None) -> None:
        self.flushed: list[int] = []
        self._error = error

    def force_flush(self, timeout_millis: int) -> bool:
        self.flushed.append(timeout_millis)
        if self._error is not None:
            raise self._error
        return True


def test_flush_is_bounded_and_reaches_the_kept_provider() -> None:
    pytest.importorskip("opentelemetry")
    kept = spanflush.keep(_Provider())
    spanflush.flush()
    assert kept.flushed == [spanflush.FLUSH_TIMEOUT_MILLIS]


def test_a_failing_export_never_masks_the_kill() -> None:
    pytest.importorskip("opentelemetry")
    kept = spanflush.keep(_Provider(RuntimeError("collector down")))
    spanflush.flush()
    assert kept.flushed == [spanflush.FLUSH_TIMEOUT_MILLIS]


def test_flush_with_nothing_kept_is_a_no_op() -> None:
    # The API's placeholder global provider has no force_flush.
    spanflush.flush()


def test_the_scoped_otlp_provider_is_kept_for_the_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider

    scoped = TracerProvider()
    monkeypatch.setattr(telemetry, "_build_tracer_provider", lambda _config: scoped)
    monkeypatch.setattr(telemetry, "_host_provider_set", lambda: False)
    try:
        telemetry.configure_otlp_export(DonkeyConfig())
        assert spanflush._owned is scoped
    finally:
        scoped.shutdown()
