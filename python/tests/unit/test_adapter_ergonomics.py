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

import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient, build_http_client
from donkey_kit.integrations import _base
from donkey_kit.integrations._base import default_adapter
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
    import warnings

    from donkey_kit.integrations.anthropic import AnthropicAdapter

    with warnings.catch_warnings():
        # The Anthropic-native proxy route is an open verification item (verification discipline);
        # connection_kwargs() warns once about it. Not what this test asserts.
        warnings.simplefilter("ignore")
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


def test_openai_agents_governed_client_carries_proxy_config() -> None:
    """The OpenAI Agents SDK wants a *pre-built* client, so this adapter has no
    connection_kwargs() — its governed surface is a native AsyncOpenAI bound to
    the proxy. Still supported at the connection level after the roster cut."""
    openai = pytest.importorskip("openai")

    from donkey_kit.integrations.openai_agents import OpenAIAgentsAdapter

    client = OpenAIAgentsAdapter(_cfg(), _http())._proxy_openai_client()
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
