"""Every adapter meets one contract (#726, ADR 0004).

:class:`~donkey_kit.integrations.AdapterProtocol` is the shape:
``connection_kwargs()`` and a frozen, per-factory
:class:`~donkey_kit.integrations.AdapterCapabilities`. These tests run over the
whole ``ADAPTERS`` roster, so a new adapter is held to the contract on arrival:

- each declared factory is a method on the adapter and a module-level factory
  that delegates to the process-default adapter (the three ergonomic forms);
- every ``connection_kwargs()`` builds on ``Adapter._connection()``, so the
  config validation and the token-mode guards cannot be skipped by one adapter;
- in a token auth mode with no ``AuthProvider``, every adapter refuses with
  ``ConfigError`` before anything is built;
- capabilities are immutable and declared per factory, a concrete adapter
  cannot leave them out, and ``transport``, ``sync`` and ``streaming`` are read
  back from the governed kwargs each factory is built from, so they cannot
  claim wiring the adapter does not have.

Adapters import their framework only inside methods, and ``_connection()`` runs
before any framework import, so none of this needs a framework installed.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
from collections.abc import Iterator
from types import MappingProxyType, ModuleType
from typing import Any

import pytest

from donkey_kit import DonkeyConfig
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import (
    DonkeyAsyncClient,
    DonkeyAsyncClientView,
    DonkeyClient,
    DonkeyClientView,
    build_http_client,
)
from donkey_kit.integrations import ADAPTERS, AdapterCapabilities, AdapterProtocol
from donkey_kit.integrations._base import Adapter

_ROSTER = sorted(ADAPTERS)
_FACTORIES = sorted(
    (attr, factory)
    for attr in ADAPTERS
    for factory in getattr(
        importlib.import_module(ADAPTERS[attr].module, package="donkey_kit.integrations"),
        ADAPTERS[attr].cls,
    ).factories
)


def _module(attr: str) -> ModuleType:
    return importlib.import_module(ADAPTERS[attr].module, package="donkey_kit.integrations")


def _adapter_class(attr: str) -> type[Adapter]:
    cls: type[Adapter] = getattr(_module(attr), ADAPTERS[attr].cls)
    return cls


def _client_id_cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
        max_retries=0,
    )


def _token_cfg(mode: str) -> DonkeyConfig:
    extra = {"llm_proxy_wallet_client_id": "wallet-42"} if mode == "jwt" else {}
    return DonkeyConfig(
        llm_proxy_url="https://proxy/", llm_proxy_auth=mode, max_retries=0, **extra
    )


@pytest.fixture
def http() -> Iterator[DonkeyAsyncClient]:
    # Never sends: every test here stops before a request.
    yield build_http_client(_client_id_cfg(), None)


@pytest.mark.parametrize("attr", _ROSTER)
def test_every_adapter_meets_the_protocol(attr: str, http: DonkeyAsyncClient) -> None:
    adapter = _adapter_class(attr)(_client_id_cfg(), http)
    assert isinstance(adapter, AdapterProtocol)
    assert adapter.capabilities() == next(iter(type(adapter).factories.values()))


@pytest.mark.parametrize(("attr", "factory"), _FACTORIES)
def test_every_factory_has_all_three_forms(
    attr: str, factory: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cls = _adapter_class(attr)
    module = _module(attr)
    # 1. donkey.<framework>.<factory>()
    assert callable(getattr(cls, factory, None)), f"{cls.__name__} has no {factory}()"
    # 2. the module-level factory, delegating to the process-default adapter.
    assert factory in module.__all__
    calls: list[tuple[type[Adapter], tuple[Any, ...], dict[str, Any]]] = []
    built = object()

    class _Default:
        def __init__(self, used: type[Adapter]) -> None:
            self._used = used

        def __getattr__(self, name: str) -> Any:
            assert name == factory

            def _factory(*args: Any, **kw: Any) -> object:
                calls.append((self._used, args, kw))
                return built

            return _factory

    monkeypatch.setattr(module, "default_adapter", _Default)
    fn = getattr(module, factory)
    args = ("m",) if "model" in inspect.signature(fn).parameters else ()
    assert fn(*args) is built
    assert [(used, a) for used, a, _ in calls] == [(cls, args)]
    # 3. connection_kwargs(), the governed values for building it yourself.
    assert callable(cls.connection_kwargs)


class _Routed(Exception):
    pass


@pytest.mark.parametrize("attr", _ROSTER)
def test_every_connection_kwargs_builds_on_connection(
    attr: str, http: DonkeyAsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []

    def _connection(self: Adapter, factory: str | None = None) -> dict[str, Any]:
        seen.append(factory)
        raise _Routed

    cls = _adapter_class(attr)
    monkeypatch.setattr(cls, "_connection", _connection)
    with pytest.raises(_Routed):
        cls(_client_id_cfg(), http).connection_kwargs()
    assert seen == [None]


def test_adk_gemini_connection_uses_the_gemini_capabilities(
    http: DonkeyAsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []

    def _connection(self: Adapter, factory: str | None = None) -> dict[str, Any]:
        seen.append(factory)
        raise _Routed

    cls = _adapter_class("adk")
    monkeypatch.setattr(cls, "_connection", _connection)
    with pytest.raises(_Routed):
        cls(_client_id_cfg(), http).gemini_connection_kwargs()  # type: ignore[attr-defined]
    assert seen == ["gemini"]


@pytest.mark.parametrize("mode", ["jwt", "bearer"])
@pytest.mark.parametrize("attr", _ROSTER)
async def test_token_mode_without_a_provider_refuses_every_adapter(
    attr: str, mode: str
) -> None:
    cfg = _token_cfg(mode)
    http = build_http_client(cfg, None)
    try:
        with pytest.raises(ConfigError):
            _adapter_class(attr)(cfg, http).connection_kwargs()
    finally:
        await http.aclose()


@pytest.mark.parametrize("mode", ["jwt", "bearer"])
@pytest.mark.parametrize("attr", _ROSTER)
async def test_token_mode_with_a_provider_refuses_only_framework_transport(
    attr: str, mode: str
) -> None:
    cfg = _token_cfg(mode)
    http = build_http_client(cfg, StaticToken("FAKE.JWT"))
    adapter = _adapter_class(attr)(cfg, http)
    try:
        if adapter.capabilities().transport == "framework":
            with pytest.raises(ConfigError, match="does not support llm_proxy_auth"):
                adapter.connection_kwargs()
        else:
            try:
                assert adapter.connection_kwargs()
            except ImportError:
                # openai_agents' kwarg is a pre-built AsyncOpenAI.
                pytest.skip(f"{attr}'s connection needs a dependency not installed here")
    finally:
        await http.aclose()


@pytest.mark.parametrize("attr", _ROSTER)
def test_capabilities_are_frozen_and_declared_once(attr: str) -> None:
    cls = _adapter_class(attr)
    assert isinstance(cls.factories, MappingProxyType)
    assert cls.factories, f"{cls.__name__} declares no factory"
    with pytest.raises(TypeError):
        cls.factories["new"] = cls.capabilities()  # type: ignore[index]
    for caps in cls.factories.values():
        assert isinstance(caps, AdapterCapabilities)
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.observes_last_call = not caps.observes_last_call  # type: ignore[misc]
    assert cls.observes_last_call is cls.capabilities().observes_last_call


def test_unknown_factory_is_refused() -> None:
    with pytest.raises(ValueError, match="no factory 'nope'"):
        _adapter_class("langgraph").capabilities("nope")


def test_adk_model_and_gemini_report_separately(http: DonkeyAsyncClient) -> None:
    # The instance, as donkey.adk is, without needing the adk extra installed.
    adk = _adapter_class("adk")(_client_id_cfg(), http)
    model = adk.capabilities("model")
    gemini = adk.capabilities("gemini")
    assert model != gemini
    assert adk.capabilities() is model
    # Both observe since #946; only model() lacks typed refusals (#724).
    assert model.observes_last_call and gemini.observes_last_call
    assert (model.typed_refusals, gemini.typed_refusals) == (False, True)


def test_strands_reports_streaming_off() -> None:
    # The governed connection sets stream=False (#830).
    assert _adapter_class("strands").capabilities().streaming is False


def test_only_crewai_brings_its_own_transport() -> None:
    framework = {
        attr
        for attr, factory in _FACTORIES
        if _adapter_class(attr).capabilities(factory).transport == "framework"
    }
    assert framework == {"crewai"}


def test_an_adapter_without_factories_is_refused_at_definition() -> None:
    with pytest.raises(TypeError, match="declares no factory"):

        class _Empty(Adapter):
            factories = MappingProxyType({})

            def connection_kwargs(self) -> dict[str, Any]:
                return {}


def test_a_concrete_adapter_must_declare_factories() -> None:
    # Without factories, capabilities() and donkey.last_call would fail later,
    # far from the mistake; an abstract intermediate base may leave them out.
    class _AbstractBase(Adapter):
        pass

    with pytest.raises(TypeError, match="must declare factories"):

        class _Concrete(_AbstractBase):
            def connection_kwargs(self) -> dict[str, Any]:
                return {}


def test_factories_written_as_a_dict_are_made_read_only() -> None:
    caps = _adapter_class("langgraph").capabilities()

    class _Dict(Adapter):
        factories = {"build": caps}  # a plain dict, on purpose

        def connection_kwargs(self) -> dict[str, Any]:
            return {}

    assert isinstance(_Dict.factories, MappingProxyType)
    assert _Dict.capabilities() is caps


def _leaves(value: Any) -> Iterator[Any]:
    """Every leaf value of a (nested) kwargs mapping."""
    if isinstance(value, dict):
        for item in value.values():
            yield from _leaves(item)
    else:
        yield value


def _governed_kwargs(attr: str, factory: str, http: DonkeyAsyncClient) -> dict[str, Any]:
    adapter = _adapter_class(attr)(_client_id_cfg(), http)
    if (attr, factory) == ("adk", "gemini"):
        return dict(adapter.gemini_connection_kwargs())  # type: ignore[attr-defined]
    assert factory == next(iter(type(adapter).factories)), (
        f"{attr}.{factory}() has no governed kwargs this test knows how to read"
    )
    try:
        return dict(adapter.connection_kwargs())
    except ImportError:
        # A pre-built AsyncOpenAI needs the [llm] extra, absent base-only.
        pytest.skip(f"{attr}'s connection needs a dependency not installed here")


@pytest.mark.parametrize(("attr", "factory"), _FACTORIES)
def test_capabilities_match_the_governed_wiring(
    attr: str, factory: str, http: DonkeyAsyncClient
) -> None:
    # The capabilities are facts about what the SDK hands the framework (§0.3),
    # so they are read back from the governed kwargs rather than restated: one
    # of our async clients anywhere in them (directly, inside a pre-built
    # OpenAI client, or behind anthropic>=1's httpx2 bridge transport) is the
    # shared transport, one of our blocking clients is sync, and stream=False
    # is streaming off.
    caps = _adapter_class(attr).capabilities(factory)
    kwargs = _governed_kwargs(attr, factory, http)
    clients = [
        c
        for v in _leaves(kwargs)
        for c in (
            v,
            getattr(v, "_client", None),
            getattr(getattr(v, "_transport", None), "_client", None),
        )
    ]
    shared = any(isinstance(c, (DonkeyAsyncClient, DonkeyAsyncClientView)) for c in clients)
    blocking = any(isinstance(c, (DonkeyClient, DonkeyClientView)) for c in clients)
    if not shared and importlib.util.find_spec("openai") is None:
        # ADK model() and Agent Framework leave out the pre-built AsyncOpenAI
        # when the [llm] extra is absent (base-only), so there is nothing to read.
        pytest.skip(f"{attr}.{factory}()'s shared client needs the openai package")
    assert (caps.transport == "shared") is shared
    assert caps.sync is blocking
    assert caps.streaming is (kwargs.get("stream") is not False)
