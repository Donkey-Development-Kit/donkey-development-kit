"""A Strands agent does not retry a terminal budget refusal (#951), nor a
request-rate-limit refusal (#974).

Strands' ``OpenAIModel.stream`` wraps an openai ``RateLimitError`` in
``ModelThrottledException``, and the agent's default ``ModelRetryStrategy``
retries every ``ModelThrottledException`` (6 attempts, exponential backoff;
docs/verified-apis.md §8). On this proxy a 429 is a token-budget refusal, which
is terminal by contract (BG §1.2), so the model ``model()`` builds raises the
typed :class:`TokenBudgetExceeded` from ``stream`` instead: the strategy does
not retry it, and the event loop re-raises it as it is.

A throttle that is not a governed budget refusal (one the SDK's transport did
not send) still reaches Strands as ``ModelThrottledException`` and is retried
as before.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import RequestRateLimitExceeded, TokenBudgetExceeded

pytest.importorskip("strands.models.openai")

from strands import Agent, ModelRetryStrategy
from strands.models.openai import OpenAIModel
from strands.types.exceptions import ModelThrottledException

# A closed loopback port: any request that gets past simulate() fails to connect.
_UNREACHABLE = "http://127.0.0.1:9/"


def _donkey() -> Donkey:
    return Donkey(
        DonkeyConfig(
            llm_proxy_url=_UNREACHABLE,
            llm_proxy_client_id="proxy-client-id",
            llm_proxy_client_secret="proxy-client-secret",
            max_retries=0,
        )
    )


def _fast_retries() -> ModelRetryStrategy:
    # The default strategy's retry policy, without its 4s/8s/... backoff.
    return ModelRetryStrategy(max_attempts=3, initial_delay=0)


@pytest.fixture
def stream_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count calls into Strands' own ``OpenAIModel.stream`` (one per request)."""
    calls: list[int] = []
    original = OpenAIModel.stream

    def counted(self: OpenAIModel, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        calls.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(OpenAIModel, "stream", counted)
    return calls


async def test_a_simulated_budget_refusal_is_raised_once_typed(stream_calls: list[int]) -> None:
    donkey = _donkey()
    try:
        agent = Agent(model=donkey.strands.model("m"), callback_handler=None)
        with donkey.simulate(TokenBudgetExceeded), pytest.raises(TokenBudgetExceeded) as info:
            await agent.invoke_async("hi")
    finally:
        await donkey.aclose()
    # One request: a retry would have gone past the fixture to the closed port.
    assert len(stream_calls) == 1
    assert type(info.value) is TokenBudgetExceeded
    assert isinstance(info.value.framework_error, ModelThrottledException)


async def test_the_model_stream_raises_the_typed_refusal(stream_calls: list[int]) -> None:
    donkey = _donkey()
    try:
        model = donkey.strands.model("m")
        assert isinstance(model, OpenAIModel)  # still the native class
        with donkey.simulate(TokenBudgetExceeded), pytest.raises(TokenBudgetExceeded):
            async for _ in model.stream([{"role": "user", "content": [{"text": "hi"}]}]):
                pass
    finally:
        await donkey.aclose()
    assert len(stream_calls) == 1


async def test_a_simulated_request_rate_limit_is_raised_once_typed(
    stream_calls: list[int],
) -> None:
    donkey = _donkey()
    try:
        agent = Agent(model=donkey.strands.model("m"), callback_handler=None)
        with (
            donkey.simulate(RequestRateLimitExceeded),
            pytest.raises(RequestRateLimitExceeded) as info,
        ):
            await agent.invoke_async("hi")
    finally:
        await donkey.aclose()
    assert len(stream_calls) == 1
    assert type(info.value) is RequestRateLimitExceeded
    assert isinstance(info.value.framework_error, ModelThrottledException)


async def test_a_throttle_that_is_not_a_budget_refusal_is_still_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rate limit the transport did not send is not a governed refusal: it
    passes through as ``ModelThrottledException`` and Strands retries it."""
    calls: list[int] = []
    request = httpx.Request("POST", "https://elsewhere/chat/completions")

    async def throttled(self: OpenAIModel, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        calls.append(1)
        import openai

        error = openai.RateLimitError(
            "rate limited", response=httpx.Response(429, request=request), body=None
        )
        raise ModelThrottledException(str(error)) from error
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(OpenAIModel, "stream", throttled)
    donkey = _donkey()
    try:
        agent = Agent(
            model=donkey.strands.model("m"), callback_handler=None, retry_strategy=_fast_retries()
        )
        with pytest.raises(ModelThrottledException):
            await agent.invoke_async("hi")
    finally:
        await donkey.aclose()
    assert len(calls) == 3
