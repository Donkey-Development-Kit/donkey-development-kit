"""LlamaIndex model-name defaults (#829).

LlamaIndex keys its reasoning-model handling and its context-window table on the
exact bare OpenAI name, so the ``<provider>/<model>`` names a model-based proxy
routes on (``openai/gpt-5-mini``) used to miss both: the wire carried
``temperature=0.1`` and ``max_tokens`` and dropped ``reasoning_effort``, and every
model got ``OpenAILike``'s 3,900-token window. ``llm()`` now resolves the bare
name after one provider prefix. These tests assert on the request body that
reaches the transport, not on LlamaIndex internals.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import DonkeyAsyncClient

pytest.importorskip("llama_index.llms.openai_like")

from llama_index.core.base.llms.types import ChatMessage

from donkey_kit.integrations.llamaindex import LlamaIndexAdapter


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 0,
    "model": "gpt-5-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "PONG"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


async def _wire_body(model: str, **kw: Any) -> dict[str, Any]:
    """The JSON body ``achat()`` sends for ``llm(model, **kw)``."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_COMPLETION)

    cfg = _cfg()
    http = DonkeyAsyncClient(cfg, None, transport=httpx.MockTransport(handler))
    async with http:
        llm = LlamaIndexAdapter(cfg, http).llm(model, **kw)
        await llm.achat([ChatMessage(role="user", content="ping")])
    (request,) = seen
    body: dict[str, Any] = json.loads(request.content)
    return body


def _llm(model: str, **kw: Any) -> Any:
    cfg = _cfg()
    return LlamaIndexAdapter(cfg, DonkeyAsyncClient(cfg, None)).llm(model, **kw)


@pytest.mark.parametrize("model", ["openai/gpt-5-mini", "azureopenai/gpt-5-mini"])
async def test_prefixed_reasoning_model_gets_reasoning_wire_shape(model: str) -> None:
    body = await _wire_body(model, max_tokens=50, reasoning_effort="low")
    assert body["model"] == model  # the proxy still routes on the prefixed name
    assert body["temperature"] == 1.0  # not LlamaIndex's 0.1, which gpt-5 rejects
    assert body["max_completion_tokens"] == 50
    assert "max_tokens" not in body
    assert body["reasoning_effort"] == "low"


async def test_bare_reasoning_model_wire_shape_is_unchanged() -> None:
    """LlamaIndex already handles the bare name; the adapter must not double up."""
    body = await _wire_body("gpt-5-mini", max_tokens=50, reasoning_effort="low")
    assert body["temperature"] == 1.0
    assert body["max_completion_tokens"] == 50
    assert "max_tokens" not in body
    assert body["reasoning_effort"] == "low"


async def test_prefixed_non_reasoning_model_keeps_chat_defaults() -> None:
    body = await _wire_body("openai/gpt-4o-mini", max_tokens=50)
    assert body["temperature"] == 0.1
    assert body["max_tokens"] == 50
    assert "max_completion_tokens" not in body


async def test_caller_kwargs_win_on_a_prefixed_reasoning_model() -> None:
    body = await _wire_body(
        "openai/gpt-5-mini",
        temperature=0.5,
        max_tokens=50,
        reasoning_effort="low",
        additional_kwargs={"max_completion_tokens": 80, "reasoning_effort": "high", "seed": 7},
    )
    assert body["temperature"] == 0.5
    assert body["max_completion_tokens"] == 80
    assert "max_tokens" not in body
    assert body["reasoning_effort"] == "high"
    assert body["seed"] == 7


async def test_reasoning_effort_unset_is_not_sent() -> None:
    body = await _wire_body("openai/gpt-5-mini")
    assert "reasoning_effort" not in body
    assert "max_completion_tokens" not in body


@pytest.mark.parametrize(
    ("model", "window"),
    [
        ("gpt-5-mini", 400_000),
        ("openai/gpt-5-mini", 400_000),
        ("azureopenai/gpt-4o-mini", 128_000),
    ],
)
def test_context_window_follows_the_model(model: str, window: int) -> None:
    assert _llm(model).context_window == window


def test_unknown_model_keeps_the_llamaindex_default_window() -> None:
    from llama_index.core.constants import DEFAULT_CONTEXT_WINDOW

    assert _llm("gemini-3-flash-preview").context_window == DEFAULT_CONTEXT_WINDOW
    assert _llm("bedrock/anthropic.claude-x").context_window == DEFAULT_CONTEXT_WINDOW


def test_caller_context_window_wins() -> None:
    assert _llm("openai/gpt-5-mini", context_window=32_000).context_window == 32_000


def test_default_memory_is_sized_to_the_model() -> None:
    """The issue's repro: memory was capped at 2,925 tokens for every model."""
    from llama_index.core.memory import ChatMemoryBuffer

    memory = ChatMemoryBuffer.from_defaults(llm=_llm("gpt-5-mini"))
    assert memory.token_limit == int(400_000 * 0.75)
