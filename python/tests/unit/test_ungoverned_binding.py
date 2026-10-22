"""``ToolSet`` binding refuses a server that is not governed (#1150, ADR 0013).

The refusal comes before any connection opens, so the consumer's gateway
credentials never reach a server the gateway does not front.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from donkey_kit.experimental import UngovernedMcpServer
from donkey_kit.registry.criteria import Check, GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools.session import ToolSet

_BINDINGS = (
    "langgraph",
    "adk",
    "strands",
    "llamaindex",
    "openai",
    "anthropic",
    "crewai",
    "agent_framework",
)

_GOVERNED = GovernanceReport(governed=True, checks=[Check("deployed", True, "gateway gw-1")])
_UNGOVERNED = GovernanceReport(
    governed=False,
    checks=[
        Check("api_instance_exists", False, "no API Manager instance in Sandbox"),
        Check("governance_pass", None, "UNKNOWN: rulesets API returned 403"),
        Check("deployed", True, "gateway gw-1"),
    ],
)


def _server(asset_id: str, governance: GovernanceReport | None) -> McpServerHandle:
    return McpServerHandle(
        ref=AssetRef("com.acme", asset_id, "1.0.0"),
        endpoint_url=f"https://{asset_id}.example.test/mcp",
        governance=governance,
    )


def _bind(tools: ToolSet, method: str) -> Callable[[], object]:
    bind: Callable[[], object] = getattr(tools, method)
    return bind


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Every request any httpx client tries to send during the test."""
    requests: list[httpx.Request] = []

    def record_sync(self: httpx.Client, request: httpx.Request, **kw: object) -> httpx.Response:
        requests.append(request)
        raise AssertionError(f"binding sent a request to {request.url}")

    async def record_async(
        self: httpx.AsyncClient, request: httpx.Request, **kw: object
    ) -> httpx.Response:
        requests.append(request)
        raise AssertionError(f"binding sent a request to {request.url}")

    monkeypatch.setattr(httpx.Client, "send", record_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", record_async)
    return requests


@pytest.mark.parametrize("method", _BINDINGS)
def test_an_ungoverned_server_is_refused_with_its_reasons(
    method: str, sent: list[httpx.Request]
) -> None:
    tools = ToolSet([_server("hr", _GOVERNED), _server("fastmcp-crm", _UNGOVERNED)])

    with pytest.raises(UngovernedMcpServer) as caught:
        _bind(tools, method)()

    err = caught.value
    assert err.servers == ("com.acme/fastmcp-crm/1.0.0",)
    assert err.reasons == (
        "com.acme/fastmcp-crm/1.0.0: api_instance_exists: no API Manager instance in Sandbox",
        "com.acme/fastmcp-crm/1.0.0: governance_pass: UNKNOWN: rulesets API returned 403",
    )
    assert "com.acme/hr/1.0.0" not in str(err)
    assert "front the server with the gateway" in err.remediation.lower()
    # No request went out at all, so none carried the gateway credentials.
    assert sent == []


@pytest.mark.parametrize("method", _BINDINGS)
def test_a_server_never_evaluated_is_refused(method: str, sent: list[httpx.Request]) -> None:
    tools = ToolSet([_server("unknown", None)])

    with pytest.raises(UngovernedMcpServer) as caught:
        _bind(tools, method)()

    assert caught.value.reasons == ("com.acme/unknown/1.0.0: governance: not evaluated",)
    assert sent == []


def test_a_verdict_without_failed_checks_still_names_a_reason() -> None:
    tools = ToolSet([_server("bare", GovernanceReport(governed=False, checks=[]))])

    with pytest.raises(UngovernedMcpServer) as caught:
        tools.langgraph()

    assert caught.value.reasons == ("com.acme/bare/1.0.0: governance: not governed",)


def test_every_refused_server_is_named() -> None:
    tools = ToolSet([_server("a", None), _server("b", _UNGOVERNED), _server("c", _GOVERNED)])

    with pytest.raises(UngovernedMcpServer) as caught:
        tools.adk()

    assert caught.value.servers == ("com.acme/a/1.0.0", "com.acme/b/1.0.0")


def test_filtering_does_not_drop_the_guard() -> None:
    # filter() narrows tools, not servers: the ungoverned server is still bound.
    tools = ToolSet([_server("crm", _UNGOVERNED)]).filter(allow=["read"])

    with pytest.raises(UngovernedMcpServer):
        tools.strands()


@pytest.mark.parametrize("method", _BINDINGS)
def test_a_governed_set_gets_past_the_guard(method: str, sent: list[httpx.Request]) -> None:
    # Binding itself is still blocked on verification (docs/verified-apis.md §9).
    tools = ToolSet([_server("hr", _GOVERNED)])

    with pytest.raises(NotImplementedError, match="blocked on verification"):
        _bind(tools, method)()
    assert sent == []


def test_there_is_no_opt_out() -> None:
    # ADR 0013 rule 3: no allow_ungoverned escape hatch on binding or discovery.
    import inspect

    from donkey_kit.donkey import ToolsFacade

    for method in _BINDINGS:
        assert list(inspect.signature(getattr(ToolSet, method)).parameters) == ["self"]
    assert "allow_ungoverned" not in inspect.signature(ToolsFacade.discover).parameters
