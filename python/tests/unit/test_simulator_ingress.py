"""The simulated A2A ingress behind ``donkey dev`` (BG §2.9.3, #1284).

Raw JSON-RPC over ``httpx.ASGITransport``, as in ``test_a2a_serve.py``: what a
plain A2A client sees through the ingress is compared byte for byte with the bare
``donkey.serve()`` app and with the shared rejection fixtures.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest

from donkey_kit import A2ATask, ContentSafetyBlocked, Donkey, TokenBudgetExceeded
from donkey_kit.a2a import build_app
from donkey_kit.a2a.protocol import matches_protocol_version
from donkey_kit.core.errors import classify
from donkey_kit.experimental import ToolInvocationError
from donkey_kit.simulator import ingress
from donkey_kit.simulator.app import SIMULATOR_HEADER
from donkey_kit.simulator.fixtures import Fixture, parse_headers, parse_status, replay_headers
from donkey_kit.simulator.ingress import build_ingress, call_text, check_scenarios
from donkey_kit.simulator.scenarios import ScenarioError, parse_scenarios

_CARD = "/.well-known/agent-card.json"
_CAPTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"


def _captured(name: str) -> Fixture:
    raw = (_CAPTURES / f"{name}.headers.txt").read_text()
    status = parse_status(raw)
    assert status is not None
    headers = parse_headers(raw)
    body = _CAPTURES / f"{name}.body.json"
    return Fixture(
        name,
        status,
        headers,
        body.read_bytes() if body.exists() else b"",
        headers.get("content-type"),
    )


def _assert_replay(response: httpx.Response, fixture: Fixture) -> None:
    assert response.status_code == fixture.status
    assert response.content == fixture.body
    expected = {
        **replay_headers(fixture),
        "content-length": str(len(fixture.body)),
        SIMULATOR_HEADER: "true",
    }
    if fixture.content_type is not None:
        expected["content-type"] = fixture.content_type
    assert dict(response.headers) == expected
    assert {
        "authorization",
        "proxy-authorization",
        "client_id",
        "client_secret",
        "x-client-id",
        "api-key",
        "x-api-key",
        "server",
        "date",
        "connection",
        "transfer-encoding",
        "strict-transport-security",
        "set-cookie",
    }.isdisjoint(response.headers)


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
async def _client(
    *specs: str, ingress: bool = True, protocol_version: str = "1.0"
) -> AsyncIterator[httpx.AsyncClient]:
    pytest.importorskip("a2a", reason="the served-agent tests need the [a2a] extra")
    agent: Any = build_app(_echo(), protocol_version=protocol_version)
    app: Any = (
        build_ingress(agent, parse_scenarios(list(specs)), protocol_version=protocol_version)
        if ingress
        else agent
    )
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


@pytest.mark.parametrize(
    "method,suffix", [("SendMessage", "send"), ("SendStreamingMessage", "stream")]
)
async def test_injection_replays_the_matching_azure_capture(method: str, suffix: str) -> None:
    """BG section 2.9.3 / #1393: prompt shield is a captured 403, not an LLM 400."""
    fixture = _captured(f"v1-azure-block-{suffix}")
    async with _client("injection:on-pattern=drop table") as c:
        response = await c.post(
            "/", json=_send("please DROP TABLE users", method=method), headers=_HEADERS
        )
    _assert_replay(response, fixture)
    assert response.headers["x-flex-azure-guardrails-action"] == "reject"
    assert response.headers["x-flex-azure-guardrails-phase"] == "request"
    assert response.headers["x-flex-azure-guardrails-reason"] == "prompt_shield"
    # Captured evidence, not an invented ID-rewrite contract.
    assert response.json()["id"] == "1393"
    assert type(classify(response)) is ContentSafetyBlocked


@pytest.mark.parametrize(
    "method,suffix", [("SendMessage", "send"), ("SendStreamingMessage", "stream")]
)
async def test_pii_block_counts_calls_only(method: str, suffix: str) -> None:
    async with _client("pii_block:every=2") as c:
        first = await c.post("/", json=_send("one", msg_id="a"), headers=_HEADERS)
        # A card read and a GetTask are not calls: they neither count nor get rejected.
        await c.get(_CARD)
        task_id = first.json()["result"]["task"]["id"]
        get = {"jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task_id}}
        assert (await c.post("/", json=get, headers=_HEADERS)).status_code == 200
        second = await c.post("/", json=_send("two", method=method, msg_id="b"), headers=_HEADERS)
    assert first.status_code == 200
    _assert_replay(second, _captured(f"v1-pii-{suffix}"))
    assert type(classify(second)) is ToolInvocationError


@pytest.mark.parametrize(
    "method,suffix", [("SendMessage", "send"), ("SendStreamingMessage", "stream")]
)
async def test_request_limit_triggers_captured_rate_shape_without_accepted_headers(
    method: str, suffix: str
) -> None:
    async with _client("request_limit:limit=1,window=60s") as c:
        first = await c.post("/", json=_send("one", msg_id="a"), headers=_HEADERS)
        second = await c.post("/", json=_send("two", method=method, msg_id="b"), headers=_HEADERS)
    assert first.status_code == 200
    assert not any(key.startswith(("x-ratelimit-", "x-token-")) for key in first.headers)
    _assert_replay(second, _captured(f"v1-rate-{suffix}"))
    assert second.headers["x-token-remaining"] == "1"
    assert second.headers["x-token-reset"] == "60000"
    assert "retry-after" not in second.headers
    assert type(classify(second)) is TokenBudgetExceeded


async def test_request_limit_rollover_and_rejection_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request window triggers replay only; rejected calls do not spend it."""
    now = 0.0
    monkeypatch.setattr("donkey_kit.simulator.scenarios.time.monotonic", lambda: now)
    async with _client(
        "request_limit:limit=1,window=60s", "pii_block:every=2", "injection:on-pattern=block"
    ) as c:
        injection = await c.post("/", json=_send("BLOCK"), headers=_HEADERS)
        first = await c.post("/", json=_send("hello"), headers=_HEADERS)
        pii = await c.post("/", json=_send("hello"), headers=_HEADERS)
        rate = await c.post("/", json=_send("hello"), headers=_HEADERS)
        now = 60.0
        await c.post("/", json=_send("hello"), headers=_HEADERS)  # PII takes precedence.
        reset = await c.post("/", json=_send("hello"), headers=_HEADERS)
    _assert_replay(injection, _captured("v1-azure-block-send"))
    assert first.status_code == reset.status_code == 200
    _assert_replay(pii, _captured("v1-pii-send"))
    _assert_replay(rate, _captured("v1-rate-send"))


async def test_passing_stream_is_forwarded_without_reframing() -> None:
    """An arbitrary ASGI stream remains byte-identical, with only the honesty stamp."""
    received = []

    async def agent(scope: Any, receive: Any, send: Any) -> None:
        received.append(await receive())
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream"), (b"x-agent", b"yes")],
            }
        )
        await send({"type": "http.response.body", "body": b"data: one\n\n", "more_body": True})
        await send({"type": "http.response.body", "body": b"data: two\n\n"})

    app = build_ingress(agent, parse_scenarios(["request_limit:limit=1,window=60s"]))
    body = json.dumps(_send("hello", method="SendStreamingMessage")).encode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://agent"
    ) as c:
        response = await c.post("/", content=body, headers=_HEADERS)
    assert received == [{"type": "http.request", "body": body, "more_body": False}]
    assert response.content == b"data: one\n\ndata: two\n\n"
    assert dict(response.headers) == {
        "content-type": "text/event-stream",
        "x-agent": "yes",
        SIMULATOR_HEADER: "true",
    }


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "scenario,policy,status",
    [
        ("injection:on-pattern=block", "azure-block", 403),
        ("pii_block:every=1", "pii", 403),
        ("request_limit:limit=1,window=60s", "rate", 429),
    ],
)
async def test_official_sdk_generated_id_receives_http_refusal_without_id_rewrite(
    stream: bool, scenario: str, policy: str, status: int
) -> None:
    """Stock v1 SDK raises at HTTP status, before RPC correlation or SSE decoding."""
    sdk = pytest.importorskip("a2a.types")
    from a2a.client.errors import A2AClientError
    from a2a.client.transports.jsonrpc import JsonRpcTransport
    from google.protobuf import json_format

    async def agent(scope: Any, receive: Any, send: Any) -> None:
        pytest.fail("A refusal must not reach the agent")

    scenarios = parse_scenarios([scenario])
    if policy == "rate":
        assert scenarios[0].on_call("") is None  # Spend the permitted request before the SDK call.
    app = build_ingress(agent, scenarios)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://agent", headers=_HEADERS
    ) as client:
        transport = JsonRpcTransport(client, sdk.AgentCard(), "http://agent/")
        request = json_format.ParseDict(_send("block")["params"], sdk.SendMessageRequest())
        with pytest.raises(A2AClientError, match=f"HTTP Error {status}") as caught:
            if stream:
                events = [event async for event in transport.send_message_streaming(request)]
                pytest.fail(f"Expected an HTTP refusal, got {events}")
            else:
                await transport.send_message(request)
    cause = caught.value.__cause__
    assert type(caught.value) is A2AClientError
    assert isinstance(cause, httpx.HTTPStatusError)
    response = cause.response
    payload = json.loads(cause.request.content)
    assert str(UUID(payload["id"])) == payload["id"]
    assert payload["method"] == ("SendStreamingMessage" if stream else "SendMessage")
    _assert_replay(response, _captured(f"v1-{policy}-{'stream' if stream else 'send'}"))
    if policy != "rate":
        assert response.json()["id"] == "1393" != payload["id"]


async def test_a_non_json_post_reaches_the_agent() -> None:
    async with _client("pii_block:every=1") as c, _client(ingress=False) as bare:
        via = await c.post("/", content=b"not json", headers=_HEADERS)
        direct = await bare.post("/", content=b"not json", headers=_HEADERS)
    assert via.content == direct.content
    assert via.headers[SIMULATOR_HEADER] == "true"


async def test_a_declared_body_over_the_cap_is_a_413_before_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingress, "MAX_BODY_BYTES", 64)
    async with _client() as c:
        response = await c.post("/", content=b"x" * 65, headers=_HEADERS)
    assert response.status_code == 413
    assert response.headers[SIMULATOR_HEADER] == "true"
    assert b"64 bytes" in response.content


async def test_a_body_at_the_cap_reaches_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(_send("hi")).encode()
    monkeypatch.setattr(ingress, "MAX_BODY_BYTES", len(body))
    async with _client() as c:
        response = await c.post("/", content=body, headers=_HEADERS)
    assert response.status_code == 200
    assert response.json()["result"]["task"]["artifacts"][0]["parts"] == [{"text": "echo: hi"}]


async def test_a_streamed_body_stops_being_read_at_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``content-length``: the ingress counts the chunks and stops at the cap."""
    monkeypatch.setattr(ingress, "MAX_BODY_BYTES", 10)
    reached: list[object] = []

    async def agent(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope)

    chunks = [b"x" * 6, b"x" * 6, b"never read"]
    read = 0

    async def receive() -> dict[str, Any]:
        nonlocal read
        read += 1
        return {"type": "http.request", "body": chunks[read - 1], "more_body": True}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    await build_ingress(agent)(scope, receive, send)
    assert reached == []
    assert read == 2
    assert sent[0]["status"] == 413


def test_budget_is_refused() -> None:
    with pytest.raises(ScenarioError, match="model tokens"):
        check_scenarios(parse_scenarios(["budget:limit=10,window=60s"]))


def test_ciba_has_no_matching_v1_ingress_capture() -> None:
    with pytest.raises(ScenarioError, match="captured A2A v1"):
        check_scenarios(parse_scenarios(["ciba:pending=1"]))


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


def _send_v03(text: str, method: str = "message/send") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 7,
        "method": method,
        "params": {
            "message": {
                "messageId": "m03",
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }


@pytest.mark.parametrize("protocol", ["2.0", "0.3", "", "1.0.0"])
def test_invalid_protocol_is_rejected_without_optional_dependencies(protocol: str) -> None:
    with pytest.raises(ScenarioError, match="unsupported A2A protocol version"):
        build_ingress(None, protocol_version=protocol)
    with pytest.raises(ScenarioError, match="unsupported A2A protocol version"):
        call_text({}, protocol_version=protocol)


def test_public_call_helpers_keep_v1_defaults_and_allow_explicit_legacy() -> None:
    assert ingress.CALL_METHODS == frozenset({"SendMessage", "SendStreamingMessage"})
    assert call_text(_send_v03("legacy")) is None
    assert call_text(_send_v03("legacy"), protocol_version="0.3.0") == "legacy"
    assert call_text(_send("v1"), protocol_version="0.3.0") is None
    check_scenarios(parse_scenarios(["injection:on-pattern=block"]))
    with pytest.raises(ScenarioError, match="no matching captured A2A v03"):
        check_scenarios(parse_scenarios(["injection:on-pattern=block"]), protocol_version="0.3.0")


async def test_default_v1_replay_keeps_headerless_compatibility() -> None:
    """#1601 preserves the ingress's existing default, even for a minimal caller."""
    async with _client("pii_block:every=1") as c:
        response = await c.post("/", json=_send("hello"))
    _assert_replay(response, _captured("v1-pii-send"))


@pytest.mark.parametrize("protocol,header", [("1.0", "1.0.0"), ("0.3.0", "0.3.1")])
async def test_compatible_patch_header_passes_then_replays(protocol: str, header: str) -> None:
    payload = _send("hello") if protocol == "1.0" else _send_v03("hello")
    capture = "v1-pii-send" if protocol == "1.0" else "v03-pii-send"
    async with _client("pii_block:every=2", protocol_version=protocol) as c:
        first = await c.post("/", json=payload, headers={"A2A-Version": header})
        second = await c.post("/", json=payload, headers={"A2A-Version": header})
    assert first.status_code == 200
    assert "error" not in first.json()
    _assert_replay(second, _captured(capture))


@pytest.mark.parametrize("protocol", ["1.0", "0.3.0"])
@pytest.mark.parametrize(
    "header",
    [
        "1",
        "1.0.0",
        "v1.0",
        "01.9",
        "1.0rc1",
        "1.0.post2",
        "1.0-dev3",
        "1.0+local",
        "2!1.0",
        "0.3",
        "0.3.1",
        "0.9",
        "",
        "bogus",
        "1..0",
        "1.0.0garbage",
        "1.0+",
        "1.0,0.3",
    ],
)
def test_header_matching_agrees_with_sdk_version_normalization(protocol: str, header: str) -> None:
    from packaging.version import InvalidVersion, Version

    try:
        expected = Version(header).major == Version(protocol).major
    except InvalidVersion:
        expected = False
    assert matches_protocol_version(header, protocol) is expected


@pytest.mark.parametrize(
    "scenario,capture",
    [("pii_block:every=2", "v03-pii-send"), ("request_limit:limit=1,window=60s", "v03-rate-send")],
)
async def test_legacy_send_replays_actual_capture_after_passing_call(
    scenario: str, capture: str
) -> None:
    """BG section 2.9.3 / #1393: replay v0.3 bytes, never a converted v1 envelope."""
    async with _client(scenario, protocol_version="0.3.0") as c:
        first = await c.post("/", json=_send_v03("one"))
        card = await c.get(_CARD)
        get = await c.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tasks/get",
                "params": {"id": first.json()["result"]["id"]},
            },
        )
        second = await c.post("/", json=_send_v03("two"))
    assert first.status_code == get.status_code == card.status_code == 200
    assert first.json()["result"]["artifacts"][0]["parts"] == [
        {"kind": "text", "text": "echo: one"}
    ]
    assert all(r.headers[SIMULATOR_HEADER] == "true" for r in (first, get, card))
    assert not any(key.startswith(("x-ratelimit-", "x-token-")) for key in first.headers)
    _assert_replay(second, _captured(capture))
    if capture == "v03-pii-send":
        assert second.json()["id"] == 1393
        assert isinstance(second.json()["error"]["data"], str)


@pytest.mark.parametrize(
    "protocol,payload,headers,scenario,detail",
    [
        ("1.0", _send_v03("x"), {}, "", "not an A2A 1.0 call method"),
        ("0.3.0", _send("x"), _HEADERS, "", "not an A2A 0.3.0 call method"),
        ("0.3.0", _send_v03("x"), _HEADERS, "", "A2A-Version"),
        ("1.0", _send("x"), {"A2A-Version": "0.3.0"}, "", "A2A-Version"),
        ("1.0", _send("x"), {"A2A-Version": "2.0"}, "", "A2A-Version"),
        ("1.0", _send("x"), {"A2A-Version": "1..0"}, "pii_block:every=2", "A2A-Version"),
        ("1.0", _send("x"), {"A2A-Version": "0.3.1"}, "pii_block:every=2", "A2A-Version"),
        ("0.3.0", _send_v03("x"), {"A2A-Version": "garbage"}, "pii_block:every=2", "A2A-Version"),
        ("0.3.0", _send_v03("x"), {"A2A-Version": "1.0.0"}, "pii_block:every=2", "A2A-Version"),
        (
            "0.3.0",
            _send_v03("x", "message/stream"),
            {},
            "pii_block:every=2",
            "no matching captured",
        ),
        (
            "0.3.0",
            _send_v03("x", "message/stream"),
            {},
            "request_limit:limit=2,window=60s",
            "no matching captured",
        ),
    ],
)
async def test_unsupported_calls_fail_before_agent_or_scenario_state(
    protocol: str, payload: Any, headers: dict[str, str], scenario: str, detail: str
) -> None:
    async def agent(scope: Any, receive: Any, send: Any) -> None:
        pytest.fail("unsupported call reached agent")

    scenarios = parse_scenarios([scenario] if scenario else [])
    wrapped = build_ingress(agent, scenarios, protocol_version=protocol)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapped), base_url="http://agent"
    ) as c:
        response = await c.post("/", json=payload, headers=headers)
    assert response.status_code == 400
    assert detail in response.text
    assert response.headers[SIMULATOR_HEADER] == "true"
    if scenarios:
        assert scenarios[0].on_call("x") is None


async def test_legacy_stream_without_scenarios_and_noncalls_preserve_bytes() -> None:
    bodies: list[bytes] = []

    async def agent(scope: Any, receive: Any, send: Any) -> None:
        bodies.append((await receive())["body"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"agent bytes"})

    wrapped = build_ingress(agent, protocol_version="0.3.0")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=wrapped), base_url="http://agent"
    ) as c:
        for body in (
            json.dumps(_send_v03("x", "message/stream")).encode(),
            b'{"method":"tasks/cancel","params":{"id":"t"}}',
            b'{"method":"unknown/method"}',
            b"not json",
        ):
            response = await c.post("/", content=body)
            assert bodies[-1] == body
            assert response.content == b"agent bytes"
            assert response.headers[SIMULATOR_HEADER] == "true"
