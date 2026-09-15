"""LastCall — the gateway's own metadata about the most recent governed call
(§3, #362). Base-only surface (Surface 1): httpx + fixtures, no framework.

Covers the issue's acceptance criteria:
  * one named record, parsed from the LIVE-VERIFIED §3 headers (request_id,
    api_instance_id, environment_id) on a 200;
  * absent/unparseable headers leave fields None, never raise (§0.3);
  * populated IDENTICALLY by the async and blocking transports;
  * contextvar concurrency scope — a fan-out does not clobber (hazard #2);
  * three honest states OBSERVED / UNOBSERVED / UNAVAILABLE (hazard #3);
  * populated under donkey.simulate() from the committed fixtures;
  * no UnverifiedValueWarning — every field traces to a verified row.
"""

from __future__ import annotations

import asyncio
import contextvars
import warnings
from datetime import datetime, timezone

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import lastcall
from donkey_kit.core._verify import UnverifiedValueWarning
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.lastcall import (
    UNOBSERVED,
    LastCall,
    LastCallStatus,
    current_last_call,
    observe_last_call,
    observe_usage,
    parse_usage,
    unavailable,
    usage_from_response,
    usage_mapping,
)
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient
from donkey_kit.simulator.fixtures import load, replay_headers

# The LIVE-VERIFIED §3 identity headers (responses.success.headers.txt, 2026-08-28).
_REQUEST_ID = "req_f85003861d5348c9a1d152c276082b07"
_DECORATOR = "api-instance-21133858.3e6ce455-e3e8-4402-b830-9fcf07d9207b.svc"
_API_INSTANCE_ID = "21133858"
_ENVIRONMENT_ID = "3e6ce455-e3e8-4402-b830-9fcf07d9207b"

_IDENTITY_HEADERS = {
    "x-request-id": _REQUEST_ID,
    "x-envoy-decorator-operation": _DECORATOR,
}
# The LIVE-VERIFIED §3 routing headers (responses.success.headers.txt, #309).
_ROUTING_HEADERS = {
    "x-llm-proxy-routing-type": "ModelBased",
    "x-llm-proxy-routing-fallback": "false",
    "x-llm-proxy-llm-provider": "openai",
    "x-llm-proxy-llm-model": "gpt-5.1",
}
_LLM_CFG = DonkeyConfig(llm_proxy_url="https://proxy")


@pytest.fixture(autouse=True)
def _reset_last_call():
    """Clear the process context's record before each test so a cold read starts
    from None regardless of a model call made by an earlier (sync) test in this
    process. Async tests get a fresh copied context from asyncio.run anyway."""
    token = lastcall._last_call.set(None)
    try:
        yield
    finally:
        lastcall._last_call.reset(token)


# --- parsing the decorator-operation header (never raises, §0.3) ------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (_DECORATOR, (_API_INSTANCE_ID, _ENVIRONMENT_ID)),
        (None, (None, None)),  # header absent
        ("", (None, None)),  # empty
        ("garbage", (None, None)),  # not the api-instance shape
        ("api-instance-21133858.svc", (None, None)),  # missing the env segment
        ("api-instance-.env.svc", (None, None)),  # empty instance is not a guess
        # Trailing/leading whitespace is tolerated, the shape still parses.
        (f"  {_DECORATOR}  ", (_API_INSTANCE_ID, _ENVIRONMENT_ID)),
    ],
)
def test_parse_decorator_operation_shapes(raw, expected) -> None:
    assert lastcall._parse_decorator_operation(raw) == expected


def test_empty_instance_segment_does_not_masquerade_as_a_value() -> None:
    # "api-instance-." with an empty instance matches the regex's [^.]+? No —
    # [^.]+ requires at least one non-dot char, so the empty-instance form above
    # actually fails to match and yields (None, None), never a fabricated "".
    assert lastcall._parse_decorator_operation("api-instance-.env.svc") == (None, None)


# --- LastCall.from_response: always OBSERVED, fields from headers ------------


def test_from_response_populates_all_identity_fields() -> None:
    now = datetime(2026, 9, 14, tzinfo=timezone.utc)
    resp = httpx.Response(200, headers=_IDENTITY_HEADERS)
    record = LastCall.from_response(resp, now=now)

    assert record.status is LastCallStatus.OBSERVED
    assert record.observed is True
    assert record.available is True
    assert record.request_id == _REQUEST_ID
    assert record.api_instance_id == _API_INSTANCE_ID
    assert record.environment_id == _ENVIRONMENT_ID
    assert record.observed_at == now


def test_from_response_with_no_identity_headers_is_observed_but_none() -> None:
    # "We saw the response, it carried no identity" is OBSERVED with None fields —
    # a true, different statement from "we never saw a response" (UNOBSERVED).
    record = LastCall.from_response(httpx.Response(200))
    assert record.status is LastCallStatus.OBSERVED
    assert record.request_id is None
    assert record.api_instance_id is None
    assert record.environment_id is None
    assert record.observed_at is not None  # timestamp still stamped


def test_from_response_never_raises_on_unparseable_decorator() -> None:
    resp = httpx.Response(200, headers={"x-envoy-decorator-operation": "not-a-shape"})
    record = LastCall.from_response(resp)
    assert (record.api_instance_id, record.environment_id) == (None, None)
    assert record.request_id is None


# --- the three honest states ------------------------------------------------


def test_unobserved_constant() -> None:
    assert UNOBSERVED.status is LastCallStatus.UNOBSERVED
    assert UNOBSERVED.observed is False
    assert UNOBSERVED.available is True  # a cold read is still an observable surface
    assert UNOBSERVED.request_id is None


def test_unavailable_names_the_surface() -> None:
    rec = unavailable("adk, crewai")
    assert rec.status is LastCallStatus.UNAVAILABLE
    assert rec.observed is False
    assert rec.available is False  # THIS is the distinguishing bit vs UNOBSERVED
    assert rec.surface == "adk, crewai"


# --- the contextvar accessor ------------------------------------------------


def test_cold_read_is_none() -> None:
    assert current_last_call() is None


def test_observe_sets_and_returns_the_record() -> None:
    resp = httpx.Response(200, headers=_IDENTITY_HEADERS)
    returned = observe_last_call(resp)
    assert returned.api_instance_id == _API_INSTANCE_ID
    assert current_last_call() is returned  # the same record is now in context


# --- both transports populate identically (AC5) -----------------------------


def _success_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers=_IDENTITY_HEADERS, json={"model": "gpt-4o"})


async def test_async_transport_populates_last_call_on_a_model_call() -> None:
    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(_success_handler))
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})

    record = current_last_call()
    assert record is not None
    assert record.status is LastCallStatus.OBSERVED
    assert record.request_id == _REQUEST_ID
    assert record.api_instance_id == _API_INSTANCE_ID
    assert record.environment_id == _ENVIRONMENT_ID


def test_sync_transport_populates_identically() -> None:
    # Run in a copied context so the record set here does not leak to later tests
    # and so the assertions read the value set within this same context.
    def body() -> LastCall:
        client = DonkeyClient(_LLM_CFG, transport=httpx.MockTransport(_success_handler))
        with client:
            client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
        rec = current_last_call()
        assert rec is not None
        return rec

    record = contextvars.copy_context().run(body)
    # Byte-for-byte the same identity fields the async twin produced.
    assert record.request_id == _REQUEST_ID
    assert record.api_instance_id == _API_INSTANCE_ID
    assert record.environment_id == _ENVIRONMENT_ID


async def test_non_model_calls_do_not_populate_the_record() -> None:
    # A GET / bodyless POST is not "the last call" a developer means — it shares
    # the transport but carries no model body, so the record stays a cold read.
    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(_success_handler))
    async with client:
        await client.get("https://proxy/models")
        await client.post("https://proxy/thing", json={"not": "a model call"})
    assert current_last_call() is None


# --- concurrency scope: fan-out does not clobber (AC7, hazard #2) -----------


async def test_fan_out_reads_each_task_s_own_call() -> None:
    """Two concurrent model calls, each served a distinct request id, must each
    read the id IT sent — not whichever landed last. The record is a contextvar,
    so an asyncio task spawned by gather runs in its own copied context."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Echo the per-request id we assigned via a header the test controls.
        rid = request.headers["x-test-rid"]
        return httpx.Response(
            200,
            headers={"x-request-id": rid, "x-envoy-decorator-operation": _DECORATOR},
            json={"model": "gpt-4o"},
        )

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))

    async def one_call(rid: str) -> str | None:
        await client.post(
            "https://proxy/chat",
            json={"model": "gpt-4o"},
            headers={"x-test-rid": rid},
        )
        # Yield so the two calls genuinely interleave before we read the record.
        await asyncio.sleep(0)
        rec = current_last_call()
        return rec.request_id if rec else None

    async with client:
        a, b = await asyncio.gather(one_call("rid-a"), one_call("rid-b"))

    assert a == "rid-a"  # each task saw its own call, not the sibling's
    assert b == "rid-b"


# --- Donkey.last_call derived states (AC1, AC8) -----------------------------


def _donkey() -> Donkey:
    return Donkey(_LLM_CFG)


def test_donkey_last_call_cold_read_is_unobserved() -> None:
    def body() -> None:
        donkey = _donkey()
        record = donkey.last_call
        assert record.status is LastCallStatus.UNOBSERVED
        assert record.available is True

    contextvars.copy_context().run(body)


def test_donkey_last_call_passes_through_an_observed_record() -> None:
    def body() -> None:
        donkey = _donkey()
        observe_last_call(httpx.Response(200, headers=_IDENTITY_HEADERS))
        record = donkey.last_call
        assert record.status is LastCallStatus.OBSERVED
        assert record.request_id == _REQUEST_ID

    contextvars.copy_context().run(body)


def test_donkey_last_call_is_unavailable_when_every_adapter_is_non_observing() -> None:
    # Reading last_call on a Donkey whose only resolved adapters route outside our
    # transport reports UNAVAILABLE (naming the surface), never a bare None/cold
    # read. We seed the adapter cache directly to avoid installing frameworks —
    # the adapter classes import their framework lazily, so this is base-safe.
    from donkey_kit.integrations.adk import ADKAdapter
    from donkey_kit.integrations.crewai import CrewAIAdapter

    def body() -> None:
        donkey = _donkey()
        donkey._adapter_cache["adk"] = ADKAdapter(_LLM_CFG, donkey._http)
        donkey._adapter_cache["crewai"] = CrewAIAdapter(_LLM_CFG, donkey._http)
        record = donkey.last_call
        assert record.status is LastCallStatus.UNAVAILABLE
        assert record.available is False
        assert record.surface == "adk, crewai"  # sorted, comma-joined

    contextvars.copy_context().run(body)


def test_donkey_last_call_stays_unobserved_when_an_observing_adapter_is_present() -> None:
    # A mix that includes an observing adapter is NOT structurally unavailable —
    # a call through it could still populate the record, so a cold read is honest.
    from donkey_kit.integrations.adk import ADKAdapter
    from donkey_kit.integrations.langgraph import LangGraphAdapter

    def body() -> None:
        donkey = _donkey()
        donkey._adapter_cache["adk"] = ADKAdapter(_LLM_CFG, donkey._http)
        donkey._adapter_cache["langgraph"] = LangGraphAdapter(_LLM_CFG, donkey._http)
        assert donkey.last_call.status is LastCallStatus.UNOBSERVED

    contextvars.copy_context().run(body)


# --- populated under donkey.simulate() from the committed fixtures (AC6) ----


async def test_simulate_populates_last_call_from_the_committed_fixture() -> None:
    from donkey_kit.core.errors import PIIDetected

    donkey = _donkey()
    # The pii-detected capture carries x-envoy-decorator-operation (the same
    # verified value), so the injected refusal's record parses the gateway
    # instance/environment ids — the success path's identity, on a refusal.
    with donkey.simulate(PIIDetected):
        resp = await donkey._http.post(
            "https://proxy/chat", json={"model": "gpt-4o", "input": "e@x.io"}
        )
    assert resp.status_code == 403

    record = donkey.last_call
    assert record.status is LastCallStatus.OBSERVED
    assert record.api_instance_id == _API_INSTANCE_ID
    assert record.environment_id == _ENVIRONMENT_ID
    await donkey.aclose()


def test_committed_success_fixture_feeds_the_record() -> None:
    # The `donkey mock` / simulate replay path keeps both identity headers in its
    # allow-list, so the committed success fixture populates the record end to end.
    fixture = load("success")
    replayed = replay_headers(fixture)
    assert replayed["x-request-id"] == _REQUEST_ID
    assert replayed["x-envoy-decorator-operation"] == _DECORATOR

    record = LastCall.from_response(httpx.Response(200, headers=replayed))
    assert record.request_id == _REQUEST_ID
    assert record.api_instance_id == _API_INSTANCE_ID
    assert record.environment_id == _ENVIRONMENT_ID


# --- per-call usage token counts (#307) -------------------------------------

# The Responses API usage shape, verbatim from responses.success.body.json — the
# detail fields are present-but-zero here (0, NOT absent), which is the contrast
# the None-not-zero tests below rely on.
_RESPONSES_USAGE = {
    "input_tokens": 17,
    "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
    "output_tokens": 51,
    "output_tokens_details": {"reasoning_tokens": 0},
    "total_tokens": 68,
}


def test_parse_usage_responses_api_shape() -> None:
    assert parse_usage(_RESPONSES_USAGE) == {
        "input_tokens": 17,
        "output_tokens": 51,
        "total_tokens": 68,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    }


def test_parse_usage_chat_completions_shape() -> None:
    # prompt_tokens/completion_tokens + *_tokens_details is the OpenAI Chat shape;
    # cache_write has no Chat equivalent, so it stays None.
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 40},
        "completion_tokens_details": {"reasoning_tokens": 8},
    }
    assert parse_usage(usage) == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cached_tokens": 40,
        "cache_write_tokens": None,
        "reasoning_tokens": 8,
    }


def test_parse_usage_absent_detail_fields_are_none_not_zero() -> None:
    # Only the top-level counts present: the detail fields are ABSENT, so None —
    # distinct from the fixture's present-but-zero 0 (AC2).
    assert parse_usage({"input_tokens": 5, "output_tokens": 3}) == {
        "input_tokens": 5,
        "output_tokens": 3,
        "total_tokens": None,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
    }


@pytest.mark.parametrize(
    "bad",
    [None, {}, "usage", 17, {"input_tokens": "x"}, {"input_tokens": True}],
)
def test_parse_usage_non_dict_or_garbage_is_all_none(bad) -> None:
    # Never raises, never a fabricated 0; a bool is not accepted as an int (§0.3).
    assert set(parse_usage(bad).values()) == {None}


def test_usage_mapping_reads_both_shapes_and_rejects_others() -> None:
    assert usage_mapping({"usage": {"prompt_tokens": 1}}) == {"prompt_tokens": 1}
    # The Responses API terminal event nests usage under `response`.
    assert usage_mapping({"response": {"usage": {"input_tokens": 2}}}) == {"input_tokens": 2}
    assert usage_mapping({"type": "response.output_text.delta"}) is None
    assert usage_mapping("not-a-dict") is None


def _json_response(status: int, usage: object) -> httpx.Response:
    body: dict[str, object] = {"model": "gpt-5.1"}
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(status, json=body, headers={"content-type": "application/json"})


def test_usage_from_response_reads_the_body() -> None:
    parsed = usage_from_response(_json_response(200, _RESPONSES_USAGE))
    assert parsed["input_tokens"] == 17
    assert parsed["reasoning_tokens"] == 0


def test_usage_from_response_no_usage_object_is_all_none() -> None:
    assert set(usage_from_response(_json_response(200, None)).values()) == {None}


def test_usage_from_response_non_2xx_is_all_none() -> None:
    # We never parse a refusal body for usage even if it carried a usage-like key.
    assert set(usage_from_response(_json_response(403, _RESPONSES_USAGE)).values()) == {None}


def test_usage_from_response_unread_or_empty_body_never_raises() -> None:
    # An SSE / bodyless response's .json() raises; it is caught → all None (§0.3).
    resp = httpx.Response(200, headers={"content-type": "text/event-stream"})
    assert set(usage_from_response(resp).values()) == {None}


def test_from_response_populates_usage_from_the_committed_fixture() -> None:
    # AC1: the three detail fields (and the totals) parse from the captured LIVE
    # fixture, with the fixture bytes themselves as the test input.
    fixture = load("success")
    resp = httpx.Response(
        200, content=fixture.body, headers={"content-type": "application/json"}
    )
    record = LastCall.from_response(resp)
    assert record.status is LastCallStatus.OBSERVED
    assert record.input_tokens == 17
    assert record.output_tokens == 51
    assert record.total_tokens == 68
    assert record.cached_tokens == 0
    assert record.cache_write_tokens == 0
    assert record.reasoning_tokens == 0


def test_from_response_identity_only_leaves_usage_none() -> None:
    # A headers-only response is OBSERVED with identity but no usage — usage None,
    # never 0, exactly like an unobserved budget window.
    record = LastCall.from_response(httpx.Response(200, headers=_IDENTITY_HEADERS))
    assert record.request_id == _REQUEST_ID
    assert record.input_tokens is None
    assert record.total_tokens is None
    assert record.reasoning_tokens is None


def test_observe_usage_merges_into_the_observed_record() -> None:
    def body() -> None:
        # A stream first sets an OBSERVED record with identity but no usage...
        observe_last_call(httpx.Response(200, headers=_IDENTITY_HEADERS))
        before = current_last_call()
        assert before is not None and before.input_tokens is None
        # ...then the terminal SSE event's counts merge in without touching identity.
        merged = observe_usage(
            parse_usage(
                {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                }
            )
        )
        assert merged is not None
        rec = current_last_call()
        assert rec is not None
        assert rec.input_tokens == 11
        assert rec.output_tokens == 3
        assert rec.reasoning_tokens == 2
        assert rec.request_id == _REQUEST_ID  # identity preserved through the merge

    contextvars.copy_context().run(body)


def test_observe_usage_is_a_noop_without_an_observed_record() -> None:
    def body() -> None:
        # A cold context has no record to merge into: no-op, returns None.
        assert observe_usage(parse_usage({"input_tokens": 5})) is None
        assert current_last_call() is None

    contextvars.copy_context().run(body)


def _usage_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, headers=_IDENTITY_HEADERS, json={"model": "gpt-4o", "usage": _RESPONSES_USAGE}
    )


async def test_async_transport_populates_usage_on_a_model_call() -> None:
    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(_usage_handler))
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
    rec = current_last_call()
    assert rec is not None
    assert rec.input_tokens == 17
    assert rec.reasoning_tokens == 0
    assert rec.request_id == _REQUEST_ID  # identity and usage on one record


def test_sync_transport_populates_usage_identically() -> None:
    def body() -> LastCall:
        client = DonkeyClient(_LLM_CFG, transport=httpx.MockTransport(_usage_handler))
        with client:
            client.post("https://proxy/chat", json={"model": "gpt-4o", "input": "hi"})
        rec = current_last_call()
        assert rec is not None
        return rec

    record = contextvars.copy_context().run(body)
    assert record.input_tokens == 17
    assert record.total_tokens == 68
    assert record.cached_tokens == 0


# --- no UnverifiedValueWarning: every field traces to a verified row (AC10) --


def test_no_unverified_warning_on_the_read_path() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        LastCall.from_response(httpx.Response(200, headers=_IDENTITY_HEADERS))
        observe_last_call(httpx.Response(200, headers=_IDENTITY_HEADERS))
        _ = current_last_call()
        # The usage path traces to the same verified §3 row — no warning either.
        parse_usage(_RESPONSES_USAGE)
        usage_from_response(_json_response(200, _RESPONSES_USAGE))


# --- gateway routing & fallback (§3, #309) ----------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("false", False),
        ("true", True),
        ("False", False),  # case-insensitive
        ("TRUE", True),
        ("  true  ", True),  # whitespace tolerated
        (None, None),  # header absent — a non-proxy / simulated response
        ("", None),  # empty is not a guess
        ("maybe", None),  # unrecognised is unknown, never a default False (§0.3)
    ],
)
def test_parse_fallback_shapes(raw, expected) -> None:
    assert lastcall._parse_fallback(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("true", True), ("false", False), (None, False), ("garbage", False)],
)
def test_is_fallback_is_definitive_true_only(raw, expected) -> None:
    # The retry predicate gates on this: only a definitive "true" counts as a
    # fallback, so an absent/unknown header still retries as before.
    headers = {"x-llm-proxy-routing-fallback": raw} if raw is not None else {}
    assert lastcall.is_fallback(httpx.Response(503, headers=headers)) is expected


def test_routing_fallback_is_tri_state() -> None:
    assert lastcall.routing_fallback(httpx.Response(200, headers=_ROUTING_HEADERS)) is False
    assert lastcall.routing_fallback(httpx.Response(200)) is None


def test_from_response_parses_all_five_routing_fields_from_the_live_fixture() -> None:
    # AC1: all five routing signals surfaced — the served provider/model, the
    # routing strategy, the fallback flag, and the requested model the caller sent.
    fixture = load("success")
    replayed = replay_headers(fixture)
    record = LastCall.from_response(
        httpx.Response(200, headers=replayed), requested_model="gpt-5.1"
    )

    assert record.requested_model == "gpt-5.1"
    assert record.served_provider == "openai"
    assert record.served_model == "gpt-5.1"
    assert record.routing_type == "ModelBased"
    assert record.fallback is False
    assert record.substituted is False


def test_missing_routing_headers_report_none_and_do_not_crash() -> None:
    # AC2: a non-proxy or simulated response with none of the routing headers must
    # leave every field None/None and never raise (§0.3).
    record = LastCall.from_response(httpx.Response(200))
    assert record.served_provider is None
    assert record.served_model is None
    assert record.routing_type is None
    assert record.fallback is None  # tri-state None, NOT a definitive False
    assert record.requested_model is None
    assert record.substituted is False  # can't claim a substitution with no data


def test_substituted_is_true_only_when_both_known_and_differ() -> None:
    def rec(**kw) -> LastCall:
        return LastCall(status=LastCallStatus.OBSERVED, **kw)

    assert rec(requested_model="gpt-5.1", served_model="gpt-4o").substituted is True
    assert rec(requested_model="gpt-5.1", served_model="gpt-5.1").substituted is False
    assert rec(requested_model="gpt-5.1", served_model=None).substituted is False
    assert rec(requested_model=None, served_model="gpt-4o").substituted is False


async def test_transport_threads_requested_model_onto_the_record() -> None:
    # The requested model comes from the request body; the served model from the
    # gateway header. A substitution is only knowable when both are on the record.
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {**_ROUTING_HEADERS, "x-llm-proxy-llm-model": "gpt-4o"}  # served ≠ requested
        return httpx.Response(200, headers=headers, json={"model": "gpt-4o"})

    client = DonkeyAsyncClient(_LLM_CFG, None, transport=httpx.MockTransport(handler))
    async with client:
        await client.post("https://proxy/chat", json={"model": "gpt-5.1", "input": "hi"})

    record = current_last_call()
    assert record is not None
    assert record.requested_model == "gpt-5.1"
    assert record.served_model == "gpt-4o"
    assert record.substituted is True


def test_no_unverified_warning_on_the_routing_read_path() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", UnverifiedValueWarning)
        LastCall.from_response(
            httpx.Response(200, headers={**_IDENTITY_HEADERS, **_ROUTING_HEADERS}),
            requested_model="gpt-5.1",
        )
