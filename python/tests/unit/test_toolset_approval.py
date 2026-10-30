"""``ToolSet.require_approval()``: approval on discovered MCP tools (BG §2.3, #1153).

A rule declared once on the ``ToolSet`` gates the matching tools: the binding
seam (``ToolSet._bind_tool``) wraps the tool's invoker in the same gate
``@donkey.tool(approval="required")`` uses, so a call raises the same
``ApprovalRequired``, or pauses through a native hook, and resolves through
``donkey.approvals``. The invokers here stand in for the per-framework binding
(#221, docs/verified-apis.md §9).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pytest

from donkey_kit import Approval, ApprovalRequired, Approvals, ConfigError, Donkey, DonkeyConfig
from donkey_kit.registry.criteria import GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools.filter import ToolDescriptor
from donkey_kit.tools.session import ToolSet

_REFUND_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice": {"type": "string"},
        "amount": {"type": "number"},
        "reason": {"type": "string", "default": "customer request"},
        "notify": {"type": "boolean"},
    },
    "required": ["invoice", "amount"],
}


def _cfg() -> DonkeyConfig:
    return DonkeyConfig(
        llm_proxy_url="https://proxy",
        llm_proxy_client_id="cid",
        llm_proxy_client_secret="csecret",
        max_retries=0,
    )


def _server(asset_id: str, *names: str) -> McpServerHandle:
    return McpServerHandle(
        ref=AssetRef("com.acme", asset_id, "1.0.0"),
        endpoint_url=f"https://{asset_id}.example.test/mcp",
        tool_descriptors=tuple({"name": n} for n in names),
        governance=GovernanceReport(governed=True, checks=[]),
    )


@pytest.fixture
def registry() -> Approvals:
    """The ``donkey.approvals`` of the client whose discovery built the set."""
    return Approvals()


def _billing(registry: Approvals | None) -> ToolSet:
    return ToolSet([_server("billing", "issue_refund", "get_invoice")], _approval_registry=registry)


def _tool(server: str, name: str) -> ToolDescriptor:
    return ToolDescriptor(server=server, name=name)


class _Invoker:
    """Stands in for a binding's call of one MCP tool."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **arguments: Any) -> str:
        self.calls.append(arguments)
        return "refunded"


def test_a_gated_tool_raises_approval_required_and_does_not_run(registry: Approvals) -> None:
    tools = _billing(registry).require_approval(tools=["issue_refund"], risk="financial")
    invoke = _Invoker()
    call = tools._bind_tool(
        "issue_refund", _tool("billing", "issue_refund"), invoke, schema=_REFUND_SCHEMA
    )

    with pytest.raises(ApprovalRequired) as caught:
        call(invoice="INV-7", amount=40.0)

    approval = caught.value.approval
    assert approval.tool == "issue_refund"
    assert approval.risk == "financial"
    assert dict(approval.arguments) == {
        "invoice": "INV-7",
        "amount": 40.0,
        "reason": "customer request",
        "notify": None,
    }
    assert invoke.calls == []


def test_resolving_runs_the_call_with_only_the_arguments_passed(registry: Approvals) -> None:
    tools = _billing(registry).require_approval(tools=["issue_refund"])
    invoke = _Invoker()
    call = tools._bind_tool(
        "issue_refund", _tool("billing", "issue_refund"), invoke, schema=_REFUND_SCHEMA
    )

    with pytest.raises(ApprovalRequired) as caught:
        call(invoice="INV-7", amount=40.0)

    assert registry.resolve(caught.value.approval.id, approved_by="m.keller") == "refunded"
    assert invoke.calls == [{"invoice": "INV-7", "amount": 40.0}]


async def test_an_async_invoker_is_gated_and_resolved(registry: Approvals) -> None:
    sent: list[dict[str, Any]] = []

    async def invoke(**arguments: Any) -> str:
        sent.append(arguments)
        return "refunded"

    tools = _billing(registry).require_approval(tools=["issue_refund"])
    call = tools._bind_tool("issue_refund", _tool("billing", "issue_refund"), invoke)

    with pytest.raises(ApprovalRequired) as caught:
        await call(invoice="INV-7")

    assert sent == []
    assert await registry.resolve(caught.value.approval.id, approved_by="m.keller") == ("refunded")
    assert sent == [{"invoice": "INV-7"}]


def test_an_ungated_tool_is_bound_unwrapped(registry: Approvals) -> None:
    tools = _billing(registry).require_approval(tools=["issue_refund"])
    invoke = _Invoker()

    assert tools._bind_tool("get_invoice", _tool("billing", "get_invoice"), invoke) is invoke


def test_a_native_pause_is_tried_first(registry: Approvals) -> None:
    # The LangGraph interrupt() hook reaches a gated discovered tool as it does
    # a @donkey.tool (#212): the hook decides, and the call runs under it.
    seen: list[tuple[str, Mapping[str, Any], str | None]] = []

    def native(tool: str, arguments: Mapping[str, Any], risk: str | None) -> Approval:
        seen.append((tool, dict(arguments), risk))
        return Approval(
            id="lg-1",
            tool=tool,
            arguments=arguments,
            risk=risk,
            correlation_id="run-1",
            requested_at=0.0,
            expires_at=math.inf,
        )

    tools = ToolSet(
        [_server("billing", "issue_refund")], _natives=(native,), _approval_registry=registry
    ).require_approval(tools=["issue_refund"], risk="financial")
    invoke = _Invoker()
    call = tools._bind_tool("issue_refund", _tool("billing", "issue_refund"), invoke)

    assert call(invoice="INV-7") == "refunded"
    assert seen == [("issue_refund", {"arguments": {"invoice": "INV-7"}}, "financial")]
    assert invoke.calls == [{"invoice": "INV-7"}]


def test_a_bare_name_gates_the_tool_on_every_server(registry: Approvals) -> None:
    tools = ToolSet(
        [_server("billing", "issue_refund"), _server("legacy", "issue_refund")],
        _approval_registry=registry,
    ).require_approval(tools=["issue_refund"])

    for server in ("billing", "legacy"):
        call = tools._bind_tool(
            f"{server}__issue_refund", _tool(server, "issue_refund"), _Invoker()
        )
        with pytest.raises(ApprovalRequired):
            call()


def test_a_prefixed_name_gates_one_server_only(registry: Approvals) -> None:
    tools = ToolSet(
        [_server("billing", "issue_refund"), _server("legacy", "issue_refund")],
        _approval_registry=registry,
    ).require_approval(tools=["billing__issue_refund"])
    legacy = _Invoker()

    with pytest.raises(ApprovalRequired) as caught:
        tools._bind_tool("billing__issue_refund", _tool("billing", "issue_refund"), _Invoker())()
    assert caught.value.approval.tool == "billing__issue_refund"
    assert tools._bind_tool("legacy__issue_refund", _tool("legacy", "issue_refund"), legacy) is (
        legacy
    )


def test_the_last_matching_rule_sets_risk_and_timeout(registry: Approvals) -> None:
    tools = (
        _billing(registry)
        .require_approval(tools=["issue_refund", "get_invoice"], risk="low", timeout="1h")
        .require_approval(tools=["issue_refund"], risk="financial", timeout="5m")
    )

    with pytest.raises(ApprovalRequired) as caught:
        tools._bind_tool("issue_refund", _tool("billing", "issue_refund"), _Invoker())()
    approval = caught.value.approval
    assert approval.risk == "financial"
    assert approval.expires_at - approval.requested_at == pytest.approx(300)


def test_filter_keeps_the_rules_and_require_approval_keeps_the_filter(registry: Approvals) -> None:
    gated = _billing(registry).require_approval(tools=["issue_refund"])
    narrowed = gated.filter(allow=["issue_refund"])

    assert narrowed.name_map == {"issue_refund": "issue_refund"}
    with pytest.raises(ApprovalRequired):
        narrowed._bind_tool("issue_refund", _tool("billing", "issue_refund"), _Invoker())()
    assert narrowed.require_approval(tools=["issue_refund"]).name_map == narrowed.name_map
    # require_approval returns a new view: the original set is untouched.
    assert gated._approvals != narrowed.require_approval(tools=["get_invoice"])._approvals


async def test_a_name_no_server_exposes_is_refused_at_bind_time(registry: Approvals) -> None:
    tools = _billing(registry).require_approval(tools=["issue_refnd", "legacy__issue_refund"])

    with pytest.raises(ConfigError, match="issue_refnd, legacy__issue_refund") as caught:
        await tools.describe()
    assert "name_map" in caught.value.remediation


async def test_a_gated_tool_removed_by_filter_is_not_a_typo(registry: Approvals) -> None:
    tools = (
        _billing(registry).require_approval(tools=["issue_refund"]).filter(deny=["issue_refund"])
    )

    await tools.describe()
    assert tools.name_map == {"get_invoice": "get_invoice"}


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"tools": []}, "at least one tool"),
        ({"tools": ["  "]}, "at least one tool"),
        ({"tools": "issue_refund"}, "list of tool names"),
        ({"tools": ["issue_refund"], "risk": " "}, "risk must be"),
        ({"tools": ["issue_refund"], "timeout": "0s"}, "positive duration"),
    ],
)
def test_require_approval_rejects_bad_input(
    kwargs: dict[str, Any], match: str, registry: Approvals
) -> None:
    with pytest.raises(ValueError, match=match):
        _billing(registry).require_approval(**kwargs)


def test_a_schema_with_non_identifier_names_falls_back_to_keywords(registry: Approvals) -> None:
    schema = {"properties": {"invoice-id": {"type": "string"}}, "required": ["invoice-id"]}
    tools = _billing(registry).require_approval(tools=["issue_refund"])
    invoke = _Invoker()
    call = tools._bind_tool("issue_refund", _tool("billing", "issue_refund"), invoke, schema=schema)

    with pytest.raises(ApprovalRequired) as caught:
        call(**{"invoice-id": "INV-7"})

    assert dict(caught.value.approval.arguments) == {"arguments": {"invoice-id": "INV-7"}}


def test_a_call_that_could_never_run_fails_before_asking(registry: Approvals) -> None:
    tools = _billing(registry).require_approval(tools=["issue_refund"])
    call = tools._bind_tool(
        "issue_refund", _tool("billing", "issue_refund"), _Invoker(), schema=_REFUND_SCHEMA
    )

    with pytest.raises(TypeError):
        call(amount=40.0)  # the required "invoice" is missing
    assert registry.pending() == ()


# --- which client records it (ADR 0018, #1423) -----------------------------
def test_a_gate_outside_every_scope_records_on_the_discovering_client(
    registry: Approvals,
) -> None:
    call = (
        _billing(registry)
        .require_approval(tools=["issue_refund"])
        ._bind_tool("issue_refund", _tool("billing", "issue_refund"), _Invoker())
    )

    with pytest.raises(ApprovalRequired) as caught:
        call(invoice="INV-7")
    assert registry.pending() == (caught.value.approval,)


def test_the_running_clients_scope_wins_over_the_discovering_client(
    registry: Approvals,
) -> None:
    call = (
        _billing(registry)
        .require_approval(tools=["issue_refund"])
        ._bind_tool("issue_refund", _tool("billing", "issue_refund"), _Invoker())
    )

    with Donkey(_cfg()) as donkey:
        with donkey.run(), pytest.raises(ApprovalRequired) as caught:
            call(invoice="INV-7")
        assert donkey.approvals.pending() == (caught.value.approval,)
    assert registry.pending() == ()


def test_a_hand_built_set_needs_a_client_scope() -> None:
    invoke = _Invoker()
    call = (
        _billing(None)
        .require_approval(tools=["issue_refund"])
        ._bind_tool("issue_refund", _tool("billing", "issue_refund"), invoke)
    )

    with pytest.raises(ConfigError, match="no Donkey owns this call"):
        call(invoice="INV-7")
    assert invoke.calls == []


def test_the_discovering_client_survives_filter_and_require_approval(
    registry: Approvals,
) -> None:
    view = (
        _billing(registry).filter(allow=["issue_refund"]).require_approval(tools=["issue_refund"])
    )

    assert view._approval_registry is registry
