"""``ToolSet.filter()`` composition, globs, typo guards, collision safety and
descriptor token accounting (#222, BG §2.7)."""

from __future__ import annotations

from typing import Any, cast

import pytest

from donkey_kit.core.errors import ConfigError
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools.filter import (
    ToolDescriptor,
    ToolFilter,
    descriptor_tokens,
    resolve_collisions,
)
from donkey_kit.tools.session import ToolSet


def _server(asset_id: str, *tools: dict[str, object], group: str = "com.acme") -> McpServerHandle:
    return McpServerHandle(
        ref=AssetRef(group, asset_id, "1.0.0"),
        endpoint_url=f"https://example.test/{asset_id}/mcp",
        tool_descriptors=tools,
    )


_HR = _server(
    "hr",
    {"name": "get_employee", "description": "Fetch one employee by id."},
    {"name": "get_ssn", "description": "Fetch an employee's SSN."},
    {"name": "search_employees", "description": "Search employees.", "tags": ["internal"]},
    {"name": "delete_employee", "description": "Delete an employee."},
)
_FIN = _server("fin", {"name": "get_employee", "description": "Fetch payroll for an employee."})


def _names(ts: ToolSet) -> set[str]:
    return set(ts.name_map)


def test_chained_filters_narrow_through_every_call() -> None:
    final = (
        ToolSet([_HR])
        .filter(allow=["get_*", "search_*"])
        .filter(deny=["get_ssn"])
        .filter(predicate=lambda t: "internal" not in t.tags)
    )
    assert _names(final) == {"get_employee"}


def test_a_later_filter_never_brings_back_an_earlier_removal() -> None:
    denied = ToolSet([_HR]).filter(deny=["delete_employee"])
    assert _names(denied.filter(allow=["delete_employee", "get_employee"])) == {"get_employee"}


def test_filtering_leaves_the_parent_unchanged() -> None:
    tools = ToolSet([_HR])
    tools.filter(allow=["get_employee"])
    assert len(tools.name_map) == 4


def test_globs_match_bare_and_server_qualified_names() -> None:
    tools = ToolSet([_HR, _FIN])
    assert _names(tools.filter(deny=["delete_*"])) == {
        "hr__get_employee",
        "fin__get_employee",
        "get_ssn",
        "search_employees",
    }
    assert _names(tools.filter(allow=["fin__*"])) == {"get_employee"}
    assert _names(tools.filter(deny=["hr__get_employee"])) >= {"get_employee"}


def test_glob_matching_is_case_sensitive() -> None:
    f = ToolFilter(allow=frozenset({"get_*"}))
    assert f.accepts(ToolDescriptor("hr", "get_employee"))
    assert not f.accepts(ToolDescriptor("hr", "Get_Employee"))


@pytest.mark.parametrize("arg", ["allow", "deny"])
def test_an_entry_matching_no_tool_is_refused(arg: str) -> None:
    with pytest.raises(ConfigError, match="get_employe\\b") as exc:
        ToolSet([_HR]).filter(**cast(dict[str, Any], {arg: ["get_employe", "get_employee"]}))
    assert exc.value.remediation


def test_an_entry_is_checked_against_every_tool_not_the_filtered_view() -> None:
    # Redundant narrowing is fine; only a name no server exposes is a typo.
    narrowed = ToolSet([_HR]).filter(deny=["get_ssn"]).filter(deny=["get_ssn"])
    assert "get_ssn" not in narrowed.name_map


def test_the_entry_check_waits_for_descriptors_that_are_not_known_yet() -> None:
    unknown = McpServerHandle(ref=AssetRef("com.acme", "crm", "1.0.0"), endpoint_url="https://x")
    assert ToolSet([_HR, unknown]).filter(allow=["anything"]).name_map == {}


@pytest.mark.parametrize("arg", ["allow", "deny"])
def test_a_string_instead_of_a_list_is_refused(arg: str) -> None:
    with pytest.raises(ValueError, match="list of tool names"):
        ToolSet([_HR]).filter(**cast(dict[str, Any], {arg: "get_employee"}))


def test_servers_sharing_a_short_name_raise_instead_of_dropping_a_tool() -> None:
    twin = _server("hr", {"name": "get_employee"}, group="com.other")
    with pytest.raises(ConfigError, match="hr__get_employee"):
        _ = ToolSet([_HR, twin]).name_map


def test_a_tool_named_like_a_prefixed_name_raises() -> None:
    tools = [
        ToolDescriptor("hr", "get_employee"),
        ToolDescriptor("fin", "get_employee"),
        ToolDescriptor("misc", "hr__get_employee"),
    ]
    with pytest.raises(ConfigError, match="hr__get_employee"):
        resolve_collisions(tools)


def test_a_clash_can_be_filtered_away() -> None:
    twin = _server("hr", {"name": "get_employee"}, group="com.other")
    assert "get_employee" not in ToolSet([_HR, twin]).filter(deny=["get_employee"]).name_map


def test_descriptor_tokens_shrink_with_the_filter_and_count_the_schema() -> None:
    schema = {
        "type": "object",
        "properties": {"employee_id": {"type": "string", "description": "The id." * 20}},
        "required": ["employee_id"],
    }
    server = _server(
        "hr",
        {"name": "get_employee", "description": "Fetch one employee.", "inputSchema": schema},
        {"name": "ping", "description": "Ping."},
    )
    tools = ToolSet([server])
    full, one = tools.descriptor_tokens(), tools.filter(allow=["ping"]).descriptor_tokens()
    assert full > one > 0
    assert tools.filter(deny=["*"]).descriptor_tokens() == 0
    assert full - one > len("The id." * 20) // 4


def test_descriptor_tokens_count_the_exposed_name() -> None:
    a = ToolDescriptor("hr", "get_employee", description="Fetch.")
    short = descriptor_tokens({"get_employee": a})
    long = descriptor_tokens({"a_very_long_server_prefix__get_employee": a})
    assert long > short


def test_the_input_schema_is_carried_but_not_compared() -> None:
    with_schema = ToolDescriptor("hr", "x", input_schema={"type": "object"})
    assert with_schema == ToolDescriptor("hr", "x")
    assert hash(with_schema) == hash(ToolDescriptor("hr", "x"))
