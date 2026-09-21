"""Pure logic that needs no platform verification: cache, tool filtering,
collision resolution, description quality, digest, governance evaluation."""

from __future__ import annotations

from donkey_kit.core.cache import TTLCache
from donkey_kit.provisioning.publish import content_digest
from donkey_kit.registry.governance import Check, GovernanceCriteria, evaluate
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.registry.publication import check_description_quality
from donkey_kit.tools.filter import ToolDescriptor, ToolFilter, resolve_collisions
from donkey_kit.tools.session import ToolSet


def test_ttl_cache_expires() -> None:
    now = [0.0]
    cache: TTLCache[str] = TTLCache(ttl_s=10, clock=lambda: now[0])
    cache.set("k", "v")
    assert cache.get("k") == "v"
    now[0] = 11
    assert cache.get("k") is None


def test_collision_prefixes_only_on_clash() -> None:
    tools = [
        ToolDescriptor(server="hr", name="get_employee"),
        ToolDescriptor(server="fin", name="get_employee"),
        ToolDescriptor(server="hr", name="list_leave"),
    ]
    exposed, name_map = resolve_collisions(tools)
    assert "hr__get_employee" in exposed
    assert "fin__get_employee" in exposed
    assert "list_leave" in exposed  # no clash → not prefixed
    assert name_map["hr__get_employee"] == "get_employee"


def test_tool_filter_allow_deny_predicate() -> None:
    f = ToolFilter(deny=frozenset({"danger"}))
    assert f.accepts(ToolDescriptor("hr", "safe"))
    assert not f.accepts(ToolDescriptor("hr", "danger"))

    f2 = ToolFilter(allow=frozenset({"only"}))
    assert f2.accepts(ToolDescriptor("hr", "only"))
    assert not f2.accepts(ToolDescriptor("hr", "other"))


def test_tool_set_filter_returns_independent_views() -> None:
    server = McpServerHandle(
        ref=AssetRef("com.acme", "hr", "1.0.0"),
        endpoint_url="https://example.com/mcp",
        tool_descriptors=({"name": "read"}, {"name": "write"}),
    )
    tools = ToolSet([server])

    read_only = tools.filter(allow=["read"])
    write_only = tools.filter(allow=["write"])

    assert read_only is not tools
    assert read_only.name_map == {"read": "read"}
    assert write_only.name_map == {"write": "write"}
    assert tools.name_map == {"read": "read", "write": "write"}


def test_description_quality_flags_tautological_and_missing() -> None:
    issues = check_description_quality(
        [
            ("search_employees", "search employees"),  # tautological after normalise
            ("get_leave", None),                        # missing
            ("ok_tool", "Fetch a well documented thing."),
        ]
    )
    kinds = {i.tool: i.kind for i in issues}
    assert kinds["search_employees"] == "tautological"
    assert kinds["get_leave"] == "missing"
    assert "ok_tool" not in kinds


def test_content_digest_is_order_independent() -> None:
    a = content_digest({"b": 1, "a": 2}, {"y": 1, "x": 2})
    b = content_digest({"a": 2, "b": 1}, {"x": 2, "y": 1})
    assert a == b
    assert a.startswith("sha256:")


def test_governance_unknown_check_respects_allow_unknown() -> None:
    checks = [Check("policy", True, "ok"), Check("ruleset", None, "UNKNOWN: 403")]
    strict = evaluate(checks, GovernanceCriteria(allow_unknown=False))
    lenient = evaluate(checks, GovernanceCriteria(allow_unknown=True))
    assert strict.governed is False
    assert lenient.governed is True
    # Reason is retained either way (the governed-state check API).
    assert any("ruleset" in r for r in strict.reasons_failed())
