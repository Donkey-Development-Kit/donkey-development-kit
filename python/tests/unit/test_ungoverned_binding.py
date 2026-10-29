"""``ToolSet`` binding refuses a server that is not governed (#1150, ADR 0013).

The refusal comes before any connection opens, so the consumer's gateway
credentials never reach a server the gateway does not front.
"""

from __future__ import annotations

import inspect

import httpx
import pytest

from donkey_kit import UngovernedMcpServer
from donkey_kit.core.errors import ConfigError
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


async def _bind(tools: ToolSet, method: str) -> object:
    """Run one binding however it is called: awaited or (Anthropic, CrewAI) entered."""
    if method in ("anthropic", "crewai"):
        async with getattr(tools, method)() as bound:
            return bound
    return await getattr(tools, method)()


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
async def test_an_ungoverned_server_is_refused_with_its_reasons(
    method: str, sent: list[httpx.Request]
) -> None:
    tools = ToolSet([_server("hr", _GOVERNED), _server("fastmcp-crm", _UNGOVERNED)])

    with pytest.raises(UngovernedMcpServer) as caught:
        await _bind(tools, method)

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
async def test_a_server_never_evaluated_is_refused(method: str, sent: list[httpx.Request]) -> None:
    tools = ToolSet([_server("unknown", None)])

    with pytest.raises(UngovernedMcpServer) as caught:
        await _bind(tools, method)

    assert caught.value.reasons == ("com.acme/unknown/1.0.0: governance: not evaluated",)
    assert sent == []


async def test_a_verdict_without_failed_checks_still_names_a_reason() -> None:
    tools = ToolSet([_server("bare", GovernanceReport(governed=False, checks=[]))])

    with pytest.raises(UngovernedMcpServer) as caught:
        await tools.langgraph()

    assert caught.value.reasons == ("com.acme/bare/1.0.0: governance: not governed",)


async def test_every_refused_server_is_named() -> None:
    tools = ToolSet([_server("a", None), _server("b", _UNGOVERNED), _server("c", _GOVERNED)])

    with pytest.raises(UngovernedMcpServer) as caught:
        await tools.adk()

    assert caught.value.servers == ("com.acme/a/1.0.0", "com.acme/b/1.0.0")


async def test_filtering_does_not_drop_the_guard() -> None:
    # filter() narrows tools, not servers: the ungoverned server is still bound.
    tools = ToolSet([_server("crm", _UNGOVERNED)]).filter(allow=["read"])

    with pytest.raises(UngovernedMcpServer):
        await tools.strands()


@pytest.mark.parametrize("method", _BINDINGS)
async def test_a_governed_set_gets_past_the_guard(method: str, sent: list[httpx.Request]) -> None:
    # Past the guard, a binding needs its framework and a gateway credential; with
    # neither it stops there, still before any request.
    tools = ToolSet([_server("hr", _GOVERNED)])

    with pytest.raises((ImportError, ConfigError)):
        await _bind(tools, method)
    assert sent == []


async def test_filtering_out_the_server_leaves_nothing_to_refuse(
    sent: list[httpx.Request],
) -> None:
    # filter(servers=) drops a server before binding, as discover(governed=True) does.
    tools = ToolSet([_server("crm", _UNGOVERNED), _server("hr", _GOVERNED)])

    with pytest.raises((ImportError, ConfigError)):
        await tools.filter(servers=["hr"]).strands()
    with pytest.raises(UngovernedMcpServer):
        await tools.filter(servers=["c*"]).strands()
    assert sent == []


def test_there_is_no_opt_out() -> None:
    # ADR 0013 rule 3: no allow_ungoverned escape hatch on binding or discovery.
    from donkey_kit.donkey import ToolsFacade

    for method in _BINDINGS:
        assert list(inspect.signature(getattr(ToolSet, method)).parameters) == ["self"]
    assert "allow_ungoverned" not in inspect.signature(ToolsFacade.discover).parameters
