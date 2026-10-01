"""A typed refusal's traceback and logged form omit the blocked values.

``str(exc)`` of a :class:`PIIDetected` already names only entity types and
offsets. The rendered traceback — ``traceback.format_exception`` and what
``logger.exception`` writes — must not bring the values back through a chained
framework error, whose own message repeats the gateway's rejection text. The
framework error stays reachable on ``.framework_error``.
"""

from __future__ import annotations

import logging
import traceback

import pytest

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import PIIDetected
from donkey_kit.integrations.langgraph import typed_refusals

# The value the captured ``reject.pii-detected`` fixture reports.
_BLOCKED_VALUE = "john.doe@example.com"


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy.example.internal/",
        llm_proxy_client_id="proxy-client-id",
        llm_proxy_client_secret="proxy-client-secret",
    )


async def _refusal_from_raw_client() -> PIIDetected:
    pytest.importorskip("openai")
    donkey = Donkey(_cfg())
    try:
        client = donkey.llm.client()
        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as info:
            with typed_refusals():
                await client.chat.completions.create(
                    model="m", messages=[{"role": "user", "content": "hi"}]
                )
    finally:
        await donkey.aclose()
    return info.value


def _rendered(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


def _logged(exc: BaseException, caplog: pytest.LogCaptureFixture) -> str:
    logger = logging.getLogger("donkey_kit.tests.refusal")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        try:
            raise exc
        except PIIDetected:
            logger.exception("model call refused")
    return caplog.text


async def test_raw_client_refusal_traceback_omits_the_blocked_value() -> None:
    exc = await _refusal_from_raw_client()

    assert _BLOCKED_VALUE not in str(exc)
    assert _BLOCKED_VALUE not in _rendered(exc)


async def test_raw_client_refusal_log_record_omits_the_blocked_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exc = await _refusal_from_raw_client()

    text = _logged(exc, caplog)

    assert "model call refused" in text
    assert "PIIDetected" in text
    assert _BLOCKED_VALUE not in text


async def test_traceback_with_frame_locals_omits_the_blocked_value() -> None:
    """Error reporters that show each frame's local variables (Sentry, ``pytest
    -l``, rich/structlog tracebacks) must not find the framework error there."""
    exc = await _refusal_from_raw_client()

    rendered = "".join(
        traceback.TracebackException.from_exception(exc, capture_locals=True).format()
    )

    assert _BLOCKED_VALUE not in rendered


async def test_the_framework_error_stays_reachable_but_unchained() -> None:
    openai = pytest.importorskip("openai")
    exc = await _refusal_from_raw_client()

    assert isinstance(exc.framework_error, openai.APIStatusError)
    assert exc.__cause__ is None
    assert exc.__suppress_context__ is True


async def test_langchain_node_refusal_traceback_and_log_omit_the_blocked_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("langchain_openai")
    donkey = Donkey(_cfg())
    try:
        model = donkey.langgraph.chat_model("m")
        with donkey.simulate(PIIDetected), pytest.raises(PIIDetected) as info:
            with typed_refusals():
                await model.ainvoke("hi")
    finally:
        await donkey.aclose()

    assert _BLOCKED_VALUE not in _rendered(info.value)
    assert _BLOCKED_VALUE not in _logged(info.value, caplog)
