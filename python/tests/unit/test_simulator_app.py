"""In-process tests of the simulator ASGI app (BG §1.4), driven through
``httpx.ASGITransport`` — no TCP port, no uvicorn.

Guarded by ``importorskip("starlette")``: under the base-only / typecheck jobs
(``[dev]`` only, no ``[local]`` extra) this whole module skips cleanly, which is
the flip side of [[test_simulator_base_only]] — importing the package there must
still succeed, but exercising the app requires the extra.

The point being pinned: a stock client (here plain ``httpx``, standing in for
``openai`` / ``donkey.llm.client()``) gets byte-identical rejection bodies and
the exact discriminator headers, so ``core.errors.classify()`` lights up the
typed refusals against the simulator with no real gateway.
"""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")

import httpx  # noqa: E402

from donkey_kit import Budget  # noqa: E402
from donkey_kit.core.errors import (  # noqa: E402
    AuthError,
    ContentSafetyBlocked,
    PIIDetected,
    PolicyViolation,
    PromptInjectionBlocked,
    TokenBudgetExceeded,
    UpstreamModelError,
    UpstreamRequestError,
    classify,
)
from donkey_kit.simulator import build_app  # noqa: E402
from donkey_kit.simulator import fixtures as fx  # noqa: E402
from donkey_kit.simulator.app import (  # noqa: E402
    RATELIMIT_HEADER,
    SIM_MODEL_PREFIX,
    SIMULATOR_HEADER,
)


def _client() -> httpx.AsyncClient:
    """A fresh app (fresh x-token counter) behind an in-process transport."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=build_app()), base_url="http://sim"
    )


async def test_happy_path_replays_success_verbatim_and_stamps_honesty() -> None:
    async with _client() as client:
        resp = await client.post("/v1/responses", json={"model": "gpt-5.1"})
    assert resp.status_code == 200
    assert resp.content == fx.load("success").body  # byte-identical passthrough
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers[SIMULATOR_HEADER] == "true"  # honesty header, always
    assert resp.headers["x-llm-proxy-llm-provider"] == "openai"  # replayed attribution
    # x-request-id is replayed so classify() can surface it as DonkeyError.request_id.
    assert resp.headers["x-request-id"] == fx.load("success").headers["x-request-id"]
    # Gateway-identity headers from the capture are stripped, not replayed.
    assert "server" not in resp.headers
    assert "x-content-type-options" not in resp.headers


async def test_happy_path_serves_the_live_ratelimit_prose_header() -> None:
    # The live `200` (with the llm-token-rate-limit policy applied) emits the budget
    # window as a single prose header, NOT the numeric x-token-* trio (#352/#353).
    # The simulator must match that sentence byte-for-byte so it stops validating
    # the SDK against its own former assumption.
    async with _client() as client:
        resp = await client.post("/v1/responses", json={"model": "gpt-5.1"})
    assert (
        resp.headers[RATELIMIT_HEADER]
        == "Token rate limit: 99500 tokens remaining of 100000 limit. Reset in 60000ms."
    )
    # The false overlay is gone: no numeric x-token-* on a `200`, matching live.
    assert "x-token-limit" not in resp.headers
    assert "x-token-remaining" not in resp.headers
    assert "x-token-reset" not in resp.headers


async def test_happy_path_ratelimit_is_a_budget_the_object_observes_from_prose() -> None:
    # With the prose parser (#352) merged, Budget.observe() populates from the
    # simulated `200`'s x-llm-proxy-ratelimit header exactly as it would from a live
    # `200` — the whole point of #353: the simulator now exercises the real code path.
    async with _client() as client:
        resp = await client.post("/v1/responses", json={"model": "gpt-5.1"})
    budget = Budget()
    budget.observe(resp)
    assert budget.limit == 100_000
    assert budget.remaining == 99_500  # limit - one token_step
    assert budget.reset_at is not None
    assert budget.observed_at is not None
    assert budget.fraction_used == pytest.approx(0.005)


async def test_ratelimit_window_decrements_monotonically_per_request() -> None:
    async with _client() as client:
        first = await client.post("/v1/responses", json={"model": "gpt-5.1"})
        second = await client.post("/v1/responses", json={"model": "gpt-5.1"})
    assert first.headers[RATELIMIT_HEADER] == (
        "Token rate limit: 99500 tokens remaining of 100000 limit. Reset in 60000ms."
    )
    assert second.headers[RATELIMIT_HEADER] == (
        "Token rate limit: 99000 tokens remaining of 100000 limit. Reset in 60000ms."
    )


async def test_stream_replays_the_sse_capture_with_event_stream_media_type() -> None:
    async with _client() as client:
        resp = await client.post("/v1/responses", json={"model": "gpt-5.1", "stream": True})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # The captured SSE sample is a single, truncated `response.created` event —
    # replayed verbatim, NOT a complete stream ending in `data: [DONE]` (verification discipline:
    # the rest is not fabricated). Assert byte-identity, not consumability.
    assert resp.content == fx.load("stream").body
    assert resp.headers[SIMULATOR_HEADER] == "true"
    # A streaming call is still a happy path: it carries the same prose budget
    # window header as the non-stream success path.
    assert resp.headers[RATELIMIT_HEADER] == (
        "Token rate limit: 99500 tokens remaining of 100000 limit. Reset in 60000ms."
    )


async def test_get_models_is_an_honest_404() -> None:
    async with _client() as client:
        resp = await client.get("/v1/models")
    assert resp.status_code == 404
    assert resp.headers[SIMULATOR_HEADER] == "true"


async def test_unknown_route_is_an_honest_404() -> None:
    async with _client() as client:
        resp = await client.get("/nothing/here")
    assert resp.status_code == 404
    assert resp.headers[SIMULATOR_HEADER] == "true"


async def test_framework_generated_405_is_still_honesty_stamped() -> None:
    # BG §1.4: EVERY response carries x-donkey-simulator, including the 405 that
    # starlette generates for an unsupported method — which never passes through
    # _Simulator._response, so only the _HonestyStamp ASGI wrapper covers it.
    async with _client() as client:
        resp = await client.request("PUT", "/v1/responses")
    assert resp.status_code == 405
    assert resp.headers[SIMULATOR_HEADER] == "true"


# The eight documented rejection shapes plus the consumer-auth 401, each fed to
# classify() exactly as a stock client would receive it from the simulator.
# regex-prompt-guard classifies to PromptInjectionBlocked (its captured 403 carries
# matched_patterns), content-safety to ContentSafetyBlocked (its provider action
# header reads reject) — the two shapes #289 adds.
_CLASSIFY = [
    ("token-rate-limit", TokenBudgetExceeded),
    ("pii-detected", PIIDetected),
    ("injection-protection", PromptInjectionBlocked),
    ("regex-prompt-guard", PromptInjectionBlocked),
    ("content-safety", ContentSafetyBlocked),
    ("model-not-found", UpstreamRequestError),
    ("upstream-5xx", UpstreamModelError),
    ("client-id-missing", AuthError),
]


@pytest.mark.parametrize("shape,exc", _CLASSIFY)
async def test_sentinel_rejection_replays_shape_and_classifies(
    shape: str, exc: type[Exception]
) -> None:
    async with _client() as client:
        resp = await client.post("/v1/responses", json={"model": SIM_MODEL_PREFIX + shape})
    assert resp.headers[SIMULATOR_HEADER] == "true"  # honest even when rejecting
    assert resp.content == fx.load(shape).body  # byte-identical to the classify() fixture
    assert isinstance(classify(resp), exc)


async def test_content_moderation_sentinel_falls_through_to_generic_policy_violation() -> None:
    # Under-documented shape (empty 400, no discriminator): must stay a generic
    # PolicyViolation, not be misrouted to a subclass (the classify() ordering guard).
    async with _client() as client:
        resp = await client.post(
            "/v1/responses", json={"model": SIM_MODEL_PREFIX + "content-moderation"}
        )
    assert resp.headers[SIMULATOR_HEADER] == "true"
    assert type(classify(resp)) is PolicyViolation


async def test_injection_sentinel_replays_the_discriminator_header() -> None:
    # The x-injection-protection header is the discriminator classify() keys on;
    # it must survive the allow-list replay verbatim.
    async with _client() as client:
        resp = await client.post(
            "/v1/responses", json={"model": SIM_MODEL_PREFIX + "injection-protection"}
        )
    assert resp.headers["x-injection-protection"] == "blocked"


async def test_unknown_sentinel_suffix_falls_through_to_happy_path() -> None:
    async with _client() as client:
        resp = await client.post(
            "/v1/responses", json={"model": SIM_MODEL_PREFIX + "totally-made-up"}
        )
    assert resp.status_code == 200
    assert resp.content == fx.load("success").body


async def test_semantic_success_sentinel_replays_the_semantic_routing_200() -> None:
    # The happy-path sentinel forces the Semantic-routing 200 (#601): a byte-faithful
    # replay of the live 'Finance' capture, honesty-stamped, carrying routing_type
    # Semantic and the semantic-only success prose that populates matched_topic/score.
    async with _client() as client:
        resp = await client.post(
            "/v1/responses", json={"model": SIM_MODEL_PREFIX + "success-semantic"}
        )
    assert resp.status_code == 200
    assert resp.content == fx.load("success-semantic").body  # byte-identical passthrough
    assert resp.headers[SIMULATOR_HEADER] == "true"
    assert resp.headers["x-llm-proxy-routing-type"] == "Semantic"
    assert resp.headers["x-llm-proxy-semantic-routing-success"].startswith(
        "Request successfully matched 'Finance' topic"
    )
    # No synthesised budget window is layered onto a sentinel-forced shape, so the
    # replay stays exactly the captured bytes (unlike the default happy path).
    assert RATELIMIT_HEADER not in resp.headers


async def test_semantic_success_sentinel_populates_matched_topic_and_score() -> None:
    # The whole point of the shape: last_call.matched_topic / routing_score light up
    # from the simulated Semantic 200 exactly as they would from a live semantic proxy.
    from donkey_kit.core.lastcall import LastCall, LastCallStatus

    async with _client() as client:
        resp = await client.post(
            "/v1/responses", json={"model": SIM_MODEL_PREFIX + "success-semantic"}
        )
    record = LastCall.from_response(resp, requested_model="donkey-sim/success-semantic")
    assert record.status is LastCallStatus.OBSERVED
    assert record.routing_type == "Semantic"
    assert record.matched_topic == "Finance"
    assert record.routing_score == 0.62
    assert record.fallback is False
