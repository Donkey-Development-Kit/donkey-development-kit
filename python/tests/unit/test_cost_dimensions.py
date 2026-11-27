"""Custom cost dimensions beyond the fixed four (#316, BG §1.7).

A client declares its custom dimension names once, at construction
(``cost_dimensions``), and sets their values per request through the same
``donkey.attribution(...)`` scope as the business group (#310). An undeclared name
raises ``ConfigError`` at the call that passed it, naming it. A dimension not
declared high-cardinality has a distinct-value ceiling. Values reach the
``donkey.cost.<name>`` span attribute, and the request headers only with
``send_cost_headers`` (the gateway reads no cost header, docs/verified-apis.md §3).
No network: every request is answered by ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import pytest

from donkey_kit import Donkey
from donkey_kit.core import cost as cost_module
from donkey_kit.core import telemetry
from donkey_kit.core.config import DonkeyConfig
from donkey_kit.core.cost import (
    MAX_COST_DIMENSIONS,
    CostTags,
    DimensionGuard,
    dimension_header,
)
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.identity import capture_requester

_PROXY = "https://gw.example.internal/openai-sdk/"
_URL = f"{_PROXY}chat/completions"
_DIMS = ("cost_center", "legal_entity", "jurisdiction")


def _cfg(**overrides: object) -> DonkeyConfig:
    fields: dict[str, object] = {
        "llm_proxy_url": _PROXY,
        "llm_proxy_client_id": "cid",
        "llm_proxy_client_secret": "csecret",
        "cost_dimensions": _DIMS,
    }
    fields.update(overrides)
    return DonkeyConfig(**fields)  # type: ignore[arg-type]


def _wire(donkey: Donkey) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})

    donkey._http.governed_transport.replace_inner(httpx.MockTransport(handler))
    return seen


# --- the declared set, validated at construction ------------------------------


def test_the_declared_set_is_kept_as_a_tuple_whatever_sequence_is_passed() -> None:
    cfg = _cfg(
        cost_dimensions=["cost_center", "case_id"], high_cardinality_cost_dimensions=["case_id"]
    )
    assert cfg.cost_dimensions == ("cost_center", "case_id")
    assert cfg.high_cardinality_cost_dimensions == ("case_id",)
    assert hash(cfg.cost_dimensions)  # frozen and hashable, like every other field


def test_nothing_is_declared_by_default() -> None:
    cfg = DonkeyConfig()
    assert cfg.cost_dimensions == ()
    assert cfg.high_cardinality_cost_dimensions == ()


@pytest.mark.parametrize(
    "names",
    [
        ["Cost_Center"],  # uppercase
        ["cost-center"],  # hyphen
        ["1st"],  # leading digit
        [""],  # empty
        ["x" * 33],  # too long
        ["team"],  # a fixed dimension
        ["enduser"],  # would read as donkey.cost.enduser.* on the span
        ["cost_center", "cost_center"],  # repeated
        "cost_center",  # a bare string in code, not a list
    ],
)
def test_an_invalid_declared_set_is_refused_at_construction(names: object) -> None:
    with pytest.raises(ConfigError, match="cost_dimensions"):
        _cfg(cost_dimensions=names)


def test_the_count_ceiling_is_enforced() -> None:
    names = [f"d{i}" for i in range(MAX_COST_DIMENSIONS + 1)]
    with pytest.raises(ConfigError, match=f"at most {MAX_COST_DIMENSIONS}"):
        _cfg(cost_dimensions=names)
    assert _cfg(cost_dimensions=names[:-1]).cost_dimensions == tuple(names[:-1])


def test_a_high_cardinality_opt_in_must_name_a_declared_dimension() -> None:
    with pytest.raises(ConfigError, match="case_id"):
        _cfg(high_cardinality_cost_dimensions=["case_id"])


def test_configured_values_for_an_undeclared_dimension_are_refused() -> None:
    with pytest.raises(ConfigError, match="'region_code'"):
        _cfg(cost=CostTags(dimensions=(("region_code", "EMEA"),)))
    assert _cfg(cost=CostTags(dimensions=(("cost_center", "CC-1"),))).cost.dimensions == (
        ("cost_center", "CC-1"),
    )


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for var in [v for v in os.environ if v.startswith(("DONKEY_", "ANYPOINT_"))]:
        monkeypatch.delenv(var)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_environment_declares_a_comma_separated_set(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_COST_DIMENSIONS", "cost_center, case_id")
    monkeypatch.setenv("DONKEY_HIGH_CARDINALITY_COST_DIMENSIONS", "case_id")
    cfg = DonkeyConfig.resolve()
    assert cfg.cost_dimensions == ("cost_center", "case_id")
    assert cfg.high_cardinality_cost_dimensions == ("case_id",)


def test_a_config_file_declares_a_list(clean_env: Path) -> None:
    (clean_env / ".donkey-kit.toml").write_text(
        '[donkey]\ncost_dimensions = ["cost_center", "legal_entity"]\n', encoding="utf-8"
    )
    assert DonkeyConfig.resolve().cost_dimensions == ("cost_center", "legal_entity")


def test_a_bad_name_in_the_environment_says_where_it_was_set(
    clean_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DONKEY_COST_DIMENSIONS", "cost_center,Bad-Name")
    with pytest.raises(ConfigError, match="DONKEY_COST_DIMENSIONS"):
        DonkeyConfig.resolve()


def test_from_env_takes_the_declared_set_as_a_keyword(clean_env: Path) -> None:
    donkey = Donkey.from_env(cost_dimensions=["cost_center"])
    assert donkey.config.cost_dimensions == ("cost_center",)


# --- undeclared names raise where they are passed ----------------------------


def test_an_undeclared_dimension_raises_naming_the_typo() -> None:
    donkey = Donkey(_cfg())
    with pytest.raises(ConfigError) as caught:
        donkey.attribution(cost_centre="CC-4471")
    message = str(caught.value)
    assert "'cost_centre'" in message
    assert "did you mean 'cost_center'?" in message
    assert "donkey.attribution()" in message


def test_every_undeclared_name_is_listed_at_once() -> None:
    donkey = Donkey(_cfg())
    with pytest.raises(ConfigError, match="'region'.*'tenant'"):
        donkey.attribution(tenant="a", region="b", cost_center="CC-1")


def test_with_nothing_declared_a_custom_keyword_raises() -> None:
    donkey = Donkey(_cfg(cost_dimensions=()))
    with pytest.raises(ConfigError, match=r"'cost_center'.*Declared: none"):
        donkey.attribution(cost_center="CC-1")


def test_run_and_run_from_headers_check_the_name_too() -> None:
    donkey = Donkey(_cfg())
    with pytest.raises(ConfigError, match=r"donkey\.run\(\).*'cost_centre'"):
        donkey.run(cost_centre="CC-1")
    with pytest.raises(ConfigError, match=r"donkey\.run_from_headers\(\)"):
        donkey.run_from_headers({}, cost_centre="CC-1")


@pytest.mark.parametrize("value", ["", "x" * 257, "a\nb", 42])
def test_an_unsafe_value_is_refused_where_it_is_set(value: object) -> None:
    donkey = Donkey(_cfg())
    with pytest.raises(ConfigError, match="cost_center"):
        donkey.attribution(cost_center=value)  # type: ignore[arg-type]


def test_a_none_value_leaves_the_dimension_unset() -> None:
    donkey = Donkey(_cfg())
    scope = donkey.attribution(cost_center=None)
    assert scope._cost is None  # nothing set, so nothing to merge


# --- values reach the headers (opt-in) and share the business group's scope ----


async def test_values_go_out_as_headers_only_with_send_cost_headers() -> None:
    for send, expected in ((True, "CC-4471"), (False, None)):
        async with Donkey(_cfg(send_cost_headers=send)) as donkey:
            seen = _wire(donkey)
            with donkey.attribution(cost_center="CC-4471"):
                await donkey._http.post(_URL)
        assert seen[0].headers.get("x-anypoint-cost-cost-center") == expected


async def test_one_scope_carries_the_business_group_and_the_dimensions() -> None:
    async with Donkey(_cfg(send_cost_headers=True)) as donkey:
        seen = _wire(donkey)
        with donkey.attribution(business_group="risk-eu", team="risk", cost_center="CC-1"):
            await donkey._http.post(_URL)
    (request,) = seen
    assert request.headers["x-anypoint-business-group"] == "risk-eu"
    assert request.headers["x-anypoint-cost-team"] == "risk"
    assert request.headers["x-anypoint-cost-cost-center"] == "CC-1"


async def test_dimensions_merge_per_name_over_an_enclosing_run() -> None:
    async with Donkey(_cfg(send_cost_headers=True)) as donkey:
        seen = _wire(donkey)
        async with donkey.run(cost_center="CC-1", legal_entity="ACME-UK"):
            with donkey.attribution(cost_center="CC-2"):
                await donkey._http.post(_URL)
            await donkey._http.post(_URL)
        await donkey._http.post(_URL)
    inner, after, outside = seen
    assert inner.headers["x-anypoint-cost-cost-center"] == "CC-2"
    assert inner.headers["x-anypoint-cost-legal-entity"] == "ACME-UK"  # kept from the run
    assert after.headers["x-anypoint-cost-cost-center"] == "CC-1"  # restored on exit
    assert "x-anypoint-cost-cost-center" not in outside.headers


async def test_concurrent_tasks_never_see_each_others_dimensions() -> None:
    async with Donkey(_cfg(send_cost_headers=True)) as donkey:
        seen = _wire(donkey)

        async def call(center: str) -> None:
            with donkey.attribution(cost_center=center):
                await asyncio.sleep(0)
                await donkey._http.post(_URL, headers={"x-expect": center})

        await asyncio.gather(*(call(f"CC-{i}") for i in range(5)))
    for request in seen:
        assert request.headers["x-anypoint-cost-cost-center"] == request.headers["x-expect"]


def test_a_captured_requester_carries_the_dimensions_to_a_deferred_call() -> None:
    donkey = Donkey(_cfg())
    with donkey.attribution(cost_center="CC-9"):
        requester = capture_requester()
    assert requester.cost is not None
    assert requester.cost.dimensions == (("cost_center", "CC-9"),)


# --- values reach the span ---------------------------------------------------


async def test_values_are_recorded_as_donkey_cost_span_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_tracer", lambda: provider.get_tracer("test"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"model": "gpt-4o", "usage": {"input_tokens": 1}})

    async with Donkey(_cfg(cost=CostTags(team="risk"))) as donkey:
        donkey._http.governed_transport.replace_inner(httpx.MockTransport(handler))
        with donkey.attribution(cost_center="CC-4471", jurisdiction="EU"):
            await donkey._http.post(_URL, json={"model": "gpt-4o"})

    (span,) = exporter.get_finished_spans()
    attrs = dict(span.attributes or {})
    assert attrs["donkey.cost.cost_center"] == "CC-4471"
    assert attrs["donkey.cost.jurisdiction"] == "EU"
    assert attrs["donkey.cost.team"] == "risk"
    assert "donkey.cost.legal_entity" not in attrs


def test_build_genai_attributes_places_custom_dimensions_under_donkey_cost() -> None:
    attrs = telemetry.build_genai_attributes(
        cost_team="risk", cost_dimensions=(("cost_center", "CC-1"),)
    )
    assert attrs["donkey.cost.team"] == "risk"
    assert attrs["donkey.cost.cost_center"] == "CC-1"


# --- the cardinality guard ---------------------------------------------------


def test_a_dimension_past_the_ceiling_raises_and_names_the_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cost_module, "DIMENSION_CARDINALITY_CEILING", 3)
    donkey = Donkey(_cfg())
    for center in ("CC-1", "CC-2", "CC-3", "CC-1"):  # a seen value never counts twice
        donkey.attribution(cost_center=center)
    with pytest.raises(ConfigError) as caught:
        donkey.attribution(cost_center="CC-4")
    assert "'cost_center'" in str(caught.value)
    assert "high_cardinality_cost_dimensions=['cost_center']" in str(caught.value)
    donkey.attribution(cost_center="CC-2")  # values already seen keep working
    donkey.attribution(legal_entity="ACME-UK")  # each dimension counts on its own


def test_a_high_cardinality_dimension_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_module, "DIMENSION_CARDINALITY_CEILING", 3)
    donkey = Donkey(_cfg(cost_dimensions=["case_id"], high_cardinality_cost_dimensions=["case_id"]))
    for i in range(10):
        donkey.attribution(case_id=f"case-{i}")


def test_the_ceiling_is_per_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cost_module, "DIMENSION_CARDINALITY_CEILING", 1)
    first, second = Donkey(_cfg()), Donkey(_cfg())
    first.attribution(cost_center="CC-1")
    second.attribution(cost_center="CC-2")
    with pytest.raises(ConfigError):
        first.attribution(cost_center="CC-2")


def test_the_guard_counts_only_set_values() -> None:
    guard = DimensionGuard(["cost_center"])
    assert guard.tags({"cost_center": None}, where="test").is_empty


# --- CostTags and the header name --------------------------------------------


def test_cost_tags_dimensions_are_sorted_and_merge_per_name() -> None:
    outer = CostTags(team="risk", dimensions=(("legal_entity", "UK"), ("cost_center", "CC-1")))
    assert outer.dimensions == (("cost_center", "CC-1"), ("legal_entity", "UK"))
    merged = outer.merge(CostTags(dimensions=(("cost_center", "CC-2"),)))
    assert merged == CostTags(
        team="risk", dimensions=(("cost_center", "CC-2"), ("legal_entity", "UK"))
    )
    assert outer.merge(CostTags()) is outer


def test_cost_tags_with_only_dimensions_are_not_empty() -> None:
    tags = CostTags(dimensions=(("cost_center", "CC-1"),))
    assert not tags.is_empty
    assert list(tags.items()) == []  # items() stays the fixed four
    assert tags.span_kwargs() == {"cost_dimensions": (("cost_center", "CC-1"),)}


def test_cost_tags_refuse_a_malformed_dimension_name() -> None:
    with pytest.raises(ConfigError, match="X-Evil"):
        CostTags(dimensions=(("X-Evil\nHost", "v"),))
    with pytest.raises(ConfigError, match="twice"):
        CostTags(dimensions=(("a", "1"), ("a", "2")))


@pytest.mark.parametrize(
    ("name", "header"),
    [
        ("cost_center", "X-Anypoint-Cost-Cost-Center"),
        ("jurisdiction", "X-Anypoint-Cost-Jurisdiction"),
        ("legal_entity_2", "X-Anypoint-Cost-Legal-Entity-2"),
    ],
)
def test_the_header_name_is_the_prefix_plus_the_title_cased_name(name: str, header: str) -> None:
    assert dimension_header(name) == header
