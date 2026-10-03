"""One header / retry / refusal suite, run against both governed clients (#728).

``DonkeyAsyncClient`` and ``DonkeyClient`` share the sans-IO halves of a send
(``core/transport/policy.py``, ``headers.py``, ``pipeline.py``) and differ only
in IO. Every case here is parametrised over both, so a rule cannot drift on one
of them. ``httpx.MockTransport`` only; the async client runs under
``asyncio.run`` so each case is one plain function for both clients.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.core import _wire
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ModelSubstituted, PolicyViolation, TokenBudgetExceeded
from donkey_kit.core.telemetry import run_scope
from donkey_kit.core.transport import (
    DonkeyAsyncClient,
    DonkeyClient,
    GovernedSyncTransport,
    GovernedTransport,
    async_client,
    sync_client,
)
from donkey_kit.core.transport.policy import Finish, Retry, decide_retry

_URL = "https://proxy.example/chat/completions"
_CORRELATION = "x-correlation-id"
_CALL_ID = "x-donkey-request-id"
_CFG = DonkeyConfig(
    llm_proxy_url="https://proxy.example/",
    llm_proxy_client_id="cid-both",
    llm_proxy_client_secret="csecret-both",
    correlation_header=_CORRELATION,
    call_id_header=_CALL_ID,
    max_retries=3,
)
_Handler = Callable[[httpx.Request], httpx.Response]

KINDS = ["async", "sync"]


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant(_delay: float) -> None:
        return None

    monkeypatch.setattr(async_client.asyncio, "sleep", _instant)
    monkeypatch.setattr(sync_client.time, "sleep", lambda _delay: None)


class _Async(DonkeyAsyncClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.refusals: list[PolicyViolation] = []

    async def _on_refusal(self, violation: PolicyViolation) -> None:
        self.refusals.append(violation)


class _Sync(DonkeyClient):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.refusals: list[PolicyViolation] = []

    def _on_refusal(self, violation: PolicyViolation) -> None:
        self.refusals.append(violation)


def _send(
    kind: str,
    handler: _Handler,
    *,
    cfg: DonkeyConfig = _CFG,
    method: str = "POST",
    json: object = None,
) -> tuple[httpx.Response, list[PolicyViolation]]:
    """Send one request through a fresh client of ``kind``; return the response
    and the violations its ``_on_refusal`` saw."""
    body = {"model": "gpt-4o"} if json is None else json
    transport = httpx.MockTransport(handler)
    if kind == "sync":
        with _Sync(cfg, transport=transport) as client:
            response = client.request(method, _URL, json=body if method == "POST" else None)
            return response, client.refusals

    async def run() -> tuple[httpx.Response, list[PolicyViolation]]:
        async with _Async(cfg, None, transport=transport) as client:
            response = await client.request(
                method, _URL, json=body if method == "POST" else None
            )
            return response, client.refusals

    return asyncio.run(run())


def _counting(statuses: list[int]) -> tuple[_Handler, list[httpx.Request]]:
    sent: list[httpx.Request] = []
    replies = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(next(replies))

    return handler, sent


# --- headers ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_both_clients_stamp_the_same_governed_headers(kind: str) -> None:
    handler, sent = _counting([200])
    with run_scope("run-1"):
        _send(kind, handler)
    headers = sent[0].headers
    assert headers[_CORRELATION] == "run-1"
    assert headers[_CALL_ID] != headers[_CORRELATION]


@pytest.mark.parametrize("kind", KINDS)
def test_the_call_id_is_stable_across_retries(kind: str) -> None:
    handler, sent = _counting([503, 503, 200])
    response, _ = _send(kind, handler)
    assert response.status_code == 200
    assert len({r.headers[_CALL_ID] for r in sent}) == 1
    assert len({r.headers[_CORRELATION] for r in sent}) == 1


# --- retry -----------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_503_is_retried_on_both_clients(kind: str) -> None:
    handler, sent = _counting([503, 503, 200])
    response, _ = _send(kind, handler)
    assert response.status_code == 200
    assert len(sent) == 3


@pytest.mark.parametrize("kind", KINDS)
def test_retries_stop_at_max_retries(kind: str) -> None:
    handler, sent = _counting([503] * 10)
    response, _ = _send(kind, handler)
    assert response.status_code == 503
    assert len(sent) == _CFG.max_retries + 1


@pytest.mark.parametrize("status", [502, 504])
@pytest.mark.parametrize("kind", KINDS)
def test_a_gateway_error_on_a_model_post_is_not_resent_by_default(kind: str, status: int) -> None:
    # The upstream call may have completed and billed; no idempotency key is
    # verified (docs/verified-apis.md), so a re-send could bill twice (#728).
    handler, sent = _counting([status, 200])
    response, _ = _send(kind, handler)
    assert response.status_code == status
    assert len(sent) == 1


@pytest.mark.parametrize("status", [502, 504])
@pytest.mark.parametrize("kind", KINDS)
def test_a_gateway_error_on_a_model_post_is_resent_when_opted_in(kind: str, status: int) -> None:
    cfg = dataclasses.replace(_CFG, retry_model_calls_on_gateway_errors=True)
    handler, sent = _counting([status, 200])
    response, _ = _send(kind, handler, cfg=cfg)
    assert response.status_code == 200
    assert len(sent) == 2


@pytest.mark.parametrize(
    ("method", "json"),
    [("GET", None), ("POST", {"input": "no model field"})],
    ids=["get", "post-without-model"],
)
@pytest.mark.parametrize("kind", KINDS)
def test_a_502_on_a_non_model_request_is_still_retried(
    kind: str, method: str, json: object
) -> None:
    handler, sent = _counting([502, 200])
    response, _ = _send(kind, handler, method=method, json=json)
    assert response.status_code == 200
    assert len(sent) == 2


# --- refusal ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_a_budget_refusal_is_sent_once_and_reaches_on_refusal(kind: str) -> None:
    handler, sent = _counting([429, 200])
    response, refusals = _send(kind, handler)
    assert response.status_code == 429
    assert len(sent) == 1
    assert response.headers["x-should-retry"] == "false"
    assert [type(v) for v in refusals] == [TokenBudgetExceeded]


@pytest.mark.parametrize("kind", KINDS)
def test_a_success_never_reaches_on_refusal(kind: str) -> None:
    handler, _ = _counting([200])
    _, refusals = _send(kind, handler)
    assert refusals == []


@pytest.mark.parametrize("kind", KINDS)
def test_a_substitution_raises_on_both_clients_when_opted_in(kind: str) -> None:
    cfg = dataclasses.replace(_CFG, on_model_substitution="raise")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={_wire.LLM_MODEL_HEADER: "gpt-4o-mini"})

    with pytest.raises(ModelSubstituted) as exc:
        _send(kind, handler, cfg=cfg)
    assert (exc.value.requested_model, exc.value.served_model) == ("gpt-4o", "gpt-4o-mini")


# --- the governed transport seam -------------------------------------------------


def test_both_clients_expose_their_governed_transport() -> None:
    inner = httpx.MockTransport(lambda r: httpx.Response(200))
    assert isinstance(DonkeyAsyncClient(_CFG, None, transport=inner).governed_transport,
                      GovernedTransport)
    sync = DonkeyClient(_CFG, transport=inner)
    assert isinstance(sync.governed_transport, GovernedSyncTransport)
    assert sync.governed_transport.inner is inner


@pytest.mark.parametrize("kind", KINDS)
def test_replace_inner_serves_the_next_request(kind: str) -> None:
    first = httpx.MockTransport(lambda r: httpx.Response(500))
    second = httpx.MockTransport(lambda r: httpx.Response(201))
    if kind == "sync":
        with DonkeyClient(_CFG, transport=first) as client:
            client.governed_transport.replace_inner(second)
            assert client.governed_transport.inner is second
            assert client.get(_URL).status_code == 201
        return

    async def run() -> None:
        async with DonkeyAsyncClient(_CFG, None, transport=first) as client:
            client.governed_transport.replace_inner(second)
            assert client.governed_transport.inner is second
            assert (await client.get(_URL)).status_code == 201

    asyncio.run(run())


# --- the sans-IO decision --------------------------------------------------------


def _request(method: str = "POST", body: bytes = b'{"model": "m"}') -> httpx.Request:
    return httpx.Request(method, _URL, content=body if method == "POST" else None)


def test_decide_retry_is_pure() -> None:
    req = _request()
    assert decide_retry(_CFG, req, httpx.Response(200), attempt=0, attempts=4,
                        can_refresh=False) == Finish(None)
    retry = decide_retry(_CFG, req, httpx.Response(503), attempt=0, attempts=4,
                         can_refresh=False)
    assert isinstance(retry, Retry)
    assert decide_retry(_CFG, req, httpx.Response(502), attempt=0, attempts=4,
                        can_refresh=False) == Finish("unsafe")
    assert decide_retry(_CFG, req, httpx.Response(503), attempt=3, attempts=4,
                        can_refresh=False) == Finish("exhausted")


# --- config ----------------------------------------------------------------------


def test_the_gateway_error_retry_flag_defaults_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DONKEY_RETRY_MODEL_CALLS_ON_GATEWAY_ERRORS", raising=False)
    assert DonkeyConfig().retry_model_calls_on_gateway_errors is False
    assert DonkeyConfig.from_env().retry_model_calls_on_gateway_errors is False


def test_the_gateway_error_retry_flag_reads_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DONKEY_RETRY_MODEL_CALLS_ON_GATEWAY_ERRORS", "true")
    assert DonkeyConfig.from_env().retry_model_calls_on_gateway_errors is True
