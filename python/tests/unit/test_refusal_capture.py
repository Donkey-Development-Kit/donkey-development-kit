"""The per-call refusal capture behind ADK ``model()``'s typed refusals (#969).

LiteLLM re-raises a gateway refusal around a response it rebuilt, so nothing on
its error leads back to the SDK's transport. ``core.refusals.capture_refusals``
records the typed outcome of each governed send made during one framework call,
the adapter tags the framework's error with it, and ``translate()`` returns the
tag. Base-only: the transport cases use ``httpx.MockTransport``, and the ADK
client subclass is built over a stand-in base, so no framework extra is needed.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from donkey_kit.core import _wire
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import (
    ConfigError,
    DonkeyError,
    GatewayUnavailable,
    ModelSubstituted,
    PIIDetected,
)
from donkey_kit.core.refusals import (
    RefusalCapture,
    TypedRefusals,
    capture_refusals,
    capturing,
    record_outcome,
    translate,
)
from donkey_kit.core.transport import DonkeyAsyncClient, DonkeyClient, pipeline
from donkey_kit.integrations.adk import _governed_llm_client_class

_URL = "https://proxy.example/chat/completions"
_CFG = DonkeyConfig(
    llm_proxy_url="https://proxy.example/",
    llm_proxy_client_id="cid-capture",
    llm_proxy_client_secret="csecret-capture",
    max_retries=0,
)
_PII_BODY = {"error": {"type": "pii_detected", "message": 'blocked: [{"pii_type": "EMAIL"}]'}}
_Handler = Callable[[httpx.Request], httpx.Response]

KINDS = ["async", "sync"]


def _pii(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403, json=_PII_BODY)


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"model": "gpt-4o"})


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _substituted(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={_wire.LLM_MODEL_HEADER: "other"}, json={"model": "x"})


def _send_all(kind: str, handlers: list[_Handler], *, cfg: DonkeyConfig = _CFG) -> None:
    """One send per handler, through one fresh client of ``kind``. A send that
    raises is swallowed: the capture, not the raise, is under test."""
    replies = iter(handlers)
    transport = httpx.MockTransport(lambda request: next(replies)(request))
    body = {"model": "gpt-4o"}
    if kind == "sync":
        with DonkeyClient(cfg, transport=transport) as client:
            for _ in handlers:
                try:
                    client.post(_URL, json=body)
                except DonkeyError:
                    pass
        return

    async def run() -> None:
        async with DonkeyAsyncClient(cfg, None, transport=transport) as client:
            for _ in handlers:
                try:
                    await client.post(_URL, json=body)
                except DonkeyError:
                    pass

    asyncio.run(run())


# --- the capture itself ---------------------------------------------------------


def test_record_outcome_outside_a_capture_is_a_no_op() -> None:
    assert not capturing()
    record_outcome(PIIDetected("blocked"))  # nothing to record into; must not raise
    assert not capturing()


def test_a_capture_records_the_last_outcome_and_closes_on_exit() -> None:
    first, second = PIIDetected("blocked"), GatewayUnavailable("down")
    with capture_refusals() as capture:
        assert capturing()
        record_outcome(first)
        record_outcome(second)
        assert capture.error is second
        record_outcome(None)  # a success clears an earlier failure
        assert capture.error is None
        record_outcome(first)
    assert not capturing()
    record_outcome(second)  # after the call: not recorded
    assert capture.error is first


def test_captures_nest_and_the_innermost_records() -> None:
    error = PIIDetected("blocked")
    with capture_refusals() as outer:
        with capture_refusals() as inner:
            record_outcome(error)
        assert capturing()
    assert (outer.error, inner.error) == (None, error)


def test_a_capture_is_shared_with_tasks_started_inside_it() -> None:
    error = PIIDetected("blocked")

    async def send() -> None:
        record_outcome(error)

    async def call() -> None:
        # The transport may send from a task of its own; the task's copied
        # context still points at the same capture.
        await asyncio.create_task(send())

    with capture_refusals() as capture:
        asyncio.run(call())
    assert capture.error is error


# --- tagging and the bridge -----------------------------------------------------


class LiteLLMLikeError(Exception):
    """Shaped like LiteLLM's re-raise: a status code, no response, no cause."""

    status_code = 403


def test_a_tagged_error_translates_to_its_recorded_refusal() -> None:
    typed = PIIDetected("blocked")
    error = LiteLLMLikeError("403 blocked")
    with capture_refusals() as capture:
        record_outcome(typed)
        capture.tag(error)
    assert translate(error) is typed
    resolved = TypedRefusals().resolve(error)
    assert resolved is typed
    assert typed.framework_error is error


def test_an_untagged_error_still_passes_through() -> None:
    assert translate(LiteLLMLikeError("403 blocked")) is None
    assert TypedRefusals().resolve(LiteLLMLikeError("403 blocked")) is None


def test_tag_does_nothing_when_the_call_succeeded() -> None:
    error = LiteLLMLikeError("a bug in a callback")
    with capture_refusals() as capture:
        record_outcome(PIIDetected("blocked"))
        record_outcome(None)
        capture.tag(error)
    assert translate(error) is None


def test_a_tag_on_a_chained_link_is_found_by_the_walk() -> None:
    typed = GatewayUnavailable("down")
    inner = LiteLLMLikeError("500")
    capture = RefusalCapture()
    capture.record(typed)
    capture.tag(inner)

    class Wrapper(Exception):
        request = object()  # an HTTP-shaped link, so the walk steps through it

    outer = Wrapper("wrapped")
    outer.__cause__ = inner
    assert translate(outer) is typed


def test_tag_never_raises_on_an_exception_that_refuses_attributes() -> None:
    class Frozen(Exception):
        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError(name)

    capture = RefusalCapture()
    capture.record(PIIDetected("blocked"))
    error = Frozen("x")
    capture.tag(error)
    assert translate(error) is None


# --- the transport records, on both clients -------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_both_clients_record_a_refusal_response(kind: str) -> None:
    with capture_refusals() as capture:
        _send_all(kind, [_pii])
    assert type(capture.error) is PIIDetected


@pytest.mark.parametrize("kind", KINDS)
def test_both_clients_record_a_success_after_a_refusal_as_none(kind: str) -> None:
    with capture_refusals() as capture:
        _send_all(kind, [_pii, _ok])
    assert capture.error is None


@pytest.mark.parametrize("kind", KINDS)
def test_both_clients_record_an_unreachable_gateway(kind: str) -> None:
    with capture_refusals() as capture:
        _send_all(kind, [_unreachable])
    assert type(capture.error) is GatewayUnavailable


@pytest.mark.parametrize("kind", KINDS)
def test_both_clients_record_a_raised_substitution(kind: str) -> None:
    cfg = dataclasses.replace(_CFG, on_model_substitution="raise")
    with capture_refusals() as capture:
        _send_all(kind, [_substituted], cfg=cfg)
    assert type(capture.error) is ModelSubstituted


@pytest.mark.parametrize("kind", KINDS)
def test_both_clients_record_a_send_on_a_closed_client(kind: str) -> None:
    transport = httpx.MockTransport(_ok)
    with capture_refusals() as capture:
        if kind == "sync":
            client = DonkeyClient(_CFG, transport=transport)
            client.close()
            with pytest.raises(ConfigError):
                client.post(_URL, json={"model": "gpt-4o"})
        else:

            async def run() -> None:
                client = DonkeyAsyncClient(_CFG, None, transport=transport)
                await client.aclose()
                with pytest.raises(ConfigError):
                    await client.post(_URL, json={"model": "gpt-4o"})

            asyncio.run(run())
    assert type(capture.error) is ConfigError


@pytest.mark.parametrize("kind", KINDS)
def test_a_failing_classification_never_breaks_the_send(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(response: object) -> DonkeyError:
        raise ValueError("broken")

    monkeypatch.setattr(pipeline, "classify", broken)
    earlier = GatewayUnavailable("down")
    with capture_refusals() as capture:
        record_outcome(earlier)
        _send_all(kind, [_pii])  # the 403 is still returned to the caller
    assert capture.error is earlier


# --- the ADK llm_client subclass ------------------------------------------------


class _Base:
    """Stands in for ADK's ``LiteLLMClient``: one send through the transport's
    recording, then LiteLLM's own error."""

    def __init__(self, outcome: DonkeyError | None, error: Exception | None) -> None:
        self.outcome, self.error = outcome, error

    async def acompletion(self, *args: Any, **kwargs: Any) -> str:
        record_outcome(self.outcome)
        if self.error is not None:
            raise self.error
        return "response"


def test_the_adk_client_tags_litellms_error_and_reraises_it_unchanged() -> None:
    governed = _governed_llm_client_class(_Base)
    typed, error = PIIDetected("blocked"), LiteLLMLikeError("403 blocked")
    with pytest.raises(LiteLLMLikeError) as excinfo:
        asyncio.run(governed(typed, error).acompletion(model="openai/gpt-4o", messages=[]))
    assert excinfo.value is error
    assert translate(error) is typed
    assert not capturing()


def test_the_adk_client_leaves_a_success_and_an_unrelated_error_alone() -> None:
    governed = _governed_llm_client_class(_Base)
    assert asyncio.run(governed(None, None).acompletion()) == "response"
    error = LiteLLMLikeError("not a refusal")
    with pytest.raises(LiteLLMLikeError):
        asyncio.run(governed(None, error).acompletion())
    assert translate(error) is None


def test_the_adk_client_class_is_built_once_per_base() -> None:
    assert _governed_llm_client_class(_Base) is _governed_llm_client_class(_Base)
    assert issubclass(_governed_llm_client_class(_Base), _Base)
