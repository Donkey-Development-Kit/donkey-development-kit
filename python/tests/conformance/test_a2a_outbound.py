"""Official SDK outbound calls over mock HTTP, not a substitute protocol (#1395)."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit import classify as policy_classifier
from donkey_kit._testing import http_clients
from donkey_kit._testing import swap_transport as _swap_transport
from donkey_kit.core.correlation import current_correlation_id
from donkey_kit.core.errors import (
    AuthError,
    ConfigError,
    PolicyViolation,
    RegistryError,
    ToolInvocationError,
    classify,
)
from donkey_kit.registry.criteria import Check, GovernanceCriteria, GovernanceReport
from donkey_kit.registry.models import AgentHandle, AssetRef
from donkey_kit.tools.auth import BearerAuth

_URL = "https://gateway.example/agent/"
_REF = "g/agent/1.0.0"


def swap_transport(donkey, transport):
    _swap_transport(http_clients(donkey)[0], transport)


@pytest.fixture
def sdk():
    return pytest.importorskip("a2a.types")


@pytest.fixture
def request_message(sdk):
    return sdk.SendMessageRequest(
        message=sdk.Message(
            message_id="m1", role=sdk.Role.ROLE_USER, parts=[sdk.Part(text="hello")]
        )
    )


def _card(legacy=False, binding="JSONRPC"):
    base = {
        "name": "agent",
        "description": "test",
        "version": "1",
        "skills": [],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "capabilities": {"streaming": True},
    }
    if legacy:
        return {
            **base,
            "url": "https://malicious.example/upstream",
            "protocolVersion": "0.3.0",
            "preferredTransport": binding,
        }
    return {
        **base,
        "supportedInterfaces": [
            {
                "url": "https://malicious.example/upstream",
                "protocolBinding": binding,
                "protocolVersion": "1.0",
            }
        ],
    }


def _result(legacy=False):
    message = {
        "messageId": "reply",
        "role": "agent" if legacy else "ROLE_AGENT",
        "parts": [{"kind": "text", "text": "hello"}] if legacy else [{"text": "hello"}],
    }
    return {**message, "kind": "message"} if legacy else {"message": message}


def _registry(monkeypatch, donkey, governed=True):
    explain = AsyncMock(
        return_value=GovernanceReport(governed, [Check("policy", governed, "test verdict")])
    )
    resolve = AsyncMock(return_value=AgentHandle(AssetRef.parse(_REF), _URL))
    monkeypatch.setattr(donkey.registry, "explain", explain)
    monkeypatch.setattr(donkey.registry, "resolve_agent", resolve)
    return explain, resolve


def _cfg(**kwargs):
    return DonkeyConfig(
        telemetry=False,
        allow_a2a_sdk_telemetry=True,
        llm_proxy_url="https://llm.example/",
        llm_proxy_client_id="consumer",
        llm_proxy_client_secret="secret",
        **kwargs,
    )


class _Stream(httpx.AsyncByteStream):
    def __init__(self, body: bytes, block=False, finite=False):
        self.body = body
        self.closed = False
        self.block = block
        self.finite = finite
        self.started = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        yield self.body
        if not self.finite:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("legacy", [False, True])
async def test_send_official_response_pinned_gateway(monkeypatch, sdk, request_message, legacy):
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_card(legacy))
        body = json.loads(request.content)
        assert body["method"] == ("message/send" if legacy else "SendMessage")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": _result(legacy)}
        )

    async with Donkey(_cfg()) as donkey:
        explain, resolve = _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        response = await donkey.a2a.send(_REF, request_message)
        assert isinstance(response, sdk.SendMessageResponse)
        assert response.message.message_id == "reply"
        assert not donkey.http_client().is_closed
        explain.assert_awaited_once_with(AssetRef.parse(_REF), criteria=GovernanceCriteria())
        resolve.assert_awaited_once()
    assert [str(r.url) for r in seen] == [_URL + ".well-known/agent-card.json", _URL]
    assert len({r.headers["X-Correlation-Id"] for r in seen}) == 1
    assert current_correlation_id() is None
    assert all(r.headers["client_id"] == "consumer" for r in seen)
    assert all("X-Client-Id" not in r.headers for r in seen)


@pytest.mark.parametrize("legacy", [False, True])
async def test_stream_official_events_early_exit_and_reuse(
    monkeypatch, sdk, request_message, legacy
):
    streams = []
    methods = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card(legacy))
        body = json.loads(request.content)
        methods.append(body["method"])
        result = {"jsonrpc": "2.0", "id": body["id"], "result": _result(legacy)}
        if body["method"] in {"SendMessage", "message/send"}:
            return httpx.Response(200, json=result)
        stream = _Stream(("data: " + json.dumps(result) + "\n\n").encode())
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        async with donkey.a2a.stream(_REF, request_message) as events:
            async for event in events:
                assert isinstance(event, sdk.StreamResponse)
                assert event.message.message_id == "reply"
                break
        assert streams[0].closed
        assert current_correlation_id() is None
        assert not donkey.http_client().is_closed
        assert (await donkey.a2a.send(_REF, request_message)).message.message_id == "reply"
    assert methods == (
        ["message/stream", "message/send"] if legacy else ["SendStreamingMessage", "SendMessage"]
    )


async def test_governance_rejection_precedes_resolution_and_http(monkeypatch, request_message):
    async with Donkey(_cfg()) as donkey:
        explain, resolve = _registry(monkeypatch, donkey, governed=False)
        swap_transport(donkey, httpx.MockTransport(lambda r: pytest.fail("HTTP before verdict")))
        criteria = GovernanceCriteria(required_policies=["policy"])
        with pytest.raises(RegistryError):
            await donkey.a2a.send(_REF, request_message, criteria=criteria)
        explain.assert_awaited_once_with(AssetRef.parse(_REF), criteria=criteria)
        resolve.assert_not_awaited()


@pytest.mark.parametrize("status", [401, 503])
@pytest.mark.parametrize("streaming", [False, True])
async def test_refusal_is_typed_terminal_and_safe(monkeypatch, request_message, status, streaming):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        return httpx.Response(status, json={"error": "secret diagnostic"})

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        from donkey_kit.core.errors import DonkeyError

        with pytest.raises(DonkeyError) as caught:
            if streaming:
                async with donkey.a2a.stream(_REF, request_message) as events:
                    await anext(events)
            else:
                await donkey.a2a.send(_REF, request_message)
        assert "secret diagnostic" not in str(caught.value)
        if status == 401:
            assert isinstance(caught.value, AuthError)
    assert len(calls) == 2


@pytest.mark.parametrize("binding", ["GRPC", "HTTP+JSON"])
async def test_unsupported_transport_has_no_send(monkeypatch, request_message, binding):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_card(binding=binding))

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        with pytest.raises(ToolInvocationError, match="supported JSON-RPC"):
            await donkey.a2a.send(_REF, request_message)
    assert len(calls) == 1


@pytest.mark.parametrize("version", ["2.0", "0.2.0", "not-version"])
@pytest.mark.parametrize("compatible", [None, "0.3.0", "1.0"])
async def test_protocol_version_selection(monkeypatch, request_message, version, compatible):
    calls = []
    card = _card()
    card["supportedInterfaces"][0]["protocolVersion"] = version
    if compatible is not None:
        card["supportedInterfaces"].append(
            {**card["supportedInterfaces"][0], "protocolVersion": compatible}
        )

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=card)
        body = json.loads(request.content)
        legacy = compatible == "0.3.0"
        assert body["method"] == ("message/send" if legacy else "SendMessage")
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "result": _result(legacy)}
        )

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        if compatible is None:
            with pytest.raises(ToolInvocationError, match="supported JSON-RPC"):
                await donkey.a2a.send(_REF, request_message)
        else:
            assert (await donkey.a2a.send(_REF, request_message)).message.message_id == "reply"
    assert len(calls) == (1 if compatible is None else 2)


async def test_user_auth_is_read_per_http_request(monkeypatch, request_message):
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        body = json.loads(request.content)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": _result()})

    provider = AsyncMock()
    provider.token.return_value = "service"
    async with Donkey(_cfg(user_token_header="X-User-Token")) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        async with donkey.a2a.stream(_REF, request_message, auth=BearerAuth(provider)) as events:
            # Card discovery has already run, but the streaming POST is deferred.
            with donkey.as_user(token="first"):
                await anext(events)
        with donkey.as_user(token="second"):
            await donkey.a2a.send(_REF, request_message, auth=BearerAuth(provider))
        await donkey.a2a.send(_REF, request_message, auth=BearerAuth(provider))
    assert [r.headers.get("X-User-Token") for r in seen] == [
        None,
        "first",
        "second",
        "second",
        None,
        None,
    ]
    assert all(r.headers["Authorization"] == "Bearer service" for r in seen)
    assert all("client_id" not in r.headers for r in seen)
    assert provider.token.await_count == len(seen)
    provider.invalidate.assert_not_awaited()


async def test_endpoint_check_before_each_credential_acquisition(monkeypatch, request_message):
    from donkey_kit.a2a import client

    provider = AsyncMock()
    provider.token.return_value = "service"
    order = []

    def check(*args, **kwargs):
        order.append("check")
        assert kwargs["credential"] == "code"
        if len(order) == 3:
            raise ConfigError("endpoint became unsafe")

    async def token():
        order.append("token")
        return "service"

    provider.token.side_effect = token
    monkeypatch.setattr(client, "check_gateway_endpoint", check)
    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(lambda r: httpx.Response(200, json=_card())))
        with pytest.raises(ConfigError):
            await donkey.a2a.send(_REF, request_message, auth=BearerAuth(provider))
    assert order == ["check", "token", "check"]


@pytest.mark.parametrize(
    "phase", ["card-json", "card-payload", "send-json", "send-payload", "sse", "sse-payload"]
)
async def test_malformed_protocol_preserves_response_and_ids(monkeypatch, request_message, phase):
    responses = []
    malformed = b'{"secret diagnostic":'

    def handler(request):
        if request.method == "GET" and not phase.startswith("card"):
            return httpx.Response(200, json=_card())
        content = malformed
        content_type = "application/json"
        if phase.endswith("payload"):
            content = b'{"secret diagnostic":true}'
        if phase == "card-payload":
            content = b'{"supportedInterfaces":"secret diagnostic"}'
        if phase.startswith("sse"):
            if phase == "sse-payload":
                content = json.dumps(
                    {"jsonrpc": "2.0", "id": json.loads(request.content)["id"], "result": {}}
                ).encode()
            content = b"data: " + content + b"\n\n"
            content_type = "text/event-stream"
        response = httpx.Response(
            200,
            headers={"content-type": content_type, "x-request-id": "provider-id"},
            content=content,
        )
        responses.append(response)
        return response

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        with pytest.raises(ToolInvocationError) as caught:
            if phase.startswith("sse"):
                async with donkey.a2a.stream(_REF, request_message) as events:
                    await anext(events)
            else:
                await donkey.a2a.send(_REF, request_message)
        assert "secret diagnostic" not in str(caught.value)
        assert caught.value.__suppress_context__
        assert caught.value.__cause__ is None
        assert caught.value.response is responses[-1]
        assert caught.value.response.content == responses[-1].content
        assert caught.value.correlation_id == responses[-1].request.headers["X-Correlation-Id"]
        assert caught.value.call_id == responses[-1].request.headers["X-Donkey-Request-Id"]
        assert caught.value.request_id == "provider-id"


async def test_mutated_handle_is_not_an_accepted_ref(monkeypatch, request_message):
    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        handle = replace(await donkey.registry.resolve_agent(_REF), endpoint_url="https://evil/")
        with pytest.raises(RegistryError):
            await donkey.a2a.send(handle, request_message)


async def test_stream_cancellation_closes_response_without_remote_cancel(
    monkeypatch, request_message
):
    stream = _Stream(b"", block=True)
    methods = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))

        async def consume():
            async with donkey.a2a.stream(_REF, request_message) as events:
                await anext(events)

        task = asyncio.create_task(consume())
        await stream.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stream.closed
        assert not donkey.http_client().is_closed
    assert methods == ["SendStreamingMessage"]


@pytest.mark.parametrize("failure", ["cancel", "invocation", "cleanup", "cleanup-cancel"])
async def test_failing_close_preserves_error_and_attempts_all_cleanup(
    monkeypatch, request_message, failure
):
    from a2a.client.transports.jsonrpc import JsonRpcTransport

    class FailingStream(_Stream):
        async def aclose(self):
            self.closed = True
            if failure == "cleanup-cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("secret close diagnostic")

    payload = {"jsonrpc": "2.0", "id": "1", "result": _result()}
    stream = FailingStream(
        ("data: " + json.dumps(payload) + "\n\n").encode(), block=failure == "cancel"
    )
    transport_close = AsyncMock(wraps=JsonRpcTransport.close)

    # Observe the public cleanup method without replacing SDK parsing or tracing.
    async def close(transport):
        await transport_close(transport)

    monkeypatch.setattr(JsonRpcTransport, "close", close)

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        if json.loads(request.content)["method"] == "SendMessage":
            body = json.loads(request.content)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": body["id"], "result": _result()}
            )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        shared = http_clients(donkey)[0]
        view_factory = shared.a2a_view
        views = []

        def view(*args, **kwargs):
            result = view_factory(*args, **kwargs)
            result.aclose = AsyncMock(wraps=result.aclose)
            views.append(result)
            return result

        monkeypatch.setattr(shared, "a2a_view", view)
        swap_transport(donkey, httpx.MockTransport(handler))
        invocation_error = ToolInvocationError("original invocation failure")

        async def consume():
            async with donkey.a2a.stream(_REF, request_message) as events:
                await anext(events)
                if failure == "invocation":
                    raise invocation_error

        task = asyncio.create_task(consume())
        await stream.started.wait()
        if failure == "cancel":
            task.cancel()
        expected = asyncio.CancelledError if "cancel" in failure else ToolInvocationError
        with pytest.raises(expected) as caught:
            await task
        if failure == "invocation":
            assert caught.value is invocation_error
        assert "secret close diagnostic" not in str(caught.value)
        transport_close.assert_awaited_once()
        assert views[0].aclose.await_count == 2
        assert stream.closed
        assert not shared.is_closed
        assert (await donkey.a2a.send(_REF, request_message)).message.message_id == "reply"


@pytest.mark.parametrize("sampler", ["always-on", "parent-based"])
@pytest.mark.parametrize(
    "scenario", ["default", "ddk-off", "late-optout", "optout", "allow", "allow-ddk-off"]
)
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_sdk_telemetry_privacy_boundary(sdk, sampler, scenario, legacy, streaming):
    pytest.importorskip("opentelemetry.sdk")
    # Global providers are set-once; use a fresh interpreter, not private OTel globals.
    code = textwrap.dedent(
        """
        import asyncio
        import json
        import os
        import sys
        import traceback
        sys.path.insert(0, SOURCE)
        from dataclasses import replace
        from unittest.mock import AsyncMock
        import httpx
        from opentelemetry import context, trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        if SCENARIO == 'optout':
            os.environ['OTEL_INSTRUMENTATION_A2A_SDK_ENABLED'] = 'false'
        else:
            os.environ['OTEL_INSTRUMENTATION_A2A_SDK_ENABLED'] = 'true'
        from a2a.types import Message, Part, Role, SendMessageRequest
        from a2a.utils import telemetry as sdk_telemetry
        assert sdk_telemetry.otel_installed is True
        assert sdk_telemetry.otel_enabled is (SCENARIO != 'optout')
        if SCENARIO == 'late-optout':
            os.environ['OTEL_INSTRUMENTATION_A2A_SDK_ENABLED'] = 'false'
        from donkey_kit import Donkey
        from donkey_kit.core.errors import ConfigError, ToolInvocationError
        from test_a2a_outbound import _cfg, _card, swap_transport, _REF

        exporter = InMemorySpanExporter()
        provider = TracerProvider(sampler=ALWAYS_ON if SAMPLER == 'always-on'
                                  else ParentBased(ALWAYS_ON))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        seen = []
        secret = 'sensitive-parser-diagnostic'
        def handler(request):
            seen.append(request)
            if request.method == 'GET':
                return httpx.Response(200, json=_card(LEGACY))
            payload = {'jsonrpc': '2.0', 'id': json.loads(request.content)['id'],
                       'result': ({'kind': 'message', 'messageId': {secret: True}} if LEGACY
                                  else {'message': {'messageId': {secret: True}}})}
            if STREAMING:
                return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                    content=('data: ' + json.dumps(payload) + '\\n\\n').encode())
            return httpx.Response(200, json=payload)

        async def main():
            parent = trace.get_tracer('host').start_span('parent')
            token = context.attach(trace.set_span_in_context(parent))
            allowed = SCENARIO in {'allow', 'allow-ddk-off'}
            telemetry = SCENARIO not in {'ddk-off', 'allow-ddk-off'}
            blocked = SCENARIO in {'default', 'ddk-off', 'late-optout'}
            async with Donkey(replace(_cfg(), telemetry=telemetry,
                                     allow_a2a_sdk_telemetry=allowed)) as donkey:
                donkey.registry.explain = AsyncMock()
                from donkey_kit.registry.criteria import GovernanceReport
                from donkey_kit.registry.models import AgentHandle, AssetRef
                donkey.registry.explain.return_value = GovernanceReport(True, [])
                donkey.registry.resolve_agent = AsyncMock(return_value=
                    AgentHandle(AssetRef.parse(_REF), 'https://gateway.example/agent/'))
                swap_transport(donkey, httpx.MockTransport(handler))
                request = SendMessageRequest(message=Message(message_id='m1',
                    role=Role.ROLE_USER, parts=[Part(text='hello')]))
                before = context.get_current()
                try:
                    if STREAMING:
                        async with donkey.a2a.stream(_REF, request) as events:
                            await anext(events)
                    else:
                        await donkey.a2a.send(_REF, request)
                except (ConfigError, ToolInvocationError) as error:
                    assert isinstance(error, ConfigError if blocked else ToolInvocationError)
                    assert secret not in ''.join(traceback.format_exception(error))
                    if blocked:
                        assert 'before importing a2a' in error.remediation
                        assert 'sensitive diagnostics' in error.remediation
                        donkey.registry.explain.assert_not_awaited()
                        donkey.registry.resolve_agent.assert_not_awaited()
                        assert seen == []
                else:
                    raise AssertionError('malformed response accepted')
                assert context.get_current() == before
            spans = exporter.get_finished_spans()
            ddk = [s for s in spans if s.instrumentation_scope.name == 'donkey_kit']
            sdk = [s for s in spans if s.instrumentation_scope.name == 'a2a-python-sdk']
            assert len(ddk) == (1 if telemetry and not blocked else 0)
            assert bool(sdk) is (allowed and not blocked), [s.name for s in spans]
            for span in ddk:
                assert span.name == 'donkey.a2a.call'
                assert span.parent.span_id == parent.get_span_context().span_id
                assert span.status.status_code.name == 'ERROR'
                assert span.attributes['error.type'] == 'ToolInvocationError'
                assert not span.events
                assert secret not in str(span.attributes)
                assert secret not in str(span.status.description)
            if sdk:
                expected_parent = ddk[0].context if telemetry else parent.get_span_context()
                assert any(s.parent.span_id == expected_parent.span_id for s in sdk)
                assert all(s.context.trace_id == expected_parent.trace_id for s in sdk)
                if not STREAMING:
                    assert any(s.events for s in sdk)
                    diagnostics = [(e.attributes, s.status.description)
                                   for s in sdk for e in s.events]
                    assert all(e.attributes['exception.message'] for s in sdk for e in s.events)
                    if LEGACY:
                        assert secret in str(diagnostics), diagnostics
            if telemetry and not blocked:
                assert seen[0].headers['traceparent'] == seen[1].headers['traceparent']
                wire_parent = int(seen[1].headers['traceparent'].split('-')[2], 16)
                assert wire_parent == ddk[0].context.span_id
                assert int(seen[1].headers['traceparent'].split('-')[-1], 16) & 1
            else:
                assert all('traceparent' not in request.headers for request in seen)
            assert trace.get_current_span() is parent
            context.detach(token)
            parent.end()
            with trace.get_tracer('host').start_as_current_span('after'):
                pass
            assert exporter.get_finished_spans()[-1].name == 'after'
        asyncio.run(main())
        provider.shutdown()
        """
    )
    code = (
        f"SAMPLER={sampler!r}\nSCENARIO={scenario!r}\nLEGACY={legacy!r}\nSTREAMING={streaming!r}\n"
        f"SOURCE={str(Path(__file__).resolve().parents[2] / 'src')!r}\n" + code
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("streaming", [False, True])
def test_sdk_without_opentelemetry_allows_default_calls(sdk, streaming):
    code = textwrap.dedent(
        """
        import asyncio
        import builtins
        import json
        import sys
        sys.path.insert(0, SOURCE)
        original = builtins.__import__
        def without_otel(name, *args, **kwargs):
            if name == 'opentelemetry' or name.startswith('opentelemetry.'):
                raise ImportError('optional dependency absent')
            return original(name, *args, **kwargs)
        builtins.__import__ = without_otel
        from a2a.types import Message, Part, Role, SendMessageRequest
        from a2a.utils import telemetry
        assert telemetry.otel_installed is False
        from donkey_kit import Donkey
        from donkey_kit.core.errors import ToolInvocationError
        from dataclasses import replace
        from unittest.mock import AsyncMock
        import httpx
        from test_a2a_outbound import _cfg, _card, _REF, _URL, swap_transport
        from donkey_kit.registry.criteria import GovernanceReport
        from donkey_kit.registry.models import AgentHandle, AssetRef
        seen = []
        def handler(request):
            seen.append(request)
            if request.method == 'GET':
                return httpx.Response(200, json=_card())
            payload = {'jsonrpc': '2.0', 'id': json.loads(request.content)['id'],
                       'result': {}}
            if STREAMING:
                return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                    content=('data: ' + json.dumps(payload) + '\\n\\n').encode())
            return httpx.Response(200, json=payload)
        async def main():
            async with Donkey(replace(_cfg(), telemetry=True,
                                     allow_a2a_sdk_telemetry=False)) as donkey:
                donkey.registry.explain = AsyncMock(return_value=GovernanceReport(True, []))
                donkey.registry.resolve_agent = AsyncMock(return_value=
                    AgentHandle(AssetRef.parse(_REF), _URL))
                swap_transport(donkey, httpx.MockTransport(handler))
                request = SendMessageRequest(message=Message(message_id='m1',
                    role=Role.ROLE_USER, parts=[Part(text='hello')]))
                try:
                    if STREAMING:
                        async with donkey.a2a.stream(_REF, request) as events:
                            await anext(events)
                    else:
                        await donkey.a2a.send(_REF, request)
                except ToolInvocationError:
                    pass
                else:
                    raise AssertionError('empty payload accepted')
                donkey.registry.explain.assert_awaited_once()
                assert len(seen) == 2
                assert all('traceparent' not in r.headers for r in seen)
        asyncio.run(main())
        """
    )
    code = (
        f"STREAMING={streaming!r}\n"
        f"SOURCE={str(Path(__file__).resolve().parents[2] / 'src')!r}\n" + code
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("early_exit", [False, True])
async def test_stream_exhaustion_and_span_lifetime(
    monkeypatch, sdk, request_message, legacy, early_exit
):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from donkey_kit.core import telemetry

    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("donkey_kit"))
    seen = []
    streams = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_card(legacy))
        payload = {
            "jsonrpc": "2.0",
            "id": json.loads(request.content)["id"],
            "result": _result(legacy),
        }
        stream = _Stream(("data: " + json.dumps(payload) + "\n\n").encode(), finite=not early_exit)
        streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    try:
        async with Donkey(replace(_cfg(), telemetry=True)) as donkey:
            _registry(monkeypatch, donkey)
            swap_transport(donkey, httpx.MockTransport(handler))
            with donkey.run(id="parent-run"):
                async with donkey.a2a.stream(_REF, request_message) as events:
                    assert not exporter.get_finished_spans()
                    collected = []
                    async for event in events:
                        collected.append(event)
                        if early_exit:
                            break
                    assert len(collected) == 1
                    assert isinstance(collected[0], sdk.StreamResponse)
                    assert not exporter.get_finished_spans()
                assert current_correlation_id() == "parent-run"
            spans = exporter.get_finished_spans()
            assert len(spans) == 1
            assert spans[0].name == "donkey.a2a.call"
            assert streams[0].closed
            assert seen[0].headers["traceparent"] == seen[1].headers["traceparent"]
            assert all(r.headers["X-Correlation-Id"] == "parent-run" for r in seen)
    finally:
        provider.shutdown()


@pytest.mark.parametrize("streaming", [False, True])
async def test_captured_pii_refusal_passes_through(monkeypatch, request_message, streaming):
    """Captured Anypoint PII refusal (§4, #1393) reaches the facade unchanged."""
    root = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"
    body = (root / "v1-pii-send.body.json").read_bytes()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        return httpx.Response(403, headers={"content-type": "application/json"}, content=body)

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        expected = type(classify(httpx.Response(403, content=body)))
        with pytest.raises(expected) as caught:
            if streaming:
                async with donkey.a2a.stream(_REF, request_message) as events:
                    await anext(events)
            else:
                await donkey.a2a.send(_REF, request_message)
        assert caught.value.response.content == body
        assert "capture@example.com" not in str(caught.value)


async def test_auth_required_is_returned_without_oauth_completion(
    monkeypatch, sdk, request_message
):
    root = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "a2a_policies"
    body = (root / "v03-auth-challenge-send.body.json").read_bytes()
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_card(legacy=True))
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        response = await donkey.a2a.send(_REF, request_message)
        assert isinstance(response, sdk.SendMessageResponse)
        assert response.task.status.state == sdk.TaskState.TASK_STATE_AUTH_REQUIRED
    assert len(seen) == 2


async def test_send_cancellation_preserved(monkeypatch, request_message):
    entered = asyncio.Event()

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        entered.set()
        await asyncio.Event().wait()

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        task = asyncio.create_task(donkey.a2a.send(_REF, request_message))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not donkey.http_client().is_closed


async def test_user_and_service_header_collision_refused(monkeypatch, request_message):
    provider = AsyncMock()
    provider.token.return_value = "service"
    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(
            donkey, httpx.MockTransport(lambda r: pytest.fail("unsafe credentials sent"))
        )
        with donkey.as_user(token="user"), pytest.raises(ConfigError, match="same header"):
            await donkey.a2a.send(_REF, request_message, auth=BearerAuth(provider))


@pytest.mark.parametrize("header", ["Authorization", "aUtHoRiZaTiOn", "X-User-Token"])
async def test_user_token_header_scheme(monkeypatch, request_message, header):
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": json.loads(request.content)["id"], "result": _result()},
        )

    async with Donkey(_cfg(user_token_header=header)) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        with donkey.as_user(token="user-token"):
            await donkey.a2a.send(_REF, request_message)
    expected = "Bearer user-token" if header.lower() == "authorization" else "user-token"
    assert all(request.headers[header] == expected for request in seen)


@pytest.mark.parametrize("streaming", [False, True])
async def test_registered_jsonrpc_refusal_parity(monkeypatch, request_message, streaming):
    class CustomRefusal(PolicyViolation):
        policy = "custom-a2a"
        remediation = "Check the custom gateway policy."

    responses = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": json.loads(request.content)["id"],
                "error": {"code": -32099, "message": "secret diagnostic", "type": "custom_a2a"},
            }
        ).encode()
        response = httpx.Response(
            200,
            headers={"content-type": "application/json", "x-custom-policy": "blocked"},
            stream=_Stream(body, finite=True),
        )
        responses.append(response)
        return response

    policy_classifier.register(
        match={"status": 200, "jsonrpc_code": -32099, "headers": {"x-custom-policy": "blocked"}},
        raises=CustomRefusal,
    )
    try:
        async with Donkey(_cfg()) as donkey:
            _registry(monkeypatch, donkey)
            swap_transport(donkey, httpx.MockTransport(handler))
            with pytest.raises(CustomRefusal) as caught:
                if streaming:
                    async with donkey.a2a.stream(_REF, request_message) as events:
                        await anext(events)
                else:
                    await donkey.a2a.send(_REF, request_message)
            assert caught.value.response is responses[-1]
            assert caught.value.response.json()["error"]["code"] == -32099
            assert caught.value.correlation_id == responses[-1].request.headers["X-Correlation-Id"]
            assert caught.value.call_id == responses[-1].request.headers["X-Donkey-Request-Id"]
            assert "secret diagnostic" not in str(caught.value)
    finally:
        policy_classifier.unregister(CustomRefusal)


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
async def test_empty_payload_is_not_success(monkeypatch, request_message, legacy, streaming):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card(legacy))
        payload = {"jsonrpc": "2.0", "id": json.loads(request.content)["id"], "result": {}}
        return httpx.Response(200, json=payload)

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        with pytest.raises(ToolInvocationError):
            if streaming:
                async with donkey.a2a.stream(_REF, request_message) as events:
                    await anext(events)
            else:
                await donkey.a2a.send(_REF, request_message)


async def test_sse_protocol_error_is_safe(monkeypatch, request_message):
    stream = _Stream(
        b'event: error\ndata: {"jsonrpc":"2.0","id":"1",'
        b'"error":{"code":-32603,"message":"secret diagnostic"}}\n\n',
        finite=True,
    )

    responses = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=_card())
        response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        responses.append(response)
        return response

    async with Donkey(_cfg()) as donkey:
        _registry(monkeypatch, donkey)
        swap_transport(donkey, httpx.MockTransport(handler))
        with pytest.raises(ToolInvocationError) as caught:
            async with donkey.a2a.stream(_REF, request_message) as events:
                await anext(events)
        assert "secret diagnostic" not in str(caught.value)
        assert caught.value.response is responses[-1]
        assert caught.value.correlation_id == responses[-1].request.headers["X-Correlation-Id"]
        assert caught.value.call_id == responses[-1].request.headers["X-Donkey-Request-Id"]
        assert caught.value.__cause__ is None
        assert caught.value.__suppress_context__
        assert stream.closed
