"""Logging convention (#717, CONTRIBUTING §3): DEBUG records for the retry loop
and the 401 refresh, a NullHandler on the package logger, and no request header
value (bearer token, client secret) in any record. httpx.MockTransport only."""

from __future__ import annotations

import logging

import httpx
import pytest

import donkey_kit
from donkey_kit.core.auth import ChainedAuth, StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.lastcall import ROUTING_FALLBACK_HEADER
from donkey_kit.core.transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    async_client,
    proxy_auth_headers,
    sync_client,
)

_TRANSPORT_LOGGER = "donkey_kit.core.transport"
_CLIENT_ID = "cid-logging-test-7f3a"
_CLIENT_SECRET = "csecret-logging-test-9b1e"
_BEARER = "bearer-logging-test-4c2d"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries sleep for the real backoff; the delay is still logged as computed."""

    async def _instant(_delay: float) -> None:
        return None

    monkeypatch.setattr(async_client.asyncio, "sleep", _instant)
    monkeypatch.setattr(sync_client.time, "sleep", lambda _delay: None)


def _debug(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]


def test_package_logger_has_a_null_handler() -> None:
    handlers = logging.getLogger("donkey_kit").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)
    assert donkey_kit.__name__ == "donkey_kit"
    assert not hasattr(donkey_kit, "logging")  # the stdlib module is not re-exported


def test_tools_logger_is_named_after_its_module() -> None:
    from donkey_kit.tools import session

    assert session._log.name == "donkey_kit.tools.session"


async def test_retried_503_emits_debug_records(caplog: pytest.LogCaptureFixture) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, headers={"retry-after": "2"})
        return httpx.Response(200)

    with caplog.at_level(logging.DEBUG, logger=_TRANSPORT_LOGGER):
        async with DonkeyAsyncClient(
            DonkeyConfig(max_retries=3), None, transport=httpx.MockTransport(handler)
        ) as client:
            resp = await client.get("https://proxy.example/v1/chat?k=v")

    assert resp.status_code == 200
    retries = [m for m in _debug(caplog) if "retry" in m]
    # attempt, status and delay are all in the record; the query string is not.
    assert retries == [
        "GET https://proxy.example/v1/chat returned 503; retry 1 of 3 in 2.00s",
        "GET https://proxy.example/v1/chat returned 503; retry 2 of 3 in 2.00s",
    ]
    # Every record comes from a module of the transport package (#728).
    assert all(r.name.startswith(_TRANSPORT_LOGGER + ".") for r in caplog.records)


def test_sync_retried_503_emits_debug_records(caplog: pytest.LogCaptureFixture) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 2 else httpx.Response(200)

    with caplog.at_level(logging.DEBUG, logger=_TRANSPORT_LOGGER):
        with DonkeyClient(
            DonkeyConfig(max_retries=1), transport=httpx.MockTransport(handler)
        ) as client:
            assert client.get("https://proxy.example/").status_code == 200

    assert any("returned 503; retry 1 of 1" in m for m in _debug(caplog))


async def test_exhausted_retries_and_fallback_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def always_503(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with caplog.at_level(logging.DEBUG, logger=_TRANSPORT_LOGGER):
        async with DonkeyAsyncClient(
            DonkeyConfig(max_retries=1), None, transport=httpx.MockTransport(always_503)
        ) as client:
            assert (await client.get("https://proxy.example/")).status_code == 503

    assert any("giving up after 2 attempt(s)" in m for m in _debug(caplog))

    caplog.clear()

    def fell_back(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={ROUTING_FALLBACK_HEADER: "true"})

    with caplog.at_level(logging.DEBUG, logger=_TRANSPORT_LOGGER):
        async with DonkeyAsyncClient(
            DonkeyConfig(max_retries=3), None, transport=httpx.MockTransport(fell_back)
        ) as client:
            assert (await client.get("https://proxy.example/")).status_code == 503

    assert _debug(caplog) == [
        "GET https://proxy.example/ returned 503 after a gateway routing fallback; not retrying"
    ]


async def test_401_refresh_emits_debug_record(caplog: pytest.LogCaptureFixture) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401) if calls["n"] == 1 else httpx.Response(200)

    with caplog.at_level(logging.DEBUG, logger=_TRANSPORT_LOGGER):
        async with DonkeyAsyncClient(
            DonkeyConfig(), StaticToken("t"), transport=httpx.MockTransport(handler)
        ) as client:
            resp = await client.get("https://proxy.example/v1/chat")

    assert resp.status_code == 200
    assert (
        "GET https://proxy.example/v1/chat returned 401; refreshing the token and re-sending once"
        in _debug(caplog)
    )


async def test_chained_auth_fallthrough_is_logged_without_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Broken(StaticToken):
        async def token(self) -> str:
            raise RuntimeError(f"vault said {_CLIENT_SECRET}")

    with caplog.at_level(logging.DEBUG, logger="donkey_kit.core.auth"):
        token = await ChainedAuth(_Broken("x"), StaticToken("ok")).token()

    assert token == "ok"
    assert any("_Broken failed with RuntimeError" in m for m in _debug(caplog))
    assert _CLIENT_SECRET not in caplog.text


async def test_no_log_record_contains_a_request_header_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drive retry, refresh and exhaustion with real credentials on the wire, then
    check every record (message, args, traceback) for each sent header value."""
    sent: list[httpx.Headers] = []
    statuses = iter([401, 503, 502, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.headers)
        return httpx.Response(next(statuses))

    cfg = DonkeyConfig(
        llm_proxy_url="https://proxy.example/",
        llm_proxy_client_id=_CLIENT_ID,
        llm_proxy_client_secret=_CLIENT_SECRET,
        max_retries=3,
        # A 502 on a model POST retries only when opted in (#728).
        retry_model_calls_on_gateway_errors=True,
    )
    with caplog.at_level(logging.DEBUG, logger="donkey_kit"):
        async with DonkeyAsyncClient(
            cfg, StaticToken(_BEARER), transport=httpx.MockTransport(handler)
        ) as client:
            # The consumer-auth pair rides the default_headers snapshot, exactly
            # as a native OpenAI client built from connection_kwargs() sends it.
            resp = await client.post(
                "https://proxy.example/chat/completions",
                json={"model": "gpt-4o"},
                headers=dict(proxy_auth_headers(cfg)),
            )

    assert resp.status_code == 200
    # The credentials really were sent, so the absence below is meaningful.
    wire = [v for h in sent for v in h.values()]
    assert _CLIENT_SECRET in wire and _CLIENT_ID in wire
    assert any(_BEARER in v for v in wire)
    assert _debug(caplog), "the scenario must have produced records to check"

    secrets = {_CLIENT_ID, _CLIENT_SECRET, _BEARER}
    for record in caplog.records:
        rendered = logging.Formatter().format(record)
        for value in secrets:
            assert value not in rendered, (record.name, record.getMessage())
            assert all(value not in str(arg) for arg in (record.args or ()))


def test_otlp_bootstrap_inert_path_logs_debug(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donkey_kit.core import telemetry

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    with caplog.at_level(logging.DEBUG, logger="donkey_kit.core.telemetry"):
        telemetry.configure_otlp_export(DonkeyConfig())

    assert any("OTLP export not installed" in m for m in _debug(caplog))
    # DEBUG only: "inert and silent" (BG §1.6) means no WARNING or louder.
    assert all(r.levelno == logging.DEBUG for r in caplog.records)

