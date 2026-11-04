"""``ExchangeRegistry.llm_metadata()``: an ``llm`` asset version's
``llm-metadata`` file, read tolerantly (docs/verified-apis.md §7, #1533).

Every read replays ``tests/fixtures/anypoint/llm_metadata/`` (#1411): a custom
LLM provider's version, whose file carries every field, and a DDK LLM proxy's,
whose file carries only ``platform``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import RegistryError
from donkey_kit.core.runtime import Runtime
from donkey_kit.registry import LlmMetadata
from donkey_kit.registry.exchange import ExchangeRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "llm_metadata"
BASE_URL = "https://anypoint.mulesoft.com"
ORG = "00000000-0000-4000-8000-4256cc8e6f68"
REF = f"{ORG}/ddk-custom-llm-provider/1.0.0"
_LINK_HOST = "exchange-files.example.invalid"
_CFG = DonkeyConfig(client_id="client", client_secret="secret", org_id=ORG, base_url=BASE_URL)


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _Exchange:
    """The files query and the pre-signed download, recording every request."""

    def __init__(self, capture: str, *, stored: Any = None, files: Any = None) -> None:
        self.files = files if files is not None else _json(f"{capture}.graphql.files.json")
        self.stored = stored if stored is not None else _json(f"{capture}.llm-metadata.stored.json")
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == _LINK_HOST:
            return httpx.Response(200, json=self.stored)
        assert request.url.path == "/graph/api/v2/graphql"
        variables = json.loads(request.content)["variables"]
        assert variables == {
            "groupId": ORG,
            "assetId": "ddk-custom-llm-provider",
            "version": "1.0.0",
        }
        return httpx.Response(200, json=self.files)


@pytest.fixture
async def runtime() -> AsyncIterator[Runtime]:
    """The real control-plane client, so its credential scoping is what is tested."""
    rt = Runtime(_CFG, auth=StaticToken("control-plane"))
    yield rt
    await rt.aclose()


def _registry(runtime: Runtime, fake: _Exchange) -> ExchangeRegistry:
    runtime.control_http.governed_transport.replace_inner(httpx.MockTransport(fake))
    return ExchangeRegistry(_CFG, runtime.control_http)


async def test_a_custom_providers_file_is_read_in_full(runtime: Runtime) -> None:
    fake = _Exchange("custom")

    metadata = await _registry(runtime, fake).llm_metadata(REF)

    assert metadata == LlmMetadata(
        platform="ddk-custom-openai",
        name="DDK custom provider (test)",
        input_format="openai",
        models=("gpt-4o-mini", "gpt-4o"),
        capabilities=_json("custom.llm-metadata.stored.json")["capabilities"],
    )
    graph, download = fake.requests
    assert graph.headers["authorization"] == "Bearer control-plane"
    assert "authorization" not in download.headers


async def test_a_proxys_file_declares_only_its_platform(runtime: Runtime) -> None:
    metadata = await _registry(runtime, _Exchange("proxy")).llm_metadata(REF)

    assert metadata == LlmMetadata(platform="openai")


async def test_unknown_fields_and_mistyped_values_are_dropped_not_guessed(
    runtime: Runtime,
) -> None:
    stored = {
        "platform": 7,
        "name": "Custom",
        "inputFormat": ["openai"],
        "models": ["a", {"label": "no id"}, {"id": "b"}, 3],
        "capabilities": "all",
        "somethingNew": {"x": 1},
    }

    metadata = await _registry(runtime, _Exchange("custom", stored=stored)).llm_metadata(REF)

    assert metadata == LlmMetadata(platform=None, name="Custom", models=("a", "b"))


async def test_models_that_are_not_a_list_are_none(runtime: Runtime) -> None:
    stored = {"platform": "openai", "models": "gpt-4o"}

    metadata = await _registry(runtime, _Exchange("custom", stored=stored)).llm_metadata(REF)

    assert metadata is not None
    assert metadata.models == ()


async def test_a_version_without_the_file_is_none(runtime: Runtime) -> None:
    files = _json("custom.graphql.files.json")
    asset = files["data"]["asset"]
    asset["files"] = [f for f in asset["files"] if f["classifier"] != "llm-metadata"]
    fake = _Exchange("custom", files=files)

    assert await _registry(runtime, fake).llm_metadata(REF) is None
    assert [r.url.host for r in fake.requests] == ["anypoint.mulesoft.com"]


async def test_a_missing_version_is_a_registry_error(runtime: Runtime) -> None:
    missing = {
        "data": {"asset": None},
        "errors": [{"message": "nf", "extensions": {"status": 404}}],
    }

    with pytest.raises(RegistryError, match="no asset version"):
        await _registry(runtime, _Exchange("custom", files=missing)).llm_metadata(REF)
