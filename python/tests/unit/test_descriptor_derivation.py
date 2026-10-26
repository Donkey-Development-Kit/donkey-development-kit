"""``derive_descriptor()`` against real framework tool objects (BG §2.5, #1024).

The per-framework attributes ``registry/introspect.py`` reads are semi-public,
so they are pinned three ways (docs/verified-apis.md §10):

- each framework's reference tool, built by ``scripts/capture_descriptor_fixtures.py``,
  must derive to the shape the reference function defines, on whatever version
  is installed. The adapter-contract legs install the pinned version, and the
  nightly matrix installs the latest, so an upstream rename fails here;
- the derivation must equal the capture script's own independent read of the
  same attributes;
- at the captured version, both must equal the committed fixture under
  ``tests/fixtures/descriptor/`` byte for byte.

A framework that is not installed skips its rows; the leg that installs it runs
them. Everything below the framework rows needs no framework at all.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from donkey_kit.registry.introspect import DescriptorMismatch, derive_descriptor

_PYTHON_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PYTHON_ROOT / "scripts"))

import capture_descriptor_fixtures as capture  # noqa: E402

_FIXTURES = capture.FIXTURE_DIR


def _installed(key: str) -> str:
    version = capture.installed_version(key)
    if version is None:
        pytest.skip(f"{capture.FRAMEWORKS[key][0]} is not installed")
    return version


def _entrypoint(key: str) -> str:
    return f"capture_descriptor_fixtures:build_{key}"


@pytest.mark.parametrize("key", sorted(capture.FRAMEWORKS))
async def test_reference_tool_derives_to_its_declared_shape(key: str) -> None:
    _installed(key)
    descriptor = await derive_descriptor(_entrypoint(key))

    (tool,) = descriptor["tools"]  # type: ignore[misc]
    assert tool["name"] == "lookup_employee"
    assert "Look up an employee record by ID." in tool["description"]
    schema = tool["inputSchema"]
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"employee_id", "include_manager"}
    assert "employee_id" in schema["required"]


@pytest.mark.parametrize("key", sorted(capture.FRAMEWORKS))
async def test_derivation_equals_the_independent_attribute_read(key: str) -> None:
    _installed(key)
    assert await derive_descriptor(_entrypoint(key)) == (await capture.capture(key))["descriptor"]


@pytest.mark.parametrize("key", sorted(capture.FRAMEWORKS))
async def test_derivation_matches_the_fixture_at_the_captured_version(key: str) -> None:
    version = _installed(key)
    path = _FIXTURES / f"{capture.fixture_name(key, version)}.json"
    fixture = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if fixture is None or fixture["version"] != version:
        pytest.skip(f"no fixture captured at {key} {version}; the shape tests above still ran")

    assert await derive_descriptor(_entrypoint(key)) == fixture["descriptor"]
    assert (await capture.capture(key))["reads"] == fixture["reads"]


async def test_live_mcp_tools_list_equals_object_introspection() -> None:
    _installed("mcp")
    live = await derive_descriptor(_entrypoint("mcp"), mode="auto:live")

    assert live == await derive_descriptor(_entrypoint("mcp"))


# --- framework-free --------------------------------------------------------------


def test_every_fixture_descriptor_is_made_of_its_reads() -> None:
    fixtures = sorted(_FIXTURES.glob("*.json"))
    assert {p.stem.partition("-")[0] for p in fixtures} == set(capture.FRAMEWORKS)
    for path in fixtures:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        name, description, schema = fixture["reads"].values()
        assert fixture["descriptor"] == {
            "tools": [{"name": name, "description": description, "inputSchema": schema}]
        }, path.name


_ANTHROPIC_TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the weather for a city.",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
    {"name": "get_time", "input_schema": {"type": "object", "properties": {}}},
]


@pytest.fixture
def user_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """An importable module standing in for the user's code."""
    source = f"""
        TOOLS = {_ANTHROPIC_TOOLS!r}
        DUPLICATED = TOOLS + TOOLS[:1]

        def build():
            return TOOLS

        def build_with(prefix):
            return TOOLS

        def plain(city: str) -> str:
            '''Not a framework tool.'''
            return city

        class Custom:
            name = "custom"
    """
    (tmp_path / "ddk_user_tools.py").write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "ddk_user_tools", raising=False)
    return "ddk_user_tools"


_EXPECTED: dict[str, Any] = {
    "tools": [
        {
            "name": "get_weather",
            "description": "Get the weather for a city.",
            "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
        {
            "name": "get_time",
            "description": None,
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
}


async def test_anthropic_tool_params_derive_in_order(user_module: str) -> None:
    assert await derive_descriptor(f"{user_module}:TOOLS") == _EXPECTED


async def test_zero_argument_factory_is_called(user_module: str) -> None:
    assert await derive_descriptor(f"{user_module}:build") == _EXPECTED


async def test_factory_with_arguments_is_refused(user_module: str) -> None:
    with pytest.raises(TypeError, match="takes arguments"):
        await derive_descriptor(f"{user_module}:build_with")


async def test_plain_function_is_never_re_derived(user_module: str) -> None:
    with pytest.raises(TypeError, match="never re-derives"):
        await derive_descriptor(f"{user_module}:plain")


async def test_unverified_framework_object_stays_blocked(user_module: str) -> None:
    with pytest.raises(NotImplementedError, match=r"^blocked on verification:.*Custom"):
        await derive_descriptor(f"{user_module}:Custom")


async def test_duplicate_tool_names_are_refused(user_module: str) -> None:
    with pytest.raises(ValueError, match="more than one tool named get_weather"):
        await derive_descriptor(f"{user_module}:DUPLICATED")


async def test_check_passes_on_the_committed_descriptor(user_module: str, tmp_path: Path) -> None:
    committed = tmp_path / "descriptor.json"
    committed.write_text(json.dumps(_EXPECTED), encoding="utf-8")

    result = await derive_descriptor(f"{user_module}:TOOLS", mode="auto:check", against=committed)

    assert result == _EXPECTED


async def test_check_fails_on_drift(user_module: str, tmp_path: Path) -> None:
    committed = tmp_path / "descriptor.json"
    committed.write_text(json.dumps({"tools": _EXPECTED["tools"][:1]}), encoding="utf-8")

    with pytest.raises(DescriptorMismatch, match="differs from"):
        await derive_descriptor(f"{user_module}:TOOLS", mode="auto:check", against=committed)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"mode": "auto:check"}, "against= is required"),
        ({"against": "descriptor.json"}, "against= is required"),
        ({"mode": "auto:nope"}, "mode must be one of"),
    ],
)
async def test_argument_errors(user_module: str, kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        await derive_descriptor(f"{user_module}:TOOLS", **kwargs)


@pytest.mark.parametrize("entrypoint", ["ddk_user_tools", ":TOOLS", "ddk_user_tools:"])
async def test_malformed_entrypoint(user_module: str, entrypoint: str) -> None:
    with pytest.raises(ValueError, match="module:attribute"):
        await derive_descriptor(entrypoint)


async def test_live_needs_an_mcp_server(user_module: str) -> None:
    with pytest.raises(TypeError, match="needs an MCP server"):
        await derive_descriptor(f"{user_module}:TOOLS", mode="auto:live")


async def test_static_stays_blocked() -> None:
    with pytest.raises(NotImplementedError, match=r"^blocked on verification:.*auto:static"):
        await derive_descriptor("json:dumps", mode="auto:static")
