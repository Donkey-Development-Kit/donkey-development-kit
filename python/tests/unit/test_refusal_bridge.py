"""The framework-agnostic typed-refusal bridge (#724, ADR 0002).

``core.refusals.translate`` recovers the typed error from whatever a framework
raised, by duck type and without importing a framework, so these tests build
SDK-shaped errors by hand: an ``APIConnectionError``-like wrapper with the typed
error on ``__cause__``, and an ``APIStatusError``-like error carrying the
response. Base-only: nothing here needs a framework extra.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import httpx
import pytest

import donkey_kit
from donkey_kit import (
    Donkey,
    DonkeyConfig,
    DonkeyError,
    GatewayUnavailable,
    PIIDetected,
    TypedRefusals,
    typed_refusals,
)
from donkey_kit.core import _wire
from donkey_kit.core.errors import ModelSubstituted
from donkey_kit.core.refusals import translate
from donkey_kit.core.telemetry import run_scope
from donkey_kit.integrations import ADAPTERS, refusal_translators

_PII_BODY = {"error": {"type": "pii_detected", "message": 'blocked: [{"pii_type": "EMAIL"}]'}}


def _request(*, sent_by_transport: bool = True) -> httpx.Request:
    if not sent_by_transport:
        return httpx.Request("POST", "https://proxy/responses")
    return httpx.Request(
        "POST",
        "https://proxy/responses",
        headers={_wire.CALL_ID_HEADER: "call-1", _wire.CORRELATION_ID_HEADER: "run-1"},
        extensions={
            "donkey_call_id_header": _wire.CALL_ID_HEADER,
            "donkey_correlation_header": _wire.CORRELATION_ID_HEADER,
        },
    )


class StatusError(Exception):
    """Shaped like ``openai.APIStatusError``: carries the response and request."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__("Error code: 403 - blocked: EMAIL")
        self.response = response
        self.request = response.request


class ConnectionErr(Exception):
    """Shaped like ``openai.APIConnectionError``: a request, no response."""

    def __init__(self, request: httpx.Request) -> None:
        super().__init__("Connection error.")
        self.request = request


class FrameworkWrapper(Exception):
    """Shaped like LangChain's subclass re-raise: a status error's own attrs."""


def _status_error(*, sent_by_transport: bool = True) -> StatusError:
    return StatusError(
        httpx.Response(403, json=_PII_BODY, request=_request(sent_by_transport=sent_by_transport))
    )


def _connection_error_from(typed: DonkeyError) -> ConnectionErr:
    """An ``APIConnectionError`` the SDK raised ``from`` the transport's typed error."""
    try:
        try:
            raise typed
        except DonkeyError as inner:
            raise ConnectionErr(_request()) from inner
    except ConnectionErr as outer:
        return outer


def _unavailable() -> GatewayUnavailable:
    try:
        try:
            raise httpx.ConnectError("refused")
        except httpx.ConnectError as exc:
            raise GatewayUnavailable("unreachable", base_url="https://proxy", cause=exc) from exc
    except GatewayUnavailable as typed:
        return typed


# --- translate(): the chain walk ---------------------------------------------


def test_a_donkey_error_translates_to_itself() -> None:
    err = PIIDetected("blocked")
    assert translate(err) is err


def test_unwraps_the_typed_error_off_an_sdk_connection_error() -> None:
    typed = _unavailable()
    assert translate(_connection_error_from(typed)) is typed


def test_unwraps_a_multi_link_cause_chain() -> None:
    typed = ModelSubstituted("served another model", requested_model="a", served_model="b")
    inner = _connection_error_from(typed)
    try:
        raise ConnectionErr(_request()) from inner  # LangChain's re-wrap, one more link
    except ConnectionErr as outer:
        assert translate(outer) is typed


def test_follows_an_implicit_context_link() -> None:
    typed = _unavailable()
    try:
        try:
            raise typed
        except GatewayUnavailable:
            raise ConnectionErr(_request())  # noqa: B904 — implicit __context__ on purpose
    except ConnectionErr as outer:
        assert translate(outer) is typed


def test_classifies_a_status_error_the_transport_sent() -> None:
    typed = translate(_status_error())
    assert isinstance(typed, PIIDetected)
    assert typed.call_id == "call-1"
    assert typed.correlation_id == "run-1"


def test_classifies_a_status_error_found_down_the_chain() -> None:
    try:
        raise FrameworkWrapper("wrapped") from _status_error()
    except FrameworkWrapper:
        pass
    status = _status_error()
    outer = ConnectionErr(_request())
    outer.__cause__ = status
    assert isinstance(translate(outer), PIIDetected)


def test_a_status_error_the_transport_did_not_send_is_not_a_refusal() -> None:
    # A stock client's 403, or the app's own call to some other API, is not a
    # governed refusal: the bridge must never turn it into PIIDetected.
    assert translate(_status_error(sent_by_transport=False)) is None


def test_an_httpx_status_error_from_another_call_passes() -> None:
    response = httpx.Response(404, request=_request(sent_by_transport=False))
    err = httpx.HTTPStatusError("not found", request=response.request, response=response)
    assert translate(err) is None


@pytest.mark.parametrize("exc", [ValueError("boom"), KeyError("k"), RuntimeError("x")])
def test_a_non_donkey_error_translates_to_none(exc: Exception) -> None:
    assert translate(exc) is None


def test_the_users_own_rewrap_of_a_refusal_is_left_alone() -> None:
    # ``raise HTTPException(...) from exc`` in user code is the user's decision;
    # the walk stops at a link that is not an HTTP SDK error.
    class HTTPException(Exception):
        pass

    try:
        raise HTTPException("upstream refused") from _connection_error_from(_unavailable())
    except HTTPException as user_err:
        assert translate(user_err) is None


def test_a_bug_while_handling_a_refusal_is_left_alone() -> None:
    try:
        try:
            raise _connection_error_from(_unavailable())
        except ConnectionErr:
            {}["missing"]
    except KeyError as bug:
        assert translate(bug) is None


def test_a_cyclic_chain_terminates() -> None:
    a = ConnectionErr(_request())
    b = ConnectionErr(_request())
    a.__cause__, b.__cause__ = b, a
    assert translate(a) is None


def test_an_httpx_error_without_a_request_does_not_raise() -> None:
    # httpx raises RuntimeError reading ``.request`` on an error built without one.
    assert translate(httpx.ConnectError("refused")) is None


def test_translators_are_tried_on_each_link_before_the_built_in_rules() -> None:
    class Wrapper(Exception):
        pass

    typed = _unavailable()
    seen: list[BaseException] = []

    def unwrap(exc: BaseException) -> DonkeyError | None:
        seen.append(exc)
        if isinstance(exc, Wrapper) and exc.__cause__ is not None:
            return translate(exc.__cause__)
        return None

    try:
        raise Wrapper("throttled") from _connection_error_from(typed)
    except Wrapper as wrapped:
        assert translate(wrapped) is None  # a non-HTTP wrapper stops the walk
        assert translate(wrapped, (unwrap,)) is typed
        assert seen[0] is wrapped


# --- TypedRefusals: context manager and decorator -----------------------------


def test_sync_context_manager_raises_the_typed_error() -> None:
    err = _status_error()
    with pytest.raises(PIIDetected) as excinfo, typed_refusals():
        raise err
    assert excinfo.value.framework_error is err
    # A classified refusal is not chained: the framework error's message can
    # repeat the blocked values, and a traceback renders every chained exception.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_a_transport_raised_typed_error_keeps_its_own_cause() -> None:
    typed = _unavailable()
    wrapper = _connection_error_from(typed)
    with pytest.raises(GatewayUnavailable) as excinfo, typed_refusals():
        raise wrapper
    assert excinfo.value is typed
    assert isinstance(excinfo.value.__cause__, httpx.ConnectError)
    assert excinfo.value.framework_error is wrapper


async def test_async_context_manager_raises_the_typed_error() -> None:
    with pytest.raises(PIIDetected):
        async with typed_refusals():
            raise _status_error()


def test_sync_decorator() -> None:
    @typed_refusals()
    def ask(question: str) -> str:
        raise _status_error()

    with pytest.raises(PIIDetected):
        ask("hi")
    assert ask.__name__ == "ask"


async def test_async_decorator() -> None:
    @typed_refusals()
    async def ask(question: str) -> str:
        raise _connection_error_from(_unavailable())

    with pytest.raises(GatewayUnavailable):
        await ask("hi")


def test_a_non_donkey_error_propagates_unchanged() -> None:
    err = ValueError("boom")
    with pytest.raises(ValueError) as excinfo, typed_refusals():
        raise err
    assert excinfo.value is err


def test_a_donkey_error_raised_directly_propagates_unchanged() -> None:
    err = PIIDetected("blocked")
    with pytest.raises(PIIDetected) as excinfo, typed_refusals():
        raise err
    assert excinfo.value is err
    assert err.framework_error is None


def test_a_base_exception_is_never_translated() -> None:
    class Interrupt(KeyboardInterrupt):
        pass

    with pytest.raises(Interrupt), typed_refusals():
        raise Interrupt


def test_a_clean_block_returns_normally() -> None:
    with typed_refusals():
        value = 1
    assert value == 1


def test_one_bridge_is_reusable_and_nests() -> None:
    bridge = typed_refusals()
    for _ in range(2):
        with pytest.raises(PIIDetected), bridge, bridge:
            raise _status_error()


def test_typed_refusals_is_public() -> None:
    assert "typed_refusals" in donkey_kit.__all__
    assert "TypedRefusals" in donkey_kit.__all__
    assert isinstance(typed_refusals(), TypedRefusals)


# --- per-adapter translators (AdapterSpec.refusal_translator) -----------------


def test_a_translator_is_consulted_only_once_its_framework_is_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Wrapper(Exception):
        pass

    module = types.ModuleType("donkey_kit.integrations._fake_framework_adapter")

    def unwrap(exc: BaseException) -> DonkeyError | None:
        if isinstance(exc, Wrapper) and exc.__cause__ is not None:
            return translate(exc.__cause__)
        return None

    module.unwrap = unwrap  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = ADAPTERS["strands"]
    fake = type(spec)(
        "fake", "._fake_framework_adapter", "Fake", "fake",
        conformance_tested=False, probe=("_fake_framework",), refusal_translator="unwrap",
    )
    monkeypatch.setitem(ADAPTERS, "fake", fake)

    def raise_wrapped() -> None:
        raise Wrapper("throttled") from _connection_error_from(_unavailable())

    # The framework is not imported: no translator, and the wrapper passes through.
    monkeypatch.delitem(sys.modules, "_fake_framework", raising=False)
    assert unwrap not in refusal_translators()
    with pytest.raises(Wrapper), typed_refusals():
        raise_wrapped()

    # Once it is (the bridge resolves translators at exit), the wrapper is seen through.
    bridge = typed_refusals()
    monkeypatch.setitem(sys.modules, "_fake_framework", types.ModuleType("_fake_framework"))
    assert unwrap in refusal_translators()
    with pytest.raises(GatewayUnavailable), bridge:
        raise_wrapped()


def test_no_adapter_module_classifies_on_its_own() -> None:
    # ADR 0002: classification lives in core. A translator only unwraps.
    import importlib
    import inspect

    for spec in ADAPTERS.values():
        module = importlib.import_module(spec.module, "donkey_kit.integrations")
        source = inspect.getsource(module)
        assert "classify(" not in source, spec.module


def test_every_adapter_exposes_the_shared_bridge() -> None:
    import importlib

    for spec in ADAPTERS.values():
        module = importlib.import_module(spec.module, "donkey_kit.integrations")
        assert isinstance(getattr(module, spec.cls).typed_refusals(), TypedRefusals)


# --- donkey.run() / @governed apply the bridge on exit ------------------------


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy", llm_proxy_client_id="cid", llm_proxy_client_secret="cs"
    )


def test_run_types_a_refusal_leaving_the_block() -> None:
    with pytest.raises(PIIDetected), Donkey(_cfg()).run():
        raise _status_error()


async def test_async_run_types_a_refusal_leaving_the_block() -> None:
    with pytest.raises(GatewayUnavailable):
        async with Donkey(_cfg()).run(id="ticket-1"):
            raise _connection_error_from(_unavailable())


def test_run_unbinds_before_raising() -> None:
    from donkey_kit.core.telemetry import current_correlation_id

    with pytest.raises(PIIDetected), Donkey(_cfg()).run(id="ticket-1"):
        assert current_correlation_id() == "ticket-1"
        raise _status_error()
    assert current_correlation_id() is None


def test_run_with_typed_refusals_off_passes_the_framework_error() -> None:
    err = _status_error()
    with pytest.raises(StatusError) as excinfo, Donkey(_cfg()).run(typed_refusals=False):
        raise err
    assert excinfo.value is err


def test_run_passes_a_non_refusal_unchanged() -> None:
    with pytest.raises(ValueError, match="boom"), Donkey(_cfg()).run():
        raise ValueError("boom")


def test_governed_sync_and_async() -> None:
    donkey = Donkey(_cfg())

    @donkey.governed(team="support")
    def sync_handler() -> None:
        raise _status_error()

    @donkey.governed(typed_refusals=False)
    def untyped_handler() -> None:
        raise _status_error()

    with pytest.raises(PIIDetected):
        sync_handler()
    with pytest.raises(StatusError):
        untyped_handler()


async def test_governed_async() -> None:
    donkey = Donkey(_cfg())

    @donkey.governed
    async def handler() -> Any:
        raise _connection_error_from(_unavailable())

    with pytest.raises(GatewayUnavailable):
        await handler()


def test_a_bare_run_scope_does_not_translate() -> None:
    # The core building block stays opt-in: only donkey.run() passes the bridge.
    err = _status_error()
    with pytest.raises(StatusError), run_scope("r"):
        raise err


def test_strands_wrappers_are_seen_through() -> None:
    """Strands raises ``ModelThrottledException(str(error)) from error`` and
    ``EventLoopException(e, state) from e``: neither carries a request or a
    response, so its adapter's translator hands the cause back to core."""
    exceptions = pytest.importorskip("strands.types.exceptions")
    try:
        try:
            raise exceptions.ModelThrottledException("throttled") from _status_error()
        except exceptions.ModelThrottledException as throttled:
            raise exceptions.EventLoopException(throttled) from throttled
    except exceptions.EventLoopException as wrapped:
        assert translate(wrapped) is None  # core alone stops at the wrapper
        with pytest.raises(PIIDetected), typed_refusals():
            raise wrapped
    typed = _unavailable()
    with pytest.raises(GatewayUnavailable), typed_refusals():
        raise exceptions.EventLoopException(ValueError()) from _connection_error_from(typed)
