"""``Donkey.simulate_a2a()``: declared agents called offline via the SDK (#1652, ADR 0024)."""

from __future__ import annotations

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import (
    ConfigError,
    ContentSafetyBlocked,
    RegistryError,
    TokenBudgetExceeded,
    ToolInvocationError,
    classify,
)
from donkey_kit.simulator.a2a import CAPTURES
from donkey_kit.simulator.fixtures import load, replay_headers

pytestmark = [pytest.mark.disable_socket, pytest.mark.timeout(15)]

_REF = "acme/triage-agent/1.0.0"


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


def _donkey() -> Donkey:
    # No proxy URL or control-plane config: any registry read or real send would fail.
    return Donkey(DonkeyConfig(telemetry=False, allow_a2a_sdk_telemetry=True))


async def _call(donkey: Donkey, message, streaming: bool):
    if streaming:
        async with donkey.a2a.stream(_REF, message) as events:
            return await anext(events)
    return await donkey.a2a.send(_REF, message)


def _expected(capture: str, version: str) -> type:
    """The type classify() gives the response exactly as the simulator serves it."""
    fixture = load(CAPTURES[(capture, version)])
    headers = replay_headers(fixture)
    if fixture.content_type:
        headers["content-type"] = fixture.content_type
    return type(classify(httpx.Response(fixture.status, headers=headers, content=fixture.body)))


@pytest.mark.parametrize(("capture", "version"), sorted(CAPTURES))
async def test_named_capture_raises_its_classified_error(
    sdk, request_message, capture, version
) -> None:
    fixture = load(CAPTURES[(capture, version)])
    expected = _expected(capture, version)
    async with _donkey() as donkey:
        with donkey.simulate_a2a(capture, agents=[_REF], version=version):
            with pytest.raises(expected) as caught:
                await _call(donkey, request_message, capture.endswith("-stream"))
            response = caught.value.response
            assert response is not None
            assert response.headers["x-donkey-simulator"] == "true"
            assert response.content == fixture.body
            assert "capture@example.com" not in str(caught.value)
            # times=1: the next call of the same kind gets the simulated reply.
            reply = await _call(donkey, request_message, capture.endswith("-stream"))
            assert reply.message.parts[0].text == "simulated reply"


def test_typed_errors_are_the_documented_ones() -> None:
    for version in ("1.0", "0.3"):
        assert _expected("pii-send", version) is ToolInvocationError
        assert _expected("rate-send", version) is TokenBudgetExceeded
    assert _expected("azure-block-send", "1.0") is ContentSafetyBlocked


async def test_send_capture_leaves_stream_calls_alone(sdk, request_message) -> None:
    async with _donkey() as donkey:
        with donkey.simulate_a2a("pii-send", agents=[_REF], times=2, reply=str.upper):
            event = await _call(donkey, request_message, streaming=True)
            assert event.message.parts[0].text == "HELLO"
            for _ in range(2):
                with pytest.raises(ToolInvocationError):
                    await donkey.a2a.send(_REF, request_message)
            assert (await donkey.a2a.send(_REF, request_message)).message.parts[0].text == "HELLO"


async def test_undeclared_ref_raises_before_any_request(sdk, request_message) -> None:
    async with _donkey() as donkey:
        with donkey.simulate_a2a("pii-send", agents=[_REF]):
            with pytest.raises(RegistryError, match="not one of the agents"):
                await donkey.a2a.send("acme/other-agent/1.0.0", request_message)


async def test_after_exit_the_registry_is_used_again(sdk, request_message) -> None:
    async with _donkey() as donkey:
        with donkey.simulate_a2a("pii-send", agents=[_REF]):
            pass
        # The real registry is consulted again; with no control-plane config it refuses.
        with pytest.raises(ConfigError):
            await donkey.a2a.send(_REF, request_message)
