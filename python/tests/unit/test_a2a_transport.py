"""Scoped A2A transport seams (#1395, BG section 1.1/1.2). No framework or network."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from donkey_kit.core.budget import Budget
from donkey_kit.core.cachecontrol import CacheControls, cache_scope
from donkey_kit.core.classifier import classify
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import (
    AuthError,
    ConfigError,
    ContentSafetyBlocked,
    DonkeyError,
    GatewayUnavailable,
    PolicyViolation,
    TokenBudgetExceeded,
    ToolInvocationError,
    UpstreamModelError,
    UpstreamRequestError,
)
from donkey_kit.core.identity import UserScope, current_user
from donkey_kit.core.reactions import RefusalHandlers
from donkey_kit.core.telemetry import run_scope
from donkey_kit.core.transport import CALL_ID_HEADER, CORRELATION_HEADER, DonkeyAsyncClient

_ENDPOINT = "https://agent.example.com/proxy/"
_CARD = _ENDPOINT + ".well-known/agent-card.json"
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"


async def _headers(request: httpx.Request) -> dict[str, str]:
    assert str(request.url) in {_ENDPOINT, _CARD}
    return {"Authorization": "Bearer a2a-token"}


class _ForbiddenProvider:
    async def token(self) -> str:
        pytest.fail("A2A must not fetch the LLM token")

    async def invalidate(self) -> None:
        pytest.fail("A2A must not refresh the LLM token")


class _Stream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.reads = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.reads += 1
        yield self.body

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("mode", ["client-id", "jwt", "bearer"])
async def test_a2a_auth_is_isolated_and_callback_runs_per_request(mode: str) -> None:
    seen: list[httpx.Request] = []
    callbacks: list[httpx.Request] = []

    async def headers(request: httpx.Request) -> dict[str, str]:
        callbacks.append(request)
        assert "client_id" not in request.headers
        user = current_user()
        return {"Authorization": f"Bearer {user.token if user else 'a2a-service'}"}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": {}})

    cfg = DonkeyConfig(
        llm_proxy_url="https://llm.example.com/",
        llm_proxy_auth=mode,
        llm_proxy_client_id="llm-id",
        llm_proxy_client_secret="llm-secret",
        llm_proxy_wallet_client_id="wallet",
        llm_proxy_key_header="api-key",
        llm_proxy_key="llm-key",
        application_name="llm-app",
        business_group="llm-group",
        send_cost_headers=True,
        cost=CostTags(team="llm-team"),
        cost_project_header="x-custom-project",
    )
    async with DonkeyAsyncClient(
        cfg,
        _ForbiddenProvider(),
        transport=httpx.MockTransport(handler),
        headers={"client_id": "shared-id", "x-shared-default": "not-a2a"},
    ) as shared:
        origins = shared.checked_origins.copy()
        view = shared.a2a_view(_ENDPOINT, headers=headers)
        assert view is not shared.view()
        assert view is not shared.a2a_view(_ENDPOINT, headers=headers)
        with run_scope("a2a-run"):
            await view.get(_CARD)
            with (
                UserScope(DonkeyConfig(), "a2a-user"),
                cache_scope(CacheControls(skip=True)),
            ):
                await view.post(
                    _ENDPOINT,
                    headers={
                        "client_id": "stale-llm-id",
                        "client_secret": "stale-secret",
                        "X-Client-Id": "stale-wallet",
                        "api-key": "stale-key",
                        "X-Anypoint-Client-Application": "stale-app",
                        "X-Anypoint-Cost-Team": "stale-team",
                        "x-cache-skip": "true",
                        "x-custom-project": "stale-project",
                        "X-Anypoint-Cost-Project": "stale-default-project",
                    },
                    json={"model": "not-an-llm-call", "method": "message/send"},
                )
        await view.aclose()
        async with view:
            pass
        assert not shared.is_closed
        assert shared.checked_origins == origins
    assert view.is_closed
    assert callbacks == seen
    assert [r.headers["Authorization"] for r in seen] == ["Bearer a2a-service", "Bearer a2a-user"]
    for request in seen:
        assert request.headers[CORRELATION_HEADER] == "a2a-run"
        assert request.headers[CALL_ID_HEADER]
        assert "x-shared-default" not in request.headers
        assert "x-custom-project" not in request.headers
        assert not any(
            key.startswith(("x-anypoint-", "x-cache-"))
            or key in {"client_id", "client_secret", "x-client-id", "api-key"}
            for key in request.headers
        )
    assert seen[0].headers[CALL_ID_HEADER] != seen[1].headers[CALL_ID_HEADER]


@pytest.mark.parametrize(
    "url,method",
    [
        ("https://other.example.com/proxy/", "POST"),
        (_ENDPOINT + "other", "POST"),
        (_ENDPOINT.rstrip("/"), "POST"),
        (_ENDPOINT + "?secret=1", "POST"),
        (_CARD + "?x=1", "GET"),
        (_CARD, "POST"),
        ("https://agent.example.com/.well-known/agent-card.json", "GET"),
    ],
)
async def test_endpoint_and_card_confinement(url: str, method: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Out-of-scope request reached IO: {request.url}")

    async def headers(request: httpx.Request) -> dict[str, str]:
        pytest.fail("Out-of-scope request reached credential callback")

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=headers)
        with pytest.raises(ConfigError):
            await view.request(method, url)


async def test_core_revalidates_url_after_view_hooks_and_callback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Mutated A2A URL reached IO")

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)

        async def mutate(request: httpx.Request) -> None:
            request.url = httpx.URL(_ENDPOINT + "other")

        view.event_hooks["request"].append(mutate)
        with pytest.raises(ConfigError):
            await view.post(_ENDPOINT)
        view.event_hooks["request"].clear()

        async def headers(request: httpx.Request) -> dict[str, str]:
            await mutate(request)
            return {"Authorization": "Bearer not-sent"}

        with pytest.raises(ConfigError):
            await shared.a2a_view(_ENDPOINT, headers=headers).post(_ENDPOINT)
        request = httpx.Request("POST", _ENDPOINT, extensions={"donkey_a2a": True})
        with pytest.raises(ConfigError):
            await shared.send(request)


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirects_never_follow_even_when_shared_default_does(status: int) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, headers={"Location": _ENDPOINT + "other"})

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler), follow_redirects=True
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(ConfigError):
            await view.post(_ENDPOINT, follow_redirects=True)
        with pytest.raises(DonkeyError) as caught:
            await view.post(_ENDPOINT)
        assert caught.value.response.status_code == status
    assert len(seen) == 1


@pytest.mark.parametrize("status", [401, 500, 501, 502, 503, 504, 599])
@pytest.mark.parametrize("stream", [False, True])
async def test_a2a_never_retries_or_refreshes(status: int, stream: bool) -> None:
    seen: list[httpx.Request] = []
    body = _Stream(b'{"error":"fixture error"}')

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, stream=body)

    async with DonkeyAsyncClient(
        DonkeyConfig(max_retries=3, retry_model_calls_on_gateway_errors=True),
        _ForbiddenProvider(),
        transport=httpx.MockTransport(handler),
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        request = view.build_request("POST", _ENDPOINT)
        with pytest.raises(AuthError if status == 401 else UpstreamModelError) as caught:
            await view.send(request, stream=stream)
        assert caught.value.response.content == body.body
        assert caught.value.response.headers["x-should-retry"] == "false"
        assert caught.value.call_id == request.headers[CALL_ID_HEADER]
    assert len(seen) == 1
    assert body.closed


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "name,error_type",
    [("v1-azure-block-stream", ContentSafetyBlocked), ("v1-rate-stream", TokenBudgetExceeded)],
)
async def test_captured_refusals_run_hooks_and_preserve_response(name, error_type, stream) -> None:
    """Section 4 A2A captures feed the same budget/refusal seams as model calls."""
    lines = (_FIXTURES / f"{name}.headers.txt").read_text().splitlines()
    headers = [line.split(":", 1) for line in lines[1:] if line]
    path = _FIXTURES / f"{name}.body.json"
    raw = path.read_bytes() if path.exists() else b""
    refusals = []

    class RecordingClient(DonkeyAsyncClient):
        async def _on_refusal(self, request, response, violation, options):
            refusals.append(violation)
            return await super()._on_refusal(request, response, violation, options)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            int(lines[0].split()[1]),
            headers=[(key, value.strip()) for key, value in headers],
            stream=_Stream(raw),
        )

    budget = Budget()
    async with RecordingClient(
        DonkeyConfig(), None, budget=budget, transport=httpx.MockTransport(handler)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(error_type) as caught:
            await view.send(view.build_request("POST", _ENDPOINT), stream=stream)
        assert caught.value.response.content == raw
        assert len(refusals) == 1
        assert type(refusals[0]) is error_type
        assert "capture@example.com" not in str(caught.value)
        if error_type is TokenBudgetExceeded:
            # The captured gateway refuses despite reporting one remaining.
            assert budget.remaining == int(caught.value.response.headers["x-token-remaining"])


async def test_sse_is_not_consumed_or_wrapped_as_genai() -> None:
    body = _Stream(b'data: {"result":{"status":"working"}}\n\n')

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        async with view.stream("POST", _ENDPOINT, json={"model": "not-llm"}) as response:
            assert body.reads == 0
            assert await response.aread() == body.body
    assert body.closed


@pytest.mark.parametrize("stream", [False, True])
async def test_200_jsonrpc_error_classifies_buffered_and_streamed(stream: bool) -> None:
    raw = b'{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"private@example.com"}}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=_Stream(raw)
        )

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(ToolInvocationError) as caught:
            await view.send(view.build_request("POST", _ENDPOINT), stream=stream)
        assert "private@example.com" not in str(caught.value)
        assert caught.value.response.content == raw
        assert caught.value.response.is_closed


@pytest.mark.parametrize("stream", [False, True])
async def test_registered_jsonrpc_refusal_has_hook_parity_without_replay(stream: bool) -> None:
    class AcmeBlocked(PolicyViolation):
        policy = "acme_rpc"
        remediation = "Ask the Acme administrator to grant this operation."

    classify.register(
        match={"status": 200, "jsonrpc_code": -32099, "body": {"error.data.policy": "acme"}},
        raises=AcmeBlocked,
    )
    raw = (
        b'{"jsonrpc":"2.0","id":1,"error":{"code":-32099,'
        b'"message":"private@example.com","data":{"policy":"acme"}}}'
    )
    body = _Stream(raw)
    seen = []
    hooks = []
    reactions = RefusalHandlers()
    reactions.on(AcmeBlocked).call(lambda context: pytest.fail("A2A refusal replayed"))

    class RecordingClient(DonkeyAsyncClient):
        async def _on_response(self, request, response):
            hooks.append(("response", response))
            assert response.content == raw
            await super()._on_response(request, response)

        async def _on_refusal(self, request, response, violation, options):
            hooks.append(("refusal", response))
            assert type(violation) is AcmeBlocked
            return await super()._on_refusal(request, response, violation, options)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=body)

    async with RecordingClient(
        DonkeyConfig(max_retries=3),
        None,
        reactions=reactions,
        transport=httpx.MockTransport(handler),
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(AcmeBlocked) as caught:
            await view.send(view.build_request("POST", _ENDPOINT), stream=stream)
        assert caught.value.response.content == raw
        assert caught.value.call_id == seen[0].headers[CALL_ID_HEADER]
        assert "private@example.com" not in str(caught.value)
        assert hooks == [("response", caught.value.response), ("refusal", caught.value.response)]
    assert len(seen) == 1
    assert body.reads == 1
    assert body.closed


async def test_a2a_refusal_never_runs_replay_reactions() -> None:
    reactions = RefusalHandlers()

    def replay(context):
        pytest.fail("A2A refusals must not invoke LLM action-replay handlers")

    reactions.on(TokenBudgetExceeded).call(replay)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429)

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, reactions=reactions, transport=httpx.MockTransport(handler)
    ) as shared:
        with pytest.raises(TokenBudgetExceeded):
            await shared.a2a_view(_ENDPOINT, headers=_headers).post(_ENDPOINT)
    assert len(seen) == 1


@pytest.mark.parametrize("stream", [False, True])
async def test_nested_http_error_diagnostics_are_not_rendered(stream: bool) -> None:
    raw = b'{"error":{"message":"private@example.com","code":"invalid_request"}}'
    response = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal response
        response = httpx.Response(400, stream=_Stream(raw))
        return response

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(UpstreamRequestError) as caught:
            await view.send(view.build_request("POST", _ENDPOINT), stream=stream)
        assert "private@example.com" not in str(caught.value)
        assert caught.value.response is response
        assert response.content == raw


async def test_shared_hooks_cannot_change_scoped_destination() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Shared hook mutation reached IO")

    async def mutate(request: httpx.Request) -> None:
        request.url = httpx.URL(_ENDPOINT + "other")

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        shared.event_hooks["request"].append(mutate)
        with pytest.raises(ConfigError):
            await shared.a2a_view(_ENDPOINT, headers=_headers).post(_ENDPOINT)


async def test_callback_cannot_override_correlation_or_call_ids() -> None:
    async def headers(request: httpx.Request) -> dict[str, str]:
        return {CORRELATION_HEADER: "wrong-run", CALL_ID_HEADER: "wrong-call"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers[CORRELATION_HEADER] == "a2a-run"
        assert request.headers[CALL_ID_HEADER] != "wrong-call"
        return httpx.Response(200)

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        with run_scope("a2a-run"):
            await shared.a2a_view(_ENDPOINT, headers=headers).post(_ENDPOINT)


@pytest.mark.parametrize(
    "url",
    [
        "/relative",
        "https://user:secret@agent.example.com/",
        _ENDPOINT + "?x=1",
        _ENDPOINT + "#fragment",
    ],
)
def test_invalid_endpoint_configuration_is_refused(url: str) -> None:
    shared = DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    with pytest.raises(ConfigError) as caught:
        shared.a2a_view(url, headers=_headers)
    assert "secret" not in str(caught.value)


async def test_explicit_httpx_auth_is_refused_before_callback() -> None:
    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(lambda r: httpx.Response(200))
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(ConfigError):
            await view.post(_ENDPOINT, auth=("user", "secret"))


async def test_successful_json_stream_is_buffered_for_caller() -> None:
    body = _Stream(b'{"jsonrpc":"2.0","result":{}}')
    async with DonkeyAsyncClient(
        DonkeyConfig(),
        None,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, headers={"content-type": "application/json"}, stream=body)
        ),
    ) as shared:
        async with shared.a2a_view(_ENDPOINT, headers=_headers).stream(
            "POST", _ENDPOINT
        ) as response:
            assert body.reads == 1
            assert response.json() == {"jsonrpc": "2.0", "result": {}}
            assert await response.aread() == body.body
    assert body.closed


async def test_successful_json_stream_fallback_is_usable_by_official_sdk() -> None:
    sdk = pytest.importorskip("a2a.types")
    from a2a.client.transports.jsonrpc import JsonRpcTransport

    body = _Stream(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "message": {
                        "messageId": "reply",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "hello"}],
                    }
                },
            }
        ).encode()
    )
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=body)

    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(handler)
    ) as shared:
        transport = JsonRpcTransport(
            shared.a2a_view(_ENDPOINT, headers=_headers), sdk.AgentCard(), _ENDPOINT
        )
        events = [
            event async for event in transport.send_message_streaming(sdk.SendMessageRequest())
        ]
        assert len(events) == 1
        assert isinstance(events[0], sdk.StreamResponse)
        assert events[0].message.message_id == "reply"
        assert events[0].message.parts[0].text == "hello"
        assert not shared.is_closed
    assert len(seen) == 1
    assert body.reads == 1
    assert body.closed


async def test_oversized_error_stream_is_preserved_for_caller() -> None:
    raw = b"private@example.com" * 5000
    body = _Stream(raw)
    async with DonkeyAsyncClient(
        DonkeyConfig(),
        None,
        transport=httpx.MockTransport(lambda r: httpx.Response(500, stream=body)),
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(UpstreamModelError) as caught:
            await view.send(view.build_request("POST", _ENDPOINT), stream=True)
        assert "private@example.com" not in str(caught.value)
        response = caught.value.response
        assert not response.is_stream_consumed
        assert await response.aread() == raw
    assert body.closed


@pytest.mark.parametrize("close_fails", [False, True])
async def test_error_stream_read_failure_is_typed_and_closed(close_fails: bool) -> None:
    original = httpx.ReadError("broken stream")

    class BrokenStream(_Stream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"partial"
            raise original

        async def aclose(self) -> None:
            await super().aclose()
            if close_fails:
                raise RuntimeError("cleanup failed")

    body = BrokenStream(b"")
    async with DonkeyAsyncClient(
        DonkeyConfig(),
        None,
        transport=httpx.MockTransport(lambda r: httpx.Response(500, stream=body)),
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(GatewayUnavailable) as caught:
            await view.send(view.build_request("POST", _ENDPOINT), stream=True)
        assert caught.value.call_id
        assert caught.value.__cause__ is original
    assert body.closed


@pytest.mark.parametrize("close_fails", [False, True])
async def test_cancelled_503_body_read_closes_response(close_fails: bool) -> None:
    started = asyncio.Event()

    class BlockingStream(_Stream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            started.set()
            await asyncio.Event().wait()
            yield b"unreachable"

        async def aclose(self) -> None:
            await super().aclose()
            if close_fails:
                raise RuntimeError("cleanup failed")

    body = BlockingStream(b"")
    response = httpx.Response(503, stream=body)
    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(lambda r: response)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        task = asyncio.create_task(view.send(view.build_request("POST", _ENDPOINT), stream=True))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel("cancel body read")
        # Python 3.10 can drop the cancellation message when awaiting a task.
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert response.is_closed
        assert body.closed


async def test_error_body_read_preserves_original_exception_when_close_fails() -> None:
    original = ValueError("original body failure")

    class BrokenStream(_Stream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"partial"
            raise original

        async def aclose(self) -> None:
            await super().aclose()
            raise RuntimeError("cleanup failed")

    body = BrokenStream(b"")
    response = httpx.Response(503, stream=body)
    async with DonkeyAsyncClient(
        DonkeyConfig(), None, transport=httpx.MockTransport(lambda r: response)
    ) as shared:
        view = shared.a2a_view(_ENDPOINT, headers=_headers)
        with pytest.raises(ValueError) as caught:
            await view.send(view.build_request("POST", _ENDPOINT), stream=True)
        assert caught.value is original
        assert response.is_closed
        assert body.closed


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize("error_type", [httpx.ReadError, ValueError, asyncio.CancelledError])
async def test_failed_body_cleanup_logs_only_exception_type(
    status: int, error_type: type[BaseException], caplog: pytest.LogCaptureFixture
) -> None:
    original = error_type("secret-body-read-diagnostic")

    class BrokenStream(_Stream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"partial"
            raise original

        async def aclose(self) -> None:
            await super().aclose()
            raise RuntimeError("secret-cleanup-diagnostic")

    body = BrokenStream(b"")
    response = httpx.Response(status, headers={"content-type": "application/json"}, stream=body)
    with caplog.at_level(logging.DEBUG, logger="donkey_kit.core.transport.async_client"):
        async with DonkeyAsyncClient(
            DonkeyConfig(), None, transport=httpx.MockTransport(lambda r: response)
        ) as shared:
            view = shared.a2a_view(_ENDPOINT, headers=_headers)
            expected = GatewayUnavailable if error_type is httpx.ReadError else error_type
            with pytest.raises(expected) as caught:
                await view.send(view.build_request("POST", _ENDPOINT), stream=True)
            if error_type is httpx.ReadError:
                assert caught.value.__cause__ is original
            else:
                assert caught.value is original
    assert response.is_closed
    assert body.closed
    records = [
        record
        for record in caplog.records
        if record.name == "donkey_kit.core.transport.async_client"
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert records[0].getMessage() == "closing a failed response body failed (RuntimeError)"
    assert all(record.exc_info is None and record.exc_text is None for record in caplog.records)
    assert "secret-body-read-diagnostic" not in caplog.text
    assert "secret-cleanup-diagnostic" not in caplog.text
