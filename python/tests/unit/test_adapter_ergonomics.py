"""The two additive adapter ergonomics (BG §1.8), alongside the existing
``donkey.<framework>.<factory>()`` methods:

  1. ``connection_kwargs()`` — governed kwargs you spread into the framework's
     own constructor yourself.
  2. module-level factories (e.g. ``langgraph.chat_model``) backed by a cached
     default env-configured adapter.

These are framework-free where possible: ``connection_kwargs`` and the default
adapter build without importing any framework (adapter modules import their
framework lazily inside the native factory only). Tests that must construct the
native object ``importorskip`` the framework.
"""

from __future__ import annotations

import builtins
import importlib
import sys
import types
import warnings
from typing import Any, NamedTuple

import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient, build_http_client
from donkey_kit.integrations import _base
from donkey_kit.integrations._base import Adapter, default_adapter
from donkey_kit.integrations.langgraph import LangGraphAdapter


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


def _adapter() -> LangGraphAdapter:
    cfg = _cfg()
    return LangGraphAdapter(cfg, build_http_client(cfg, None))


def test_connection_kwargs_carry_governed_values() -> None:
    kw = _adapter().connection_kwargs()
    assert kw["base_url"] == "https://proxy"
    assert "client_id" in kw["default_headers"]
    assert "client_secret" in kw["default_headers"]
    assert kw["max_retries"] == 0  # we retry in transport, not the framework
    assert kw["http_async_client"] is not None  # our shared, hooked client
    # verified /responses endpoint (docs/verified-apis.md §4)
    assert kw["use_responses_api"] is True
    # No model id — the caller supplies that: ChatOpenAI(model=…, **kw)
    assert "model" not in kw


def test_connection_kwargs_requires_proxy_config() -> None:
    cfg = DonkeyConfig()  # no proxy creds
    adapter = LangGraphAdapter(cfg, build_http_client(cfg, None))
    with pytest.raises(ConfigError):
        adapter.connection_kwargs()


def test_adk_connection_kwargs_use_litellm_names() -> None:
    # LiteLLM uses api_base/extra_headers rather than base_url/default_headers,
    # and owns its own transport (no shared http client injected).
    from donkey_kit.integrations.adk import ADKAdapter

    cfg = _cfg()
    kw = ADKAdapter(cfg, build_http_client(cfg, None)).connection_kwargs()
    assert kw["api_base"] == "https://proxy"
    assert "client_id" in kw["extra_headers"]
    assert "base_url" not in kw
    assert "http_client" not in kw


def test_default_adapter_is_cached_per_class(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://proxy")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "csecret")
    _base._DEFAULT_ADAPTERS.clear()

    a1 = default_adapter(LangGraphAdapter)
    a2 = default_adapter(LangGraphAdapter)
    assert a1 is a2  # cached
    assert isinstance(a1, LangGraphAdapter)


def test_module_level_factory_matches_method(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("langchain_openai")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://proxy")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "csecret")
    _base._DEFAULT_ADAPTERS.clear()

    from donkey_kit.integrations.langgraph import chat_model

    model = chat_model("gpt-4o", temperature=0.1)
    assert type(model).__name__ == "ChatOpenAI"  # native object, no wrapper
    assert model.openai_api_base == "https://proxy"
    assert model.temperature == 0.1  # kwargs pass through


# --- connection_kwargs() works for all eight (one deep, seven shallow, #197) ---
#
# The roster cut (BG §1.8) leaves seven frameworks supported at connection_kwargs()
# only, so that accessor is their entire governed surface — each gets a test here
# proving it still carries the proxy base URL and the verified consumer-auth
# headers. These build offline (no framework import): connection_kwargs() only
# validates proxy config and shapes a dict.


def _http() -> DonkeyAsyncClient:
    return build_http_client(_cfg(), None)


def test_strands_connection_kwargs_inject_client_and_headers() -> None:
    from donkey_kit.integrations.strands import StrandsAdapter

    kw = StrandsAdapter(_cfg(), _http()).connection_kwargs()
    args = kw["client_args"]  # Strands forwards these to the OpenAI client
    assert args["base_url"] == "https://proxy"
    assert "client_id" in args["default_headers"]
    assert args["http_client"] is not None  # full transport injection


def test_agent_framework_connection_kwargs_are_the_openai_connection() -> None:
    from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter

    kw = AgentFrameworkAdapter(_cfg(), _http()).connection_kwargs()
    assert kw["base_url"] == "https://proxy"
    assert "client_id" in kw["default_headers"]


def test_anthropic_connection_kwargs_carry_proxy_and_shared_client() -> None:
    from donkey_kit.integrations.anthropic import AnthropicAdapter

    # The proxy's Anthropic-native route is LIVE-verified (#304), so connection_kwargs()
    # no longer emits an unverified-route warning.
    kw = AnthropicAdapter(_cfg(), _http()).connection_kwargs()
    assert kw["base_url"] == "https://proxy"
    assert "client_id" in kw["default_headers"]
    assert kw["http_client"] is not None
    assert kw["max_retries"] == 0  # we retry in transport (BG §1.1)


def test_crewai_connection_kwargs_use_litellm_extra_headers() -> None:
    from donkey_kit.integrations.crewai import CrewAIAdapter

    # crewai.LLM forwards to LiteLLM, which uses extra_headers and owns its own
    # transport, so no shared http client is injected (BG §1.8 exemption; the conformance kit).
    kw = CrewAIAdapter(_cfg(), _http()).connection_kwargs()
    assert kw["base_url"] == "https://proxy"
    assert "client_id" in kw["extra_headers"]
    assert "default_headers" not in kw
    assert "http_client" not in kw


def test_llamaindex_connection_kwargs_use_api_base_and_chat_flags() -> None:
    from donkey_kit.integrations.llamaindex import LlamaIndexAdapter

    kw = LlamaIndexAdapter(_cfg(), _http()).connection_kwargs()
    assert kw["api_base"] == "https://proxy"  # LlamaIndex name, not base_url
    assert "base_url" not in kw
    assert "client_id" in kw["default_headers"]
    assert kw["is_chat_model"] is True  # never omit — completions-endpoint gotcha
    assert kw["is_function_calling_model"] is True


def test_openai_agents_connection_kwargs_carry_governed_client() -> None:
    """The OpenAI Agents SDK wants a *pre-built* client, so unlike the
    OpenAI-compatible adapters this one's connection_kwargs() returns a single
    ``openai_client`` key holding a native AsyncOpenAI bound to the proxy — header
    AND transport injection travel as one object (BG §1.8)."""
    openai = pytest.importorskip("openai")

    from donkey_kit.integrations.openai_agents import OpenAIAgentsAdapter

    kw = OpenAIAgentsAdapter(_cfg(), _http()).connection_kwargs()
    assert set(kw) == {"openai_client"}  # not loose base_url/default_headers
    client = kw["openai_client"]
    assert isinstance(client, openai.AsyncOpenAI)
    assert str(client.base_url) == "https://proxy"
    assert client.default_headers["client_id"] == "cid"
    assert client.max_retries == 0  # we retry in transport (BG §1.1)


def test_only_langgraph_is_conformance_tested() -> None:
    """One deep, seven shallow (BG §1.8, #197): LangGraph is the sole
    conformance-tested adapter; the other seven are present in the registry but
    supported at connection_kwargs() only — no code deletion."""
    from donkey_kit.integrations import ADAPTERS

    tested = {k for k, spec in ADAPTERS.items() if spec.conformance_tested}
    assert tested == {"langgraph"}
    assert set(ADAPTERS) - tested == {
        "adk",
        "strands",
        "agent_framework",
        "openai_agents",
        "anthropic",
        "crewai",
        "llamaindex",
    }


# --- Agent Framework behavior (BG §1.2) -------------------------------------


async def test_agent_framework_policy_middleware_passes_through_result() -> None:
    from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter

    context = object()
    result = object()

    async def next_(received: object) -> object:
        assert received is context
        return result

    middleware = AgentFrameworkAdapter(_cfg(), _http()).policy_middleware()

    assert await middleware(context, next_) is result


async def test_agent_framework_policy_middleware_preserves_policy_violation() -> None:
    from donkey_kit.core.errors import PolicyViolation
    from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter

    violation = PolicyViolation("gateway refused the request")

    async def next_(_context: object) -> None:
        raise violation

    middleware = AgentFrameworkAdapter(_cfg(), _http()).policy_middleware()

    with pytest.raises(PolicyViolation) as exc_info:
        await middleware(object(), next_)

    assert exc_info.value is violation


async def test_agent_framework_policy_middleware_preserves_unrelated_error() -> None:
    from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter

    error = RuntimeError("agent failed")

    async def next_(_context: object) -> None:
        raise error

    middleware = AgentFrameworkAdapter(_cfg(), _http()).policy_middleware()

    with pytest.raises(RuntimeError) as exc_info:
        await middleware(object(), next_)

    assert exc_info.value is error


def test_agent_framework_chat_client_blocks_unverified_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter

    import_error = ImportError("OpenAIChatClient is unavailable")
    original_import = builtins.__import__

    def fail_openai_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "agent_framework.openai":
            raise import_error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_openai_import)
    adapter = AgentFrameworkAdapter(_cfg(), _http())

    with pytest.raises(NotImplementedError, match=r"^blocked on verification:") as exc_info:
        adapter.chat_client("gpt-4o")

    assert exc_info.value.__cause__ is import_error


def test_agent_framework_chat_client_constructs_with_package_present() -> None:
    """The mirror of the blocks-on-import test (#520): with agent-framework
    actually installed, the factory returns a real ``OpenAIChatClient`` built
    with the VERIFIED ``model=`` kwarg — the path the acceptance harness hit
    that the package-absent test never exercised. VERIFIED: agent-framework
    1.19.0 (docs/verified-apis.md §8)."""
    pytest.importorskip("agent_framework")
    from agent_framework.openai import OpenAIChatClient

    from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter

    client = AgentFrameworkAdapter(_cfg(), _http()).chat_client("gpt-4o")
    assert isinstance(client, OpenAIChatClient)


def test_agent_framework_chat_client_blocks_on_constructor_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future upstream kwarg rename must surface as a ``_verify.blocked(...)``
    refusal, not the raw ``TypeError`` that reached callers in 0.1.0.dev4 (#520,
    §0.3). Stub ``OpenAIChatClient`` with a constructor that rejects ``model=``
    (as a rename would); the widened guard turns the resulting ``TypeError``
    into a verification-blocked error. Runs without the package installed."""
    from donkey_kit.integrations.agent_framework import AgentFrameworkAdapter

    class _RenamedChatClient:
        def __init__(self, *, renamed_model: str, **_kw: Any) -> None:  # no ``model``
            self.renamed_model = renamed_model

    for name in ("agent_framework", "agent_framework.openai"):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    pkg, openai_mod = sys.modules["agent_framework"], sys.modules["agent_framework.openai"]
    monkeypatch.setattr(pkg, "openai", openai_mod, raising=False)
    monkeypatch.setattr(openai_mod, "OpenAIChatClient", _RenamedChatClient, raising=False)

    adapter = AgentFrameworkAdapter(_cfg(), _http())
    with pytest.raises(NotImplementedError, match=r"^blocked on verification:") as exc_info:
        adapter.chat_client("gpt-4o")

    assert isinstance(exc_info.value.__cause__, TypeError)


# --- The two paths cannot drift (issue #33 AC) ------------------------------
#
# Each framework exposes the SAME governed native object two ways: (a) the
# documented "eject" path — spread ``connection_kwargs()`` into the framework's
# own constructor yourself; (b) the module-level factory, which builds it for
# you. If the factory ever passed the native constructor different connection
# values than ``connection_kwargs()`` advertises, the docs' manual-equivalent
# block would silently lie. These tests pin the two paths together per framework.
#
# The check is offline for all eight: we replace each framework's native class
# with a spy that records the kwargs it is constructed with, then assert those
# kwargs carry exactly the governed values ``connection_kwargs()`` returns — no
# real framework install and no per-framework attribute introspection needed.


class _F(NamedTuple):
    module: str  # submodule under donkey_kit.integrations
    factory: str  # module-level factory function name
    adapter_cls: str  # adapter class name (for the shared default-adapter cache)
    native_module: str  # dotted module the factory imports its native class from
    native_attr: str  # native class attribute name to spy on
    args: tuple[str, ...]  # positional args the factory takes (model id, if any)


# Every adapter whose connection_kwargs() is a plain value dict spread straight
# into the native constructor. openai_agents is exercised separately below
# because its governed value is a freshly-built client object (BG §1.8).
_FACTORIES = [
    _F(
        "langgraph", "chat_model", "LangGraphAdapter", "langchain_openai", "ChatOpenAI", ("gpt-4o",)
    ),
    _F("adk", "model", "ADKAdapter", "google.adk.models.lite_llm", "LiteLlm", ("gpt-4o",)),
    _F("strands", "model", "StrandsAdapter", "strands.models.openai", "OpenAIModel", ("gpt-4o",)),
    _F(
        "agent_framework",
        "chat_client",
        "AgentFrameworkAdapter",
        "agent_framework.openai",
        "OpenAIChatClient",
        ("gpt-4o",),
    ),
    _F("anthropic", "client", "AnthropicAdapter", "anthropic", "AsyncAnthropic", ()),
    _F("crewai", "llm", "CrewAIAdapter", "crewai", "LLM", ("gpt-4o",)),
    _F(
        "llamaindex",
        "llm",
        "LlamaIndexAdapter",
        "llama_index.llms.openai_like",
        "OpenAILike",
        ("gpt-4o",),
    ),
]


def _install_native_stub(
    monkeypatch: pytest.MonkeyPatch, dotted: str, attr: str
) -> dict[str, Any]:
    """Replace ``<dotted>.<attr>`` (the native class a factory imports lazily)
    with a spy that records its constructor kwargs, registering stub modules for
    any part of ``dotted`` that is not installed so the lazy ``from`` import
    resolves offline. Returns the dict the spy populates."""
    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        if i > 1:  # link submodule onto its parent so `from a.b import c` resolves
            parent = sys.modules[".".join(parts[: i - 1])]
            monkeypatch.setattr(parent, parts[i - 1], sys.modules[name], raising=False)
    monkeypatch.setattr(sys.modules[dotted], attr, _Spy, raising=False)
    return captured


def _set_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", "https://proxy")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_ID", "cid")
    monkeypatch.setenv("DONKEY_LLM_PROXY_CLIENT_SECRET", "csecret")
    _base._DEFAULT_ADAPTERS.clear()


@pytest.mark.parametrize("f", _FACTORIES, ids=lambda f: f.module)
def test_factory_and_connection_kwargs_do_not_drift(
    f: _F, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_proxy_env(monkeypatch)
    mod = importlib.import_module(f"donkey_kit.integrations.{f.module}")
    factory = getattr(mod, f.factory)
    adapter_cls: type[Adapter] = getattr(mod, f.adapter_cls)

    captured = _install_native_stub(monkeypatch, f.native_module, f.native_attr)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # suppress any one-time adapter construction warnings
        factory(*f.args)

    # The factory built the native object through the process-wide cached default
    # adapter; read connection_kwargs() off that same instance, so the shared http
    # client compares by identity and the header dicts by value.
    adapter = default_adapter(adapter_cls)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        expected = adapter.connection_kwargs()

    # Every governed kwarg the eject path documents reached the native constructor
    # with an identical value. (captured also holds model/model_id — not governed.)
    assert {k: captured[k] for k in expected} == expected


def test_openai_agents_factory_and_connection_kwargs_do_not_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openai_agents' governed value is a pre-built AsyncOpenAI, freshly made on
    each call, so the two paths yield *distinct* client objects — assert they
    carry identical governed values rather than object identity (BG §1.8)."""
    pytest.importorskip("openai")  # _proxy_openai_client builds a real AsyncOpenAI
    _set_proxy_env(monkeypatch)

    captured = _install_native_stub(monkeypatch, "agents", "OpenAIChatCompletionsModel")
    from donkey_kit.integrations.openai_agents import OpenAIAgentsAdapter, model

    model("gpt-4o")
    factory_client = captured["openai_client"]
    accessor_client = default_adapter(OpenAIAgentsAdapter).connection_kwargs()["openai_client"]

    def _governed(c: Any) -> tuple[str, str, str, int]:
        return (
            str(c.base_url),
            c.default_headers["client_id"],
            c.default_headers["client_secret"],
            c.max_retries,
        )

    assert _governed(factory_client) == _governed(accessor_client)
