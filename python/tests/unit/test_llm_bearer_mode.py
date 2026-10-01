"""``llm_proxy_auth="bearer"``: a first-class bearer token for model calls (#836).

The token comes from the ``Donkey(llm_auth=...)`` provider and is added per
send by the shared data-plane client as the verified ``Authorization: Bearer``
header (docs/verified-apis.md §2), with no ``client_id``/``client_secret`` pair
and no wallet ``X-Client-Id``. It never reaches a control-plane request; a 401
refreshes it and retries once; the endpoint binding check covers it; and a form
that can hand the framework only static headers refuses the mode.
"""

from __future__ import annotations

import contextlib
import importlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import runtime
from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import DonkeyAsyncClient, proxy_auth_headers
from donkey_kit.integrations import ADAPTERS
from donkey_kit.llm.client import LLMClient

_PROXY = "https://gw.example.internal/bearer/"
_TOKEN = "bearer-token-value"
_CONTROL_TOKEN = "control-plane-token-value"


def _bearer_cfg(**kw: Any) -> DonkeyConfig:
    return DonkeyConfig(llm_proxy_auth="bearer", llm_proxy_url=_PROXY, max_retries=0, **kw)


class _CountingProvider:
    """Counts ``token()`` and ``invalidate()`` and rotates on invalidation."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.generation = 1
        self.token_calls = 0
        self.invalidations = 0

    async def token(self) -> str:
        self.token_calls += 1
        return f"{self.prefix}-{self.generation}"

    async def invalidate(self) -> None:
        self.invalidations += 1
        self.generation += 1


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            400, json={"error": {"message": "stop", "type": "invalid_request_error"}}
        )


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


# --- config ------------------------------------------------------------------


def test_bearer_mode_needs_only_the_proxy_url() -> None:
    assert _bearer_cfg().missing_fields(need="llm") == []
    (missing,) = DonkeyConfig(llm_proxy_auth="bearer").missing_fields(need="llm")
    assert missing.startswith("llm_proxy_url")


def test_bearer_mode_parses_from_the_environment(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "bearer")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", _PROXY)
    cfg = DonkeyConfig.from_env()
    assert cfg.llm_proxy_auth == "bearer"
    assert cfg.missing_fields(need="llm") == []


def test_an_unknown_mode_lists_bearer(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "basic")
    with pytest.raises(ConfigError, match="'bearer'"):
        DonkeyConfig.from_env()


def test_bearer_mode_static_headers_carry_no_consumer_credential() -> None:
    cfg = _bearer_cfg(llm_proxy_client_id="cid", llm_proxy_client_secret="secret")
    headers = {k.lower() for k in proxy_auth_headers(cfg)}
    assert not headers & {"client_id", "client_secret", "x-client-id", "authorization"}


# --- the data plane sends the token, the control plane never does ------------


async def test_data_plane_request_carries_the_bearer_token_only() -> None:
    upstream = _Recorder()
    async with Donkey(_bearer_cfg(), llm_auth=StaticToken(_TOKEN)) as donkey:
        donkey._http._swap_transport(httpx.MockTransport(upstream))
        await donkey._http.post(f"{_PROXY}chat/completions", json={"model": "m"})

    (request,) = upstream.requests
    assert request.headers["authorization"] == f"Bearer {_TOKEN}"
    for name in ("x-client-id", "client_id", "client_secret"):
        assert name not in request.headers


async def test_a_401_refreshes_the_token_and_retries_once() -> None:
    provider = _CountingProvider("tok")
    seen: list[str] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(401)

    async with Donkey(_bearer_cfg(), llm_auth=provider) as donkey:
        donkey._http._swap_transport(httpx.MockTransport(upstream))
        resp = await donkey._http.post(f"{_PROXY}chat/completions", json={"model": "m"})

    assert resp.status_code == 401  # a second 401 is terminal
    assert seen == ["Bearer tok-1", "Bearer tok-2"]
    assert provider.invalidations == 1


async def test_the_token_never_reaches_a_control_plane_request() -> None:
    provider = _CountingProvider("tok")
    platform = _Recorder()
    cfg = _bearer_cfg(base_url="https://anypoint.example.test")
    async with Donkey(cfg, auth=StaticToken(_CONTROL_TOKEN), llm_auth=provider) as donkey:
        donkey.registry._http._swap_transport(httpx.MockTransport(platform))
        await donkey.registry._http.get(f"{cfg.base_url}/exchange/api/v2/assets")

    (request,) = platform.requests
    assert request.headers["authorization"] == f"Bearer {_CONTROL_TOKEN}"
    assert provider.token_calls == 0


# --- every shared-client form sends the token --------------------------------


_MESSAGES = [{"role": "user", "content": "hi"}]


async def _adk_generate(model: Any) -> None:
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    request = LlmRequest(
        model=model.model,
        contents=[types.Content(role="user", parts=[types.Part(text="hi")])],
    )
    async for _ in model.generate_content_async(request):
        pass


async def _strands_stream(model: Any) -> None:
    async for _ in model.stream([{"role": "user", "content": [{"text": "hi"}]}]):
        pass


_FORMS: dict[str, tuple[str, Callable[[Donkey], Awaitable[Any] | Any]]] = {
    "raw": (
        "openai",
        lambda d: d.llm.client().chat.completions.create(model="m", messages=_MESSAGES),
    ),
    "langgraph": ("langchain_openai", lambda d: d.langgraph.chat_model("m").ainvoke("hi")),
    "strands": ("strands.models.openai", lambda d: _strands_stream(d.strands.model("m"))),
    "openai_agents": (
        "agents",
        lambda d: d.openai_agents.connection_kwargs()["openai_client"].chat.completions.create(
            model="m", messages=_MESSAGES
        ),
    ),
    "anthropic": (
        "anthropic",
        lambda d: d.anthropic.client().messages.create(model="m", max_tokens=1, messages=_MESSAGES),
    ),
    "adk_gemini": ("google.adk.models", lambda d: _adk_generate(d.adk.gemini("m"))),
    "adk_model": ("google.adk.models", lambda d: _adk_generate(d.adk.model("m"))),
    "llamaindex": (
        "llama_index.llms.openai_like",
        lambda d: d.llamaindex.llm("m").acomplete("hi"),
    ),
    "agent_framework": (
        "agent_framework.openai",
        lambda d: d.agent_framework.chat_client("m").get_response("hi"),
    ),
}


@pytest.mark.parametrize("form", sorted(_FORMS))
async def test_every_shared_client_form_sends_the_bearer_token(form: str) -> None:
    module, build = _FORMS[form]
    pytest.importorskip(module)
    upstream = _Recorder()
    donkey = Donkey(_bearer_cfg(), llm_auth=StaticToken(_TOKEN))
    try:
        donkey._http._swap_transport(httpx.MockTransport(upstream))
        with contextlib.suppress(Exception):
            result = build(donkey)
            if hasattr(result, "__await__"):
                await result
    finally:
        await donkey.aclose()

    assert upstream.requests, "the call never reached the proxy"
    for request in upstream.requests:
        assert request.headers["authorization"] == f"Bearer {_TOKEN}"
        assert "x-client-id" not in request.headers
        assert "client_secret" not in request.headers


# --- guards ------------------------------------------------------------------


async def test_the_sync_raw_client_is_refused_as_async_only() -> None:
    cfg = _bearer_cfg()
    http = DonkeyAsyncClient(cfg, StaticToken(_TOKEN))
    try:
        with pytest.raises(ConfigError, match="async-only"):
            LLMClient(cfg, http).client(sync=True)
    finally:
        await http.aclose()


async def test_the_raw_client_without_a_provider_is_refused_with_guidance() -> None:
    cfg = _bearer_cfg()
    http = DonkeyAsyncClient(cfg, None)
    try:
        with pytest.raises(ConfigError, match="requires an AuthProvider") as exc:
            LLMClient(cfg, http).client()
    finally:
        await http.aclose()
    assert "bearer token" in str(exc.value)
    assert "llm_auth" in str(exc.value)


@pytest.mark.parametrize("name", sorted(ADAPTERS))
async def test_every_adapter_without_a_provider_refuses_bearer_mode(name: str) -> None:
    cfg = _bearer_cfg()
    spec = ADAPTERS[name]
    cls = getattr(importlib.import_module(spec.module, "donkey_kit.integrations"), spec.cls)
    http = DonkeyAsyncClient(cfg, None)
    try:
        with pytest.raises(ConfigError, match="AuthProvider|can't send the token"):
            cls(cfg, http).connection_kwargs()
    finally:
        await http.aclose()


def test_a_module_level_factory_refuses_bearer_mode(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from donkey_kit.integrations._base import default_adapter
    from donkey_kit.integrations.anthropic import AnthropicAdapter

    monkeypatch.setenv("DONKEY_LLM_PROXY_AUTH", "bearer")
    monkeypatch.setenv("DONKEY_LLM_PROXY_URL", _PROXY)
    runtime.close_default()
    try:
        with pytest.raises(ConfigError, match="module-level factories"):
            default_adapter(AnthropicAdapter).connection_kwargs()
    finally:
        runtime.close_default()


async def test_crewai_refuses_bearer_mode_even_with_a_provider() -> None:
    async with Donkey(_bearer_cfg(), llm_auth=StaticToken(_TOKEN)) as donkey:
        with pytest.raises(ConfigError, match="CrewAI can't send the token"):
            donkey.crewai.connection_kwargs()


async def test_client_id_mode_attaches_no_data_plane_token() -> None:
    upstream = _Recorder()
    cfg = DonkeyConfig(
        llm_proxy_url=_PROXY,
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="secret",
        max_retries=0,
    )
    provider = _CountingProvider("tok")
    async with Donkey(cfg, llm_auth=provider) as donkey:
        donkey._http._swap_transport(httpx.MockTransport(upstream))
        await donkey._http.post(f"{_PROXY}chat/completions", json={"model": "m"})

    (request,) = upstream.requests
    assert "authorization" not in request.headers
    assert provider.token_calls == 0


# --- endpoint binding --------------------------------------------------------


_PROJECT_LLM = "https://llm.example.test/proxy/"


def _project_bearer_toml(project: Path) -> None:
    (project / ".donkey-kit.toml").write_text(
        f'[donkey]\nllm_proxy_url = "{_PROJECT_LLM}"\nllm_proxy_auth = "bearer"\n'
    )


async def test_the_token_is_not_fetched_for_a_project_llm_proxy_url(clean_env: Path) -> None:
    pytest.importorskip("openai")
    _project_bearer_toml(clean_env)
    provider = _CountingProvider("tok")
    async with Donkey(DonkeyConfig.from_env(), llm_auth=provider) as donkey:
        with pytest.raises(ConfigError, match="llm.example.test"):
            donkey.llm.client()
    assert provider.token_calls == 0


async def test_a_raw_request_does_not_fetch_the_token_for_a_project_llm_proxy_url(
    clean_env: Path,
) -> None:
    _project_bearer_toml(clean_env)
    provider = _CountingProvider("tok")
    upstream = _Recorder()
    async with Donkey(DonkeyConfig.from_env(), llm_auth=provider) as donkey:
        donkey._http._swap_transport(httpx.MockTransport(upstream))
        with pytest.raises(ConfigError, match="llm.example.test"):
            await donkey._http.post(f"{_PROJECT_LLM}chat/completions", json={"model": "m"})
    assert provider.token_calls == 0
    assert upstream.requests == []
