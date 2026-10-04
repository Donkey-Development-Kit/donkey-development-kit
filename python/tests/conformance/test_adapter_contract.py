"""The adapter contract suite: every factory of every adapter in ``ADAPTERS``,
driven through its real framework against one loopback proxy (#742).

Each check runs once per :data:`contract_drivers.DRIVERS` entry and asserts the
same contract the integration checklist in ``CONTRIBUTING.md`` states:

* one call sends the governed consumer-auth headers and the run's correlation id
  (unless ``KNOWN_LIMITATIONS`` records why it cannot);
* a policy refusal is sent once and reaches the caller as the typed error;
* the streamed and sync calls work, or the driver states why the framework has
  none;
* a call still works after the framework closes the client it was handed;
* ``donkey.last_call`` after a call matches ``Adapter.observes_last_call``;
* an example that exposes ``build()`` passes the public conformance kit.

That every factory has a driver is checked in ``tests/unit/test_integration_checklist.py``.

The one-send-on-429 and curated missing-framework checks are parametrised from
``ADAPTERS`` in ``tests/unit/test_framework_retries.py`` and
``tests/unit/test_missing_framework_error.py``; CI runs all three per framework
(``adapter-contract`` in ``.github/workflows/ci.yml``).

A driver skips when its framework is not installed, except in the CI leg for
that framework: ``DONKEY_CONTRACT_EXTRA=<extra>`` turns the skip into a failure,
so a broken install cannot pass as a skip.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TypeGuard

import httpx
import pytest

# Sibling modules: tests/conformance/ is not a package (see test_transport_exemptions).
from contract_drivers import DRIVERS, Driver
from contract_gateway import REQUEST_ID, Gateway
from suite import KNOWN_LIMITATIONS

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core import _verify, lastcall
from donkey_kit.core.errors import DonkeyError, PIIDetected, classify
from donkey_kit.core.lastcall import LastCallStatus
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter

_RUN_ID = "run-contract"
_CORRELATION_HEADER = "x-correlation-id"
_EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

@pytest.fixture(scope="module")
def gateway() -> Iterator[Gateway]:
    gw = Gateway()
    gw.start()
    try:
        yield gw
    finally:
        gw.stop()


@pytest.fixture(autouse=True)
def _reset_last_call() -> Iterator[None]:
    # A record left by an earlier test would read as OBSERVED here.
    token = lastcall._last_call.set(None)
    try:
        yield
    finally:
        lastcall._last_call.reset(token)


def _require(driver: Driver) -> None:
    if os.environ.get("DONKEY_CONTRACT_EXTRA") == ADAPTERS[driver.adapter].extra:
        importlib.import_module(driver.probe)  # this framework's CI leg: never skip
    else:
        pytest.importorskip(driver.probe)


def _donkey(gateway: Gateway) -> Donkey:
    return Donkey(
        DonkeyConfig(
            llm_proxy_url=gateway.url,
            llm_proxy_client_id="contract-cid",
            llm_proxy_client_secret="contract-secret",
            correlation_header=_CORRELATION_HEADER,
        )
    )


def _adapter_class(attr: str) -> type[Adapter]:
    spec = ADAPTERS[attr]
    module = importlib.import_module(spec.module, package="donkey_kit.integrations")
    cls: type[Adapter] = getattr(module, spec.cls)
    return cls


def _is_http_response(obj: object) -> TypeGuard[httpx.Response]:
    # httpx2 is a fork with its own classes (anthropic>=1 raises with one), and
    # classify() reads either, so match the class by stack name.
    cls = type(obj)
    return cls.__name__ == "Response" and cls.__module__.split(".")[0] in {"httpx", "httpx2"}


def _typed(exc: BaseException) -> DonkeyError | None:
    """The typed error a refusal carries: ``exc`` itself, or ``classify()`` of the
    gateway's response. That is the deepest response on the cause chain: a
    wrapper may carry one of its own (LiteLLM's ``OpenAIError`` makes one up)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    response: httpx.Response | None = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, DonkeyError):
            return current
        candidate = getattr(current, "response", None)
        if _is_http_response(candidate) and candidate.status_code >= 400:
            response = candidate
        current = current.__cause__ or current.__context__
    return classify(response) if response is not None else None


def _exempt(driver: Driver, scenario: str) -> bool:
    return scenario in KNOWN_LIMITATIONS.get(driver.adapter, {})


_IDS = list(DRIVERS)


@pytest.fixture(params=_IDS)
def driver(request: pytest.FixtureRequest, gateway: Gateway) -> Driver:
    d = DRIVERS[request.param]
    _require(d)
    gateway.reset()
    return d


async def test_call_sends_governed_headers_and_correlation(
    driver: Driver, gateway: Gateway
) -> None:
    async with _donkey(gateway) as donkey, donkey.run(id=_RUN_ID):
        await driver.call(donkey)
    assert len(gateway.requests) == 1
    sent = gateway.requests[0].headers
    assert sent[_verify.LLM_PROXY_CLIENT_ID_HEADER.lower()] == "contract-cid"
    assert sent[_verify.LLM_PROXY_CLIENT_SECRET_HEADER.lower()] == "contract-secret"
    if _exempt(driver, "correlation_id_propagated"):
        assert sent.get(_CORRELATION_HEADER) != _RUN_ID
    else:
        assert sent.get(_CORRELATION_HEADER) == _RUN_ID


async def test_refusal_is_sent_once_and_typed(driver: Driver, gateway: Gateway) -> None:
    gateway.reset("pii")
    async with _donkey(gateway) as donkey, donkey.run(id=_RUN_ID):
        with pytest.raises(Exception) as exc_info:  # the type is the assertion below
            await driver.call(donkey)
    assert len(gateway.requests) == 1, f"{len(gateway.requests)} sends for a 403"
    if driver.typed == "raised":
        assert isinstance(exc_info.value, PIIDetected), repr(exc_info.value)
        typed: DonkeyError | None = exc_info.value
    else:
        typed = _typed(exc_info.value)
        assert isinstance(typed, PIIDetected), repr(exc_info.value)
    if driver.typed == "raised" and not _exempt(driver, "correlation_id_propagated"):
        assert typed is not None and typed.correlation_id == _RUN_ID


async def test_streamed_call(driver: Driver, gateway: Gateway) -> None:
    if driver.stream is None:
        assert driver.no_stream, "a driver without a streamed call must say why"
        return
    async with _donkey(gateway) as donkey:
        await driver.stream(donkey)
    assert len(gateway.requests) == 1


async def test_sync_call(driver: Driver, gateway: Gateway) -> None:
    if driver.sync is None:
        assert driver.no_sync, "a driver without a sync call must say why"
        return
    async with _donkey(gateway) as donkey:
        driver.sync(donkey)
    assert len(gateway.requests) == 1


async def test_call_survives_the_framework_closing_its_client(
    driver: Driver, gateway: Gateway
) -> None:
    # A framework that closes the client it was handed closes a view; the shared
    # client, and the next object built on it, keep working (#733).
    async with _donkey(gateway) as donkey:
        await driver.call(donkey)
        adapter = getattr(donkey, driver.adapter)
        await adapter.http_client().aclose()
        adapter.sync_http_client().close()
        await driver.call(donkey)
    assert len(gateway.requests) == 2


async def test_last_call_matches_the_declared_metadata(driver: Driver, gateway: Gateway) -> None:
    async with _donkey(gateway) as donkey:
        await driver.call(donkey)
        record = donkey.last_call
    if _adapter_class(driver.adapter).observes_last_call:
        assert record.status is LastCallStatus.OBSERVED
        assert record.request_id == REQUEST_ID
    else:
        assert record.status is LastCallStatus.UNAVAILABLE
        assert record.surface == driver.adapter


@pytest.mark.parametrize("attr", list(ADAPTERS))
async def test_example_build_passes_the_conformance_kit(attr: str) -> None:
    example = _EXAMPLES / attr / "main.py"
    has_build = "\ndef build(" in example.read_text()
    assert has_build, f"examples/{attr}/main.py must expose build(donkey)"
    spec = ADAPTERS[attr]
    if os.environ.get("DONKEY_CONTRACT_EXTRA") != spec.extra:
        for module in spec.probe:
            pytest.importorskip(module)
    from donkey_kit.conformance import run_conformance

    module = importlib.import_module(f"examples.{attr}.main")
    # The example's own asserted exemptions, the way the pytest plugin reads them.
    # Only an adapter the internal matrix records as structurally limited may have
    # any, so an example cannot excuse a scenario it merely fails.
    known = getattr(module, "KNOWN_LIMITATIONS", None)
    assert bool(known) == (attr in KNOWN_LIMITATIONS), (
        f"examples/{attr} KNOWN_LIMITATIONS must match the internal matrix's entry for "
        f"{attr!r} in tests/conformance/suite.py"
    )
    results = await run_conformance(module.build, known_limitations=known)
    failed = [r for r in results if r.status == "fail"]
    assert failed == [], failed
    assert {r.scenario for r in results if r.status == "exempt"} == set(known or {})

