"""Every adapter form raises the same curated ImportError when its framework, or
a module the framework needs, is missing (BG §1.8, #741).

Three forms reach a factory: ``Donkey.<framework>.<factory>()``, the adapter
method on a standalone adapter, and the module-level factory. Before #741 only
the first was curated; the other two leaked a bare ``ModuleNotFoundError``.

"Framework missing" blocks the module the factory imports. "Dependency missing"
serves that module from a finder whose import fails on a module that is not
installed, which is how a half-installed framework looks (Strands without
``openai``).
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import types
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest

from donkey_kit import Donkey
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.transport import build_http_client
from donkey_kit.integrations import ADAPTERS, _base

_MISSING_DEP = "_ddk_test_missing_dependency"

# (factory, the module it imports lazily, call) for every factory on every
# adapter. The call takes the adapter (or, for the module-level form, the
# module) and builds the native object.
_Call = Callable[[Any], Any]
FACTORIES: dict[str, list[tuple[str, str, _Call]]] = {
    "langgraph": [
        ("chat_model", "langchain_openai", lambda a: a.chat_model("m")),
        ("__call__", "langchain_openai", lambda a: a("m")),
    ],
    "adk": [
        ("model", "google.adk.models.lite_llm", lambda a: a.model("m")),
        ("gemini", "google.adk.models", lambda a: a.gemini("m")),
    ],
    "strands": [("model", "strands.models.openai", lambda a: a.model("m"))],
    "agent_framework": [
        ("chat_client", "agent_framework.openai", lambda a: a.chat_client("m")),
    ],
    "openai_agents": [("model", "agents", lambda a: a.model("m"))],
    "anthropic": [("client", "anthropic", lambda a: a.client())],
    "crewai": [("llm", "crewai", lambda a: a.llm("m"))],
    "llamaindex": [("llm", "llama_index.llms.openai_like", lambda a: a.llm("m"))],
}

CASES = [
    pytest.param(attr, factory, module, call, id=f"{attr}.{factory}")
    for attr, factories in FACTORIES.items()
    for factory, module, call in factories
]


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


class _BrokenFramework(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Serves ``target`` (and any parent package not already imported) as an
    installed module whose own import fails on :data:`_MISSING_DEP`."""

    def __init__(self, target: str) -> None:
        parts = target.split(".")
        self._target = target
        self._parents = {".".join(parts[:i]) for i in range(1, len(parts))}

    def find_spec(
        self, fullname: str, path: Sequence[str] | None, target: object = None
    ) -> importlib.machinery.ModuleSpec | None:
        if fullname == self._target:
            return importlib.machinery.ModuleSpec(fullname, self)
        if fullname in self._parents:
            return importlib.machinery.ModuleSpec(fullname, self, is_package=True)
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> None:
        return None

    def exec_module(self, module: types.ModuleType) -> None:
        if module.__name__ == self._target:
            importlib.import_module(_MISSING_DEP)


@pytest.fixture(params=["framework", "dependency"])
def missing(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[
    Callable[[str], str]
]:
    """Make a factory's lazy import fail: ``missing(module)`` blocks the module
    itself, or serves it with a dependency that is not installed. Returns the
    name the curated error should report as missing."""
    added: list[str] = []
    before = set(sys.modules)

    def apply(module: str) -> str:
        if request.param == "framework":
            monkeypatch.setitem(sys.modules, module, None)
            return module
        # Drop the real module (and its parents) if this env has them, so the
        # finder is asked; monkeypatch puts them back.
        parts = module.split(".")
        for i in range(len(parts), 0, -1):
            monkeypatch.delitem(sys.modules, ".".join(parts[:i]), raising=False)
        finder = _BrokenFramework(module)
        monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
        added.append(module)
        return _MISSING_DEP

    yield apply
    for name in set(sys.modules) - before:
        if any(name == m or m.startswith(name + ".") for m in added):
            del sys.modules[name]


def _assert_curated(
    exc: pytest.ExceptionInfo[ImportError], attr: str, missing_module: str | None = None
) -> None:
    # The curated error itself, not a ModuleNotFoundError leaking through.
    assert type(exc.value) is ImportError
    message = str(exc.value)
    assert f'pip install "donkey-kit[{ADAPTERS[attr].extra}]"' in message
    assert f"The {attr!r} integration is not installed" in message
    if missing_module is not None:
        assert f"(no module named {missing_module!r})" in message


def test_every_adapter_has_its_factories_listed() -> None:
    assert set(FACTORIES) == set(ADAPTERS)


@pytest.mark.parametrize(("attr", "factory", "module", "call"), CASES)
def test_donkey_form_raises_curated_error(
    attr: str, factory: str, module: str, call: _Call, missing: Callable[[str], str]
) -> None:
    reported = missing(module)
    with Donkey(_cfg()) as donkey, pytest.raises(ImportError) as exc:
        call(getattr(donkey, attr))
    # Access may already fail on the probe, naming the probed module instead.
    _assert_curated(exc, attr)
    assert reported in str(exc.value) or "no module named" in str(exc.value)


@pytest.mark.parametrize(("attr", "factory", "module", "call"), CASES)
async def test_adapter_method_raises_curated_error(
    attr: str, factory: str, module: str, call: _Call, missing: Callable[[str], str]
) -> None:
    reported = missing(module)
    spec = ADAPTERS[attr]
    cls = getattr(importlib.import_module(f"donkey_kit.integrations{spec.module}"), spec.cls)
    http = build_http_client(_cfg(), None)
    try:
        with pytest.raises(ImportError) as exc:
            call(cls(_cfg(), http))
    finally:
        await http.aclose()
    _assert_curated(exc, attr, reported)


@pytest.mark.parametrize(("attr", "factory", "module", "call"), CASES)
def test_module_level_factory_raises_curated_error(
    attr: str,
    factory: str,
    module: str,
    call: _Call,
    missing: Callable[[str], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if factory == "__call__":
        pytest.skip("callable sugar exists only on the adapter, not as a module function")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://proxy")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(_base, "_DEFAULT_ADAPTERS", {})
    reported = missing(module)
    adapter_module = importlib.import_module(f"donkey_kit.integrations{ADAPTERS[attr].module}")
    with pytest.raises(ImportError) as exc:
        call(adapter_module)
    _assert_curated(exc, attr, reported)


@pytest.mark.parametrize("attr", [a for a, s in ADAPTERS.items() if len(s.probe) > 1])
def test_access_fails_when_a_probed_dependency_is_missing(
    attr: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Strands without ``openai`` (and ADK without ``litellm``) used to pass the
    # single-module probe and fail later with a bare ModuleNotFoundError.
    framework, dependency = ADAPTERS[attr].probe[:2]
    real_find_spec = importlib.util.find_spec

    def find_spec(name: str, package: str | None = None) -> Any:
        if name == framework:
            return importlib.machinery.ModuleSpec(name, None)
        if name == dependency:
            return None
        return real_find_spec(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    with Donkey(_cfg()) as donkey, pytest.raises(ImportError) as exc:
        getattr(donkey, attr)
    _assert_curated(exc, attr)
    assert f"(no module named {dependency!r})" in str(exc.value)


@pytest.mark.parametrize("attr", sorted(ADAPTERS))
def test_adapter_extra_is_read_from_the_roster(attr: str) -> None:
    # The pip extra is declared once, on ADAPTERS; the adapter reads it (#720).
    spec = ADAPTERS[attr]
    cls = getattr(importlib.import_module(spec.module, "donkey_kit.integrations"), spec.cls)
    assert "extra" not in vars(cls)
    assert object.__new__(cls).extra == spec.extra


def test_an_adapter_off_the_roster_has_no_extra() -> None:
    class Unlisted(_base.Adapter):
        def connection_kwargs(self) -> dict[str, Any]:
            return {}

    assert object.__new__(Unlisted).extra == ""
