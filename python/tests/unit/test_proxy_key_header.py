"""Bring your own provider key: ``llm_proxy_key_header`` (#367).

A model proxy route with a **Dynamic** API key reads the provider's key from a
request header its DataWeave expression names, so there is no fixed name. With
``llm_proxy_key_header`` and ``llm_proxy_key`` both set, every data-plane request
carries ``{llm_proxy_key_header: llm_proxy_key}`` next to the consumer
credentials; with either unset nothing changes. The header is a credential like
the others: masked when printed and stripped off any origin the SDK has not
checked. A model wallet holds its own provider keys, so ``jwt`` mode never
sends it.

The name is customer-chosen, not a gateway contract, so no wire name or
verified-apis row is involved (§0.3). Driven with ``httpx.MockTransport``.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from donkey_kit.core.auth import StaticToken
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.masking import Secret
from donkey_kit.core.transport import (
    CREDENTIAL_HEADERS,
    DonkeyAsyncClient,
    DonkeyClient,
    credential_headers,
    proxy_auth_headers,
)
from donkey_kit.integrations import ADAPTERS
from donkey_kit.integrations._base import Adapter

_PROXY = "https://proxy.example.internal"
_OTHER = "https://other.example.internal"
_KEY = "sk-provider-key-value"
_HEADER = "X-Provider-Key"


def _cfg(**kw: Any) -> DonkeyConfig:
    base: dict[str, Any] = {
        "llm_proxy_url": f"{_PROXY}/openai/",
        "llm_proxy_client_id": "proxy-client-id",
        "llm_proxy_client_secret": "proxy-client-secret",
        "llm_proxy_key": _KEY,
        "llm_proxy_key_header": _HEADER,
    }
    base.update(kw)
    return DonkeyConfig(**base)


class _Recorder:
    def __init__(self, redirect_to: str | None = None) -> None:
        self.seen: list[httpx.Request] = []
        self._to = redirect_to

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self._to and str(request.url).startswith(_PROXY):
            return httpx.Response(307, headers={"location": f"{self._to}/landed"})
        return httpx.Response(200, json={})


# --- proxy_auth_headers ----------------------------------------------------------


@pytest.mark.parametrize("mode", ["client-id", "bearer"])
def test_both_set_adds_the_key_header(mode: str) -> None:
    headers = proxy_auth_headers(_cfg(llm_proxy_auth=mode))
    assert headers[_HEADER] == _KEY
    assert dict(headers)[_HEADER] == _KEY


def test_client_id_mode_keeps_the_consumer_pair_beside_it() -> None:
    headers = proxy_auth_headers(_cfg())
    assert headers["client_id"] == "proxy-client-id"
    assert headers["client_secret"] == "proxy-client-secret"


@pytest.mark.parametrize(
    "unset", [{"llm_proxy_key_header": None}, {"llm_proxy_key": None}], ids=["no-header", "no-key"]
)
def test_either_unset_leaves_the_headers_unchanged(unset: dict[str, Any]) -> None:
    baseline = proxy_auth_headers(_cfg(llm_proxy_key_header=None, llm_proxy_key=None))
    assert proxy_auth_headers(_cfg(**unset)) == baseline


def test_jwt_mode_never_sends_it() -> None:
    cfg = _cfg(llm_proxy_auth="jwt", llm_proxy_wallet_client_id="wallet-client-id")
    headers = proxy_auth_headers(cfg)
    assert _HEADER not in headers
    assert _KEY not in headers.values()


def test_the_key_prints_masked_however_the_mapping_is_copied() -> None:
    headers = proxy_auth_headers(_cfg())
    for shown in (headers, dict(headers), {**headers}, {"nested": dict(headers)}):
        assert _KEY not in repr(shown)
        assert repr(_HEADER) in repr(shown)


def test_secret_is_the_real_string_everywhere_but_repr() -> None:
    secret = Secret(_KEY)
    assert secret == _KEY
    assert str(secret) == _KEY
    assert f"{secret}" == _KEY
    assert repr(secret) == "'***'"
    assert httpx.Headers({"x": secret})["x"] == _KEY


# --- the wire --------------------------------------------------------------------


def test_the_header_reaches_the_proxy_from_every_adapter_kwargs() -> None:
    """Every adapter's ``connection_kwargs()`` carries the header in its nested
    header mapping (the framework sends it), and prints it masked."""
    cfg = _cfg()
    seen = 0
    for name, spec in ADAPTERS.items():
        cls = getattr(importlib.import_module(spec.module, "donkey_kit.integrations"), spec.cls)
        adapter: Adapter = cls(cfg, DonkeyAsyncClient(cfg, None))
        try:
            kwargs = adapter.connection_kwargs()  # type: ignore[attr-defined]
        except ImportError:  # an optional extra this environment lacks
            continue
        assert _KEY not in repr(kwargs), name
        if "openai_client" in kwargs:
            assert kwargs["openai_client"].default_headers[_HEADER] == _KEY, name
        else:
            assert _HEADER in _header_names(kwargs), name
        seen += 1
    assert seen


def _header_names(value: Any) -> set[str]:
    if not isinstance(value, Mapping):
        return set()
    names = set(value)
    for nested in value.values():
        names |= _header_names(nested)
    return names


async def test_another_origin_never_gets_the_key_header() -> None:
    rec = _Recorder()
    headers = dict(proxy_auth_headers(_cfg()))
    async with DonkeyAsyncClient(_cfg(), None, transport=httpx.MockTransport(rec)) as http:
        await http.get(f"{_PROXY}/openai/responses", headers=headers)
        await http.get(f"{_OTHER}/responses", headers=headers)

    proxy, other = rec.seen
    assert proxy.headers[_HEADER] == _KEY
    assert _HEADER not in other.headers


async def test_a_redirect_off_the_proxy_drops_the_key_header() -> None:
    rec = _Recorder(redirect_to=_OTHER)
    async with DonkeyAsyncClient(_cfg(), None, transport=httpx.MockTransport(rec)) as http:
        await http.get(
            f"{_PROXY}/openai/responses",
            headers=dict(proxy_auth_headers(_cfg())),
            follow_redirects=True,
        )

    first, landed = rec.seen
    assert first.headers[_HEADER] == _KEY
    assert _HEADER not in landed.headers


def test_the_blocking_client_strips_it_too() -> None:
    rec = _Recorder()
    with DonkeyClient(_cfg(), transport=httpx.MockTransport(rec)) as http:
        http.get(f"{_OTHER}/responses", headers={_HEADER: _KEY})
    assert _HEADER not in rec.seen[0].headers


async def test_bearer_mode_sends_it_beside_the_token() -> None:
    rec = _Recorder()
    cfg = _cfg(llm_proxy_auth="bearer")
    async with DonkeyAsyncClient(
        cfg, StaticToken("bearer-token"), transport=httpx.MockTransport(rec)
    ) as http:
        await http.get(f"{_PROXY}/openai/responses", headers=dict(proxy_auth_headers(cfg)))

    assert rec.seen[0].headers[_HEADER] == _KEY
    assert rec.seen[0].headers["authorization"] == "Bearer bearer-token"


def test_credential_headers_add_the_configured_name() -> None:
    assert credential_headers(_cfg()) == CREDENTIAL_HEADERS | {_HEADER.lower()}
    assert credential_headers(_cfg(llm_proxy_key_header=None)) == CREDENTIAL_HEADERS


def test_the_crewai_interceptor_strips_it() -> None:
    pytest.importorskip("crewai")
    from donkey_kit.integrations.crewai import CrewAIAdapter

    cfg = _cfg()
    adapter = CrewAIAdapter(cfg, DonkeyAsyncClient(cfg, None))
    interceptor = adapter.connection_kwargs()["interceptor"]
    request = httpx.Request("GET", f"{_OTHER}/x", headers={_HEADER: _KEY})
    assert _HEADER not in interceptor.on_outbound(request).headers


# --- the name -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["X-API-KEY", "x-api-key", "api-key", "x-goog-api-key", "Provider-Key", "X-Llm-Key"]
)
def test_any_header_the_route_reads_is_accepted(name: str) -> None:
    assert proxy_auth_headers(_cfg(llm_proxy_key_header=name))[name] == _KEY


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("client_id", "consumer's own credentials"),
        ("CLIENT_SECRET", "consumer's own credentials"),
        ("Authorization", "consumer's own credentials"),
        ("X-Client-Id", "consumer's own credentials"),
        ("apikey", "consumer's own credentials"),
        ("Proxy-Authorization", "consumer's own credentials"),
        ("Cookie", "consumer's own credentials"),
        ("Host", "where the request goes"),
        ("X-Forwarded-For", "where the request goes"),
        ("X-HTTP-Method-Override", "method or path"),
        ("Content-Type", "SDK already sets it"),
        ("x-stainless-lang", "SDK already sets it"),
        ("x-cache-skip", "SDK already sets it"),
        ("bad header", "not a valid HTTP header name"),
        ("X-Correlation-Id", "correlation_header already uses it"),
    ],
)
def test_a_name_the_sdk_owns_is_refused(name: str, reason: str) -> None:
    with pytest.raises(ConfigError) as err:
        _cfg(llm_proxy_key_header=name)
    message = str(err.value)
    assert "llm_proxy_key_header" in message
    assert reason in message


def test_a_configured_header_cannot_reuse_it() -> None:
    with pytest.raises(ConfigError, match="already uses it"):
        _cfg(cost_team_header=_HEADER)


def test_it_resolves_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DONKEY_LLM_PROXY_KEY_HEADER", "X-API-KEY")
    monkeypatch.setenv("DONKEY_LLM_PROXY_KEY", _KEY)
    cfg = DonkeyConfig.from_env()
    assert cfg.llm_proxy_key_header == "X-API-KEY"
    assert cfg.source_of("llm_proxy_key_header").kind == "env"


def test_a_bad_name_from_the_project_file_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in [v for v in os.environ if v.startswith(("DONKEY_", "ANYPOINT_"))]:
        monkeypatch.delenv(var)
    project = tmp_path / ".donkey-kit.toml"
    project.write_text('[donkey]\nllm_proxy_key_header = "apikey"\n')
    with pytest.raises(ConfigError, match=r"llm_proxy_key_header.*\.donkey-kit\.toml"):
        DonkeyConfig.resolve(path=project)
