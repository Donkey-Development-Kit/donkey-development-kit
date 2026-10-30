"""The business group set per request, not only per ``Donkey`` (#310, BG §1.7).

``donkey.attribution(business_group=...)`` and ``donkey.run(business_group=...)``
bind a group for their block. The transport reads it on every send, so one
process serving several tenants attributes each call to its own group through
one client. The header name stays the Unverified placeholder (#21). No network:
every request is answered by ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import os
import warnings
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import _verify
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.correlation import current_business_group
from donkey_kit.core.cost import CostTags
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.transport import attribution_headers

_PROXY = "https://gw.example.internal/openai-sdk/"
_CONTROL_PLANE = "https://anypoint.example.internal"
_HEADER = _verify.ATTRIBUTION_BUSINESS_GROUP_HEADER.placeholder
_URL = f"{_PROXY}chat/completions"


@pytest.fixture(autouse=True)
def _quiet_placeholder() -> Iterator[None]:
    """The header name is a placeholder that warns once; keep that out of the way."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", _verify.UnverifiedValueWarning)
        yield


def _cfg(**overrides: object) -> DonkeyConfig:
    fields: dict[str, object] = {
        "client_id": "cp-id",
        "client_secret": "cp-secret",
        "base_url": _CONTROL_PLANE,
        "llm_proxy_url": _PROXY,
        "llm_proxy_client_id": "cid",
        "llm_proxy_client_secret": "csecret",
        "correlation_header": "x-correlation-id",
        "business_group": "configured",
    }
    fields.update(overrides)
    return DonkeyConfig(**fields)  # type: ignore[arg-type]


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

    @property
    def groups(self) -> list[str | None]:
        return [r.headers.get(_HEADER) for r in self.requests]


def _wire(donkey: Donkey) -> _Recorder:
    recorder = _Recorder()
    donkey._http.governed_transport.replace_inner(httpx.MockTransport(recorder))
    return recorder


# --- the override applies inside the block only ------------------------------


async def test_attribution_overrides_the_group_for_its_block_only() -> None:
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)
        await donkey._http.post(_URL)
        with donkey.attribution(business_group="tenant-a"):
            await donkey._http.post(_URL)
        await donkey._http.post(_URL)
    assert seen.groups == ["configured", "tenant-a", "configured"]


async def test_async_with_works_as_well_as_with() -> None:
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)
        async with donkey.attribution(business_group="tenant-a"):
            await donkey._http.post(_URL)
    assert seen.groups == ["tenant-a"]


async def test_the_override_applies_with_no_configured_group() -> None:
    async with Donkey(_cfg(business_group=None)) as donkey:
        seen = _wire(donkey)
        await donkey._http.post(_URL)
        with donkey.attribution(business_group="tenant-a"):
            await donkey._http.post(_URL)
    assert seen.groups == [None, "tenant-a"]


async def test_run_takes_a_business_group_too() -> None:
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)
        async with donkey.run(id="r1", business_group="tenant-a"):
            await donkey._http.post(_URL)
        await donkey._http.post(_URL)
    assert seen.groups == ["tenant-a", "configured"]
    assert seen.requests[0].headers["x-correlation-id"] == "r1"


async def test_governed_takes_a_business_group_too() -> None:
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)

        @donkey.governed(business_group="tenant-a")
        async def handle() -> None:
            await donkey._http.post(_URL)

        await handle()
    assert seen.groups == ["tenant-a"]


# --- nesting and concurrency -------------------------------------------------


async def test_nesting_restores_the_outer_group_on_exit() -> None:
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)
        with donkey.attribution(business_group="outer"):
            await donkey._http.post(_URL)
            with donkey.attribution(business_group="inner"):
                await donkey._http.post(_URL)
            await donkey._http.post(_URL)
    assert seen.groups == ["outer", "inner", "outer"]
    assert current_business_group() is None


async def test_concurrent_tasks_never_see_each_others_group() -> None:
    tenants = [f"tenant-{i}" for i in range(12)]
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)

        async def handle(tenant: str) -> None:
            with donkey.attribution(business_group=tenant):
                await asyncio.sleep(0)  # let the other tenants interleave
                await donkey._http.post(_URL, headers={"x-tenant": tenant})

        await asyncio.gather(*(handle(t) for t in tenants))
    assert sorted(r.headers["x-tenant"] for r in seen.requests) == sorted(tenants)
    assert all(r.headers[_HEADER] == r.headers["x-tenant"] for r in seen.requests)


async def test_attribution_composes_with_a_run_and_keeps_its_correlation_id() -> None:
    """One run spanning two cost centres: the group changes, the run ID does not."""
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)
        async with donkey.run(id="job-1"):
            with donkey.attribution(business_group="risk-eu"):
                await donkey._http.post(_URL)
            with donkey.attribution(business_group="risk-us"):
                await donkey._http.post(_URL)
    assert seen.groups == ["risk-eu", "risk-us"]
    assert {r.headers["x-correlation-id"] for r in seen.requests} == {"job-1"}


async def test_attribution_cost_tags_merge_over_the_enclosing_run() -> None:
    async with Donkey(_cfg(send_cost_headers=True, cost=CostTags(env="prod"))) as donkey:
        seen = _wire(donkey)
        async with donkey.run(project="atlas"):
            with donkey.attribution(team="risk"):
                await donkey._http.post(_URL)
            await donkey._http.post(_URL)
    inner, after = seen.requests
    assert inner.headers["x-anypoint-cost-team"] == "risk"
    assert inner.headers["x-anypoint-cost-project"] == "atlas"  # kept from the run
    assert inner.headers["x-anypoint-cost-env"] == "prod"  # kept from config
    assert "x-anypoint-cost-team" not in after.headers


async def test_an_empty_attribution_leaves_the_outer_binding_in_place() -> None:
    async with Donkey(_cfg()) as donkey:
        seen = _wire(donkey)
        with donkey.attribution(business_group="outer"), donkey.attribution():
            await donkey._http.post(_URL)
    assert seen.groups == ["outer"]


# --- precedence: scope, then Donkey config, then env, then file, then unset ---


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in list(os.environ):
        if var.startswith(("ANYPOINT_", "DONKEY_")):
            monkeypatch.delenv(var)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path / ".donkey-kit.toml"


async def _groups_for(cfg: DonkeyConfig) -> list[str | None]:
    """The group sent outside and then inside a scoped override."""
    base = _cfg().with_overrides(business_group=None)
    merged = base.with_overrides(business_group=cfg.business_group)
    async with Donkey(merged) as donkey:
        seen = _wire(donkey)
        await donkey._http.post(_URL)
        with donkey.attribution(business_group="scoped"):
            await donkey._http.post(_URL)
    return seen.groups


async def test_precedence_is_scope_then_code_then_env_then_file_then_unset(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert await _groups_for(DonkeyConfig.resolve()) == [None, "scoped"]

    clean_env.write_text('[donkey]\nbusiness_group = "from-file"\n')
    assert await _groups_for(DonkeyConfig.resolve()) == ["from-file", "scoped"]

    monkeypatch.setenv("DONKEY_BUSINESS_GROUP", "from-env")
    assert await _groups_for(DonkeyConfig.resolve()) == ["from-env", "scoped"]

    in_code = DonkeyConfig.resolve(business_group="from-code")
    assert await _groups_for(in_code) == ["from-code", "scoped"]


# --- both transports, and only the data plane --------------------------------


async def test_the_blocking_client_honours_the_override() -> None:
    recorder = _Recorder()
    async with Donkey(_cfg()) as donkey:
        client = donkey._sync_http_client()
        client.governed_transport.replace_inner(httpx.MockTransport(recorder))
        client.post(_URL)
        with donkey.attribution(business_group="tenant-a"):
            client.post(_URL)
    assert recorder.groups == ["configured", "tenant-a"]


async def test_a_control_plane_call_never_carries_the_group() -> None:
    token_endpoint, platform = _Recorder(), _Recorder()
    async with Donkey(_cfg()) as donkey:
        assert donkey._owned_auth_http is not None
        donkey._owned_auth_http.governed_transport.replace_inner(
            httpx.MockTransport(token_endpoint)
        )
        donkey.registry._http.governed_transport.replace_inner(httpx.MockTransport(platform))
        with donkey.attribution(business_group="tenant-a"):
            await donkey.registry._http.get(f"{_CONTROL_PLANE}/exchange/api/v2/assets")
    assert platform.groups == [None]
    assert token_endpoint.groups == [None]


def test_the_default_headers_snapshot_carries_the_configured_group() -> None:
    """The documented degradation: a ``default_headers`` snapshot was taken from
    config and cannot follow a later scope."""
    cfg = _cfg()
    donkey = Donkey(cfg)
    with donkey.attribution(business_group="tenant-a"):
        assert attribution_headers(cfg)[_HEADER] == "configured"


# --- validation at the call site ---------------------------------------------


@pytest.mark.parametrize("value", ["", "a\nX-Injected: 1", "x" * 257])
def test_an_unsafe_group_is_refused_where_it_is_set(value: str) -> None:
    donkey = Donkey(_cfg())
    with pytest.raises(ConfigError, match="business_group"):
        donkey.attribution(business_group=value)
    with pytest.raises(ConfigError, match="business_group"):
        donkey.run(business_group=value)
