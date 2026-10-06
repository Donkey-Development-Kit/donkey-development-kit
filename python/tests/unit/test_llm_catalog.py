"""Direct unit coverage for ``llm/catalog.py`` and the uncovered branches of
``llm/client.py`` (coverage floor review, #751).

Both modules are exercised indirectly all over the suite (every test that
builds an ``LLMClient`` touches ``client()``), but nothing imported
``llm.catalog`` directly or called ``LLMClient.resolve()`` /
``LLMClient.list_models()`` before this file, so the heuristic-lookup miss
branch and both ``list_models()`` guard-rail paths went unmeasured.
"""

from __future__ import annotations

import pytest

from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import build_http_client
from donkey_kit.llm.catalog import ModelCapabilities, ModelHandle, heuristic_capabilities
from donkey_kit.llm.client import LLMClient


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy.example/sdk/",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
    )


def _client() -> LLMClient:
    cfg = _cfg()
    return LLMClient(cfg, build_http_client(cfg, None))


def test_heuristic_capabilities_known_model_reads_the_bundled_row() -> None:
    # A bundled row overrides the conservative defaults, but it is still a
    # heuristic: `is_heuristic` only means False once a value is sourced from
    # the platform (BG §1.1), which no bundled row is today.
    caps = heuristic_capabilities("gpt-4o")
    assert caps != ModelCapabilities()
    assert caps.function_calling is True
    assert caps.vision is True
    assert caps.json_output is True
    assert caps.is_heuristic is True


def test_heuristic_capabilities_unknown_model_falls_back_to_conservative_default() -> None:
    caps = heuristic_capabilities("some-model-nobody-has-heard-of")
    assert caps == ModelCapabilities()
    assert caps.function_calling is True
    assert caps.vision is False
    assert caps.json_output is False


def test_resolve_returns_a_heuristic_handle_for_a_known_model() -> None:
    handle = _client().resolve("claude-sonnet-5", provider="anthropic")
    assert handle == ModelHandle(
        id="claude-sonnet-5",
        provider="anthropic",
        capabilities=heuristic_capabilities("claude-sonnet-5"),
    )


def test_resolve_returns_a_conservative_handle_for_an_unknown_model() -> None:
    handle = _client().resolve("not-in-the-table")
    assert handle.provider is None
    assert handle.capabilities == ModelCapabilities()


@pytest.mark.asyncio
async def test_list_models_live_reports_the_verified_404_absence() -> None:
    # docs/verified-apis.md §2: GET /models is confirmed 404, so live=True must
    # say so plainly rather than guess a path (§0.3).
    with pytest.raises(ConfigError, match="no /models endpoint"):
        await _client().list_models(live=True)


@pytest.mark.asyncio
async def test_list_models_offline_has_no_source_of_truth_yet() -> None:
    with pytest.raises(ConfigError, match="no offline source of truth"):
        await _client().list_models(live=False)
