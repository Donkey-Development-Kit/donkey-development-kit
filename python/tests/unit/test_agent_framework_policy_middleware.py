"""``donkey.agent_framework.policy_middleware()`` on a real Agent Framework
``Agent`` (BG §1.2, #739).

The middleware used to be a plain ``(context, next_)`` wrapper that MAF
rejected at ``Agent(...)`` construction and that only re-raised a
``PolicyViolation`` the client never raised. These tests build a real
``Agent`` on ``donkey.agent_framework.chat_client()`` and send through the
shared client to an ``httpx.MockTransport``, so the refusal takes the same path
it takes against the proxy: openai raises ``PermissionDeniedError`` and MAF
wraps it in a ``ChatClientException``.

Skipped where ``agent_framework`` is not installed; the
``agent-framework-middleware`` CI job installs it.
"""

from __future__ import annotations

import httpx
import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import PIIDetected

af = pytest.importorskip("agent_framework")
from agent_framework.exceptions import ChatClientException  # noqa: E402


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


def _pii_proxy(sent: list[httpx.Request]) -> httpx.MockTransport:
    """A proxy that refuses every request with the captured PII 403 shape
    (``tests/fixtures/rejections``) and records each send."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            403,
            json={
                "error": {
                    "type": "pii_detected",
                    "message": 'blocked: [{"pii_type": "EMAIL"}]',
                }
            },
        )

    return httpx.MockTransport(handler)


def _ok_proxy() -> httpx.MockTransport:
    """A proxy that answers a Responses API call with one assistant message."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "object": "response",
                "created_at": 0,
                "model": "gpt-4o",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "hello", "annotations": []}],
                    }
                ],
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
            },
        )

    return httpx.MockTransport(handler)


def _agent(fab: Donkey) -> object:
    return af.Agent(
        client=fab.agent_framework.chat_client("gpt-4o"),
        middleware=[fab.agent_framework.policy_middleware()],
    )


def test_policy_middleware_registers_on_a_real_agent() -> None:
    """The old wrapper raised ``MiddlewareException: Cannot determine
    middleware type`` here."""
    fab = Donkey(_cfg())

    agent = _agent(fab)

    assert isinstance(agent, af.Agent)


async def test_pii_refusal_ends_the_run_typed_after_one_send() -> None:
    sent: list[httpx.Request] = []
    fab = Donkey(_cfg())
    fab._http._swap_transport(_pii_proxy(sent))
    agent = _agent(fab)

    try:
        with fab.run(id="run-maf"):
            with pytest.raises(PIIDetected) as excinfo:
                await agent.run("my email is a@b.example")  # type: ignore[attr-defined]
    finally:
        await fab.aclose()

    err = excinfo.value
    assert len(sent) == 1  # no SDK, transport or framework retry
    assert err.correlation_id == "run-maf"
    assert err.call_id is not None
    assert "EMAIL" in err.entities
    # The MAF error is kept on .framework_error, not chained: its message
    # repeats the gateway text, which tracebacks would render.
    assert isinstance(err.framework_error, ChatClientException)
    assert err.__cause__ is None


async def test_streamed_pii_refusal_ends_the_run_typed_after_one_send() -> None:
    """A streamed refusal surfaces when the stream is pulled, after the
    middleware has returned; the hook on the response stream still types it."""
    sent: list[httpx.Request] = []
    fab = Donkey(_cfg())
    fab._http._swap_transport(_pii_proxy(sent))
    agent = _agent(fab)

    try:
        with fab.run(id="run-maf-stream"):
            with pytest.raises(PIIDetected) as excinfo:
                async for _ in agent.run("my email is a@b.example", stream=True):  # type: ignore[attr-defined]
                    pass
    finally:
        await fab.aclose()

    assert len(sent) == 1
    assert excinfo.value.correlation_id == "run-maf-stream"


async def test_allowed_call_passes_through_unchanged() -> None:
    fab = Donkey(_cfg())
    fab._http._swap_transport(_ok_proxy())
    agent = _agent(fab)

    try:
        response = await agent.run("hi")  # type: ignore[attr-defined]
    finally:
        await fab.aclose()

    assert response.text == "hello"


async def test_error_without_a_proxy_response_propagates_untouched() -> None:
    """A failure with no HTTP response behind it (here, the transport cannot
    connect) is not a gateway refusal and must not be masked as one."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    fab = Donkey(_cfg())
    fab._http._swap_transport(httpx.MockTransport(handler))
    agent = _agent(fab)

    try:
        with pytest.raises(ChatClientException):
            await agent.run("hi")  # type: ignore[attr-defined]
    finally:
        await fab.aclose()
