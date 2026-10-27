"""The simulated A2A ingress behind ``donkey dev`` (BG §2.9.3, #1284).

Raw JSON-RPC over ``httpx.ASGITransport``, as in ``test_a2a_serve.py``: what a
plain A2A client sees through the ingress is compared byte for byte with the bare
``donkey.serve()`` app and with the shared rejection fixtures.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

pytest.importorskip("a2a", reason="needs the [a2a] extra")

from donkey_kit import A2ATask, Donkey, PIIDetected, PromptInjectionBlocked
from donkey_kit.a2a import build_app
from donkey_kit.core.errors import RequestRateLimitExceeded, classify
from donkey_kit.simulator.app import SIMULATOR_HEADER
from donkey_kit.simulator.fixtures import load
from donkey_kit.simulator.ingress import build_ingress, call_text
from donkey_kit.simulator.scenarios import ScenarioError, parse_scenarios

_CARD = "/.well-known/agent-card.json"


def _send(text: str, method: str = "SendMessage", msg_id: str = "m1") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {
            "message": {"messageId": msg_id, "role": "ROLE_USER", "parts": [{"text": text}]}
        },
    }


def _echo() -> Any:
    @Donkey.agent(name="echo", skills=["echo"])
    async def echo(task: A2ATask) -> str:
        """Echoes the caller's text back."""
        return f"echo: {task.input}"

    return echo


@asynccontextmanager
async def _client(*specs: str, ingress: bool = True) -> AsyncIterator[httpx.AsyncClient]:
    agent: Any = build_app(_echo())
    app: Any = build_ingress(agent, parse_scenarios(list(specs))) if ingress else agent
    async with (
        agent.router.lifespan_context(agent),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as c,
    ):
        yield c


_HEADERS = {"A2A-Version": "1.0"}


async def test_a_card_passes_through_byte_identical_and_stamped() -> None:
    async with _client() as through, _client(ingress=False) as bare:
        via, direct = await through.get(_CARD), await bare.get(_CARD)
    assert via.content == direct.content
    assert via.headers[SIMULATOR_HEADER] == "true"
    assert SIMULATOR_HEADER not in direct.headers


async def test_a_passing_call_reaches_the_agent() -> None:
    async with _client("injection:on-pattern=drop table") as c:
        response = await c.post("/", json=_send("hello"), headers=_HEADERS)
    assert response.status_code == 200
    assert response.headers[SIMULATOR_HEADER] == "true"
    task = response.json()["result"]["task"]
    assert task["artifacts"][0]["parts"] == [{"text": "echo: hello"}]


async def test_a_rejection_is_the_shared_fixture_byte_for_byte() -> None:
    fixture = load("injection-protection")
    async with _client("injection:on-pattern=drop table") as c:
        response = await c.post("/", json=_send("please DROP TABLE users"), headers=_HEADERS)
    assert response.status_code == fixture.status
    assert response.content == fixture.body
    assert response.headers[SIMULATOR_HEADER] == "true"
    assert isinstance(classify(response), PromptInjectionBlocked)


async def test_pii_block_counts_calls_only() -> None:
    async with _client("pii_block:every=2") as c:
        first = await c.post("/", json=_send("one", msg_id="a"), headers=_HEADERS)
        # A card read and a GetTask are not calls: they neither count nor get rejected.
        await c.get(_CARD)
        task_id = first.json()["result"]["task"]["id"]
        get = {"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}}
        assert (await c.post("/", json=get, headers=_HEADERS)).status_code == 200
        second = await c.post("/", json=_send("two", msg_id="b"), headers=_HEADERS)
    assert first.status_code == 200
    assert second.content == load("pii-detected").body
    assert isinstance(classify(second), PIIDetected)


async def test_request_limit_adds_the_trio_then_rejects() -> None:
    async with _client("request_limit:limit=1,window=60s") as c:
        first = await c.post("/", json=_send("one", msg_id="a"), headers=_HEADERS)
        second = await c.post("/", json=_send("two", msg_id="b"), headers=_HEADERS)
    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "1"
    assert first.headers["x-ratelimit-remaining"] == "0"
    assert second.status_code == 429
    assert second.content == load("request-rate-limit").body
    assert isinstance(classify(second), RequestRateLimitExceeded)


async def test_a_non_json_post_reaches_the_agent() -> None:
    async with _client("pii_block:every=1") as c, _client(ingress=False) as bare:
        via = await c.post("/", content=b"not json", headers=_HEADERS)
        direct = await bare.post("/", content=b"not json", headers=_HEADERS)
    assert via.content == direct.content
    assert via.headers[SIMULATOR_HEADER] == "true"


def test_budget_is_refused() -> None:
    with pytest.raises(ScenarioError, match="model tokens"):
        build_ingress(build_app(_echo()), parse_scenarios(["budget:limit=10,window=60s"]))


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (_send("hi", method="SendStreamingMessage"), "hi"),
        (
            {
                "method": "SendMessage",
                "params": {"message": {"parts": [{"text": "a"}, {"data": {}}, {"text": "b"}]}},
            },
            "a\nb",
        ),
        ({"method": "SendMessage", "params": {}}, ""),
        ({"method": "GetTask", "params": {"id": "t"}}, None),
        ([_send("batch")], None),
        (None, None),
    ],
)
def test_call_text(payload: object, expected: str | None) -> None:
    assert call_text(json.loads(json.dumps(payload))) == expected
