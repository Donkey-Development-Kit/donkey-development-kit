"""The run ledger (#613): ``donkey.run()`` keeps every governed call, refusal
and token total for the block. Base-only surface: httpx + MockTransport.

Covers the issue's acceptance criteria: populated from the same response path
as ``last_call`` (refusals from ``classify()`` included), concurrent runs
isolated, child tasks share, nested runs restore, the cap applies, refusals
recorded, unavailable rather than empty on a non-observing surface.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading

import httpx
import pytest

from donkey_kit import (
    ConfigError,
    Donkey,
    PIIDetected,
    PolicyViolation,
    RunLedger,
    TokenBudgetExceeded,
)
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.correlation import run_scope
from donkey_kit.core.lastcall import LastCall, LastCallStatus
from donkey_kit.core.runledger import current_run_ledger, record_call
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient

_CFG = DonkeyConfig(llm_proxy_url="https://proxy")
_USAGE = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}


def _handler(request: httpx.Request) -> httpx.Response:
    """200 with usage, echoing ``x-test-rid`` as the provider request id; a
    ``"pii"`` prompt gets the gateway's PII 403 and ``"budget"`` its token 429."""
    body = request.content.decode()
    if '"pii"' in body:
        return httpx.Response(403, json={"error": {"type": "pii_detected", "message": "x"}})
    if '"budget"' in body:
        return httpx.Response(429)
    rid = request.headers.get("x-test-rid", "req-1")
    return httpx.Response(200, headers={"x-request-id": rid}, json={"usage": _USAGE})


def _client() -> DonkeyAsyncClient:
    return DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(_handler))


async def _call(client: DonkeyAsyncClient, prompt: str = "hi", rid: str = "req-1") -> int:
    resp = await client.post(
        "https://proxy/chat",
        json={"model": "gpt-4o", "input": prompt},
        headers={"x-test-rid": rid},
    )
    return resp.status_code


def _ids(ledger: RunLedger | None) -> list[str | None]:
    assert ledger is not None
    return [c.request_id for c in ledger.calls]


# --- populated from the transport ---------------------------------------------------


def test_outside_a_run_there_is_no_ledger() -> None:
    assert current_run_ledger() is None
    record_call(LastCall(status=LastCallStatus.OBSERVED))  # a no-op, never raises
    assert current_run_ledger() is None


async def test_every_call_and_its_usage_land_in_the_run() -> None:
    scope = run_scope("run-1")
    async with _client() as client, scope as run_id:
        await _call(client, rid="a")
        await _call(client, rid="b")
        ledger = current_run_ledger()

    assert run_id == "run-1"  # the block still yields the id
    assert ledger is not None and ledger.run_id == "run-1"
    assert _ids(ledger) == ["a", "b"]
    assert ledger.call_count == 2
    assert ledger.usage.input_tokens == 22
    assert ledger.usage.output_tokens == 6
    assert ledger.usage.total_tokens == 28
    assert ledger.usage.calls_with_usage == 2
    assert ledger.available


async def test_the_ledger_outlives_the_block_on_the_scope() -> None:
    scope = run_scope("run-1")
    assert scope.ledger is None  # not entered yet
    async with _client() as client, scope:
        await _call(client)
    assert current_run_ledger() is None  # unbound
    assert scope.ledger is not None and scope.ledger.call_count == 1


async def test_non_model_calls_are_not_recorded() -> None:
    async with _client() as client, run_scope("run-1") as _:
        await client.get("https://proxy/models")
        ledger = current_run_ledger()
    assert ledger is not None and ledger.call_count == 0


def test_the_sync_transport_records_identically() -> None:
    def body() -> RunLedger | None:
        client = DonkeyClient(_CFG, transport=httpx.MockTransport(_handler))
        with client, run_scope("run-1"):
            client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
            return current_run_ledger()

    ledger = contextvars.copy_context().run(body)
    assert ledger is not None and ledger.call_count == 1
    assert ledger.usage.total_tokens == 14


async def test_a_streamed_call_s_usage_reaches_the_totals() -> None:
    chunks = [
        b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n',
        b'data: {"choices":[{"delta":{}}],'
        b'"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
        b"data: [DONE]\n\n",
    ]

    class _SSE(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in chunks:
                yield chunk

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_SSE())

    client = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler))
    async with client, run_scope("run-1"):
        req = client.build_request("POST", "https://proxy/chat", json={"model": "m"})
        resp = await client.send(req, stream=True)
        mid = current_run_ledger()
        _ = [line async for line in resp.aiter_lines()]
        await resp.aclose()
        ledger = current_run_ledger()

    assert mid is not None and mid.call_count == 1 and mid.usage.total_tokens == 0
    assert ledger is not None and ledger.call_count == 1
    assert ledger.usage.total_tokens == 7
    assert ledger.usage.calls_with_usage == 1
    assert ledger.calls[0].total_tokens == 7  # the entry was swapped, not appended


# --- refusals --------------------------------------------------------------------


async def test_refusals_are_recorded_typed_even_when_caught() -> None:
    async with _client() as client, run_scope("run-1"):
        assert await _call(client, "pii") == 403  # the caller never raised it
        assert await _call(client, "budget") == 429
        await _call(client)
        ledger = current_run_ledger()

    assert ledger is not None
    assert [r.kind for r in ledger.refusals] == [PIIDetected, TokenBudgetExceeded]
    assert ledger.refusal_count == 2
    assert ledger.call_count == 3  # a refused call is still a call
    pii = ledger.refusals[0]
    assert pii.is_a(PIIDetected) and pii.is_a(PolicyViolation)
    assert not pii.is_a(TokenBudgetExceeded)
    assert pii.policy == "pii-detection"
    assert pii.status_code == 403
    assert pii.correlation_id == "run-1"
    assert pii.call_id is not None


async def test_a_refusal_keeps_no_response_content() -> None:
    # The gateway's PII message echoes the flagged value; the ledger must not.
    def handler(request: httpx.Request) -> httpx.Response:
        error = {"type": "pii_detected", "message": "Email jane@example.com found"}
        return httpx.Response(403, json={"error": error})

    client = DonkeyAsyncClient(_CFG, None, transport=httpx.MockTransport(handler))
    async with client, run_scope("run-1"):
        await client.post("https://proxy/chat", json={"model": "gpt-4o"})
        ledger = current_run_ledger()
    assert ledger is not None
    (refusal,) = ledger.refusals
    assert "jane@example.com" not in repr(refusal)
    assert not hasattr(refusal, "response")


async def test_a_model_substitution_is_recorded() -> None:
    from donkey_kit import ModelSubstituted

    cfg = DonkeyConfig(llm_proxy_url="https://proxy", on_model_substitution="raise")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"x-llm-proxy-llm-model": "other"}, json={})

    client = DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler))
    async with client, run_scope("run-1"):
        with pytest.raises(ModelSubstituted):
            await client.post("https://proxy/chat", json={"model": "gpt-4o"})
        ledger = current_run_ledger()
    assert ledger is not None
    assert [r.kind for r in ledger.refusals] == [ModelSubstituted]
    assert ledger.refusals[0].status_code == 200  # served, but by another model


# --- scoping ---------------------------------------------------------------------


async def test_concurrent_runs_are_isolated() -> None:
    async with _client() as client:

        async def one_run(rid: str) -> RunLedger | None:
            async with run_scope(f"run-{rid}"):
                await _call(client, rid=rid)
                await asyncio.sleep(0)
                await _call(client, rid=rid)
                return current_run_ledger()

        a, b = await asyncio.gather(one_run("a"), one_run("b"))

    assert _ids(a) == ["a", "a"]
    assert _ids(b) == ["b", "b"]


async def test_child_tasks_share_the_run_s_ledger() -> None:
    async with _client() as client, run_scope("run-1"):
        await asyncio.gather(*(_call(client, rid=f"t{i}") for i in range(5)))
        ledger = current_run_ledger()
    assert ledger is not None and ledger.call_count == 5
    assert sorted(_ids(ledger)) == [f"t{i}" for i in range(5)]  # type: ignore[type-var]


def test_threads_under_a_copied_context_share_it_safely() -> None:
    def body() -> RunLedger | None:
        with run_scope("run-1"):
            ctx = contextvars.copy_context()

            def work() -> None:
                for _ in range(200):
                    record_call(LastCall(status=LastCallStatus.OBSERVED, total_tokens=1))

            threads = [threading.Thread(target=ctx.copy().run, args=(work,)) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            return current_run_ledger()

    ledger = contextvars.copy_context().run(body)
    assert ledger is not None
    assert ledger.call_count == 1600
    assert ledger.usage.total_tokens == 1600
    assert ledger.dropped == 600  # the default cap of 1000 calls


async def test_nested_runs_get_their_own_ledger_and_restore() -> None:
    async with _client() as client, run_scope("outer"):
        await _call(client, rid="o1")
        async with run_scope("inner"):
            await _call(client, rid="i1")
            inner = current_run_ledger()
        await _call(client, rid="o2")
        outer = current_run_ledger()
    assert _ids(inner) == ["i1"]
    assert _ids(outer) == ["o1", "o2"]


async def test_rebinding_the_run_s_own_id_shares_its_ledger() -> None:
    # An approval gate resumes a tool under run_scope(<the run's id>) (#211).
    async with _client() as client, run_scope("run-1"):
        await _call(client, rid="a")
        async with run_scope("run-1"):
            await _call(client, rid="b")
        ledger = current_run_ledger()
    assert _ids(ledger) == ["a", "b"]


# --- the cap ---------------------------------------------------------------------


async def test_the_cap_keeps_the_newest_and_the_totals_stay_exact() -> None:
    async with _client() as client, run_scope("run-1", ledger_max_entries=2):
        for rid in ("a", "b", "c"):
            await _call(client, rid=rid)
        for _ in range(3):
            await _call(client, "pii")
        ledger = current_run_ledger()
    assert ledger is not None
    assert len(ledger.calls) == 2 and ledger.calls[-1].request_id is None
    assert len(ledger.refusals) == 2
    assert ledger.call_count == 6
    assert ledger.refusal_count == 3
    assert ledger.dropped == 4 + 1
    assert ledger.usage.total_tokens == 42  # all three successful calls


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10"])
def test_a_bad_cap_is_refused(bad: object) -> None:
    with pytest.raises(ConfigError, match="ledger_max_entries"):
        run_scope("run-1", ledger_max_entries=bad)  # type: ignore[arg-type]


# --- the Donkey surface ----------------------------------------------------------


def test_donkey_current_run_is_none_outside_a_run() -> None:
    assert Donkey(_CFG).current_run is None


async def test_donkey_run_yields_the_id_and_keeps_the_ledger() -> None:
    donkey = Donkey(_CFG)
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(_handler))
    scope = donkey.run(id="t-1", ledger_max_entries=5)
    async with scope as run_id:
        assert isinstance(run_id, str) and run_id == "t-1"
        await donkey._http.post("https://proxy/chat", json={"model": "gpt-4o", "input": "pii"})
        inside = donkey.current_run
    await donkey.aclose()
    assert inside is not None and inside.refusal_count == 1
    assert scope.ledger is not None and scope.ledger.call_count == 1


def test_donkey_current_run_is_unavailable_on_a_non_observing_surface() -> None:
    from donkey_kit.integrations.crewai import CrewAIAdapter

    def body() -> None:
        donkey = Donkey(_CFG)
        donkey._adapter_cache["crewai"] = CrewAIAdapter(_CFG, donkey._http)
        scope = donkey.run()
        with scope:
            ledger = donkey.current_run
            assert ledger is not None
            assert not ledger.available
            assert ledger.surface == "crewai"
        assert scope.ledger is not None and scope.ledger.surface == "crewai"

    contextvars.copy_context().run(body)
