"""The gateway's MCP tool-access denial, typed (#312).

The denial is an HTTP 200 with a JSON-RPC error, byte-identical for a Mule-hosted
and a framework-built server (docs/verified-apis.md §9, #303). These tests read
the captured #303 bodies, so they need no framework and no gateway.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from donkey_kit.core.errors import PolicyViolation, UpstreamModelError
from donkey_kit.core.identity import Requester
from donkey_kit.core.tool_access import ToolNotInProfile, tool_access_denial
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.tools import ToolExclusion
from donkey_kit.tools.session import ToolSet, _refusing

_SERVICE = Requester(None, None, None)
_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "anypoint" / "mcp_access"


def _captured(name: str) -> dict[str, Any]:
    body = json.loads((_FIXTURES / f"tools-call.{name}-denied.body.json").read_text("utf-8"))
    return dict(body["error"])


class _ErrorData:
    """The shape of the ``mcp`` package's ``ErrorData``."""

    def __init__(self, code: int, message: str, data: object = None) -> None:
        self.code, self.message, self.data = code, message, data


class _Group(Exception):
    """The shape of an ``ExceptionGroup`` (3.11+): ``.exceptions``."""

    def __init__(self, message: str, exceptions: list[BaseException]) -> None:
        super().__init__(message)
        self.exceptions = tuple(exceptions)


class _McpError(Exception):
    """The shape of ``mcp``'s ``McpError`` (1.x) / ``MCPError`` (2.x): ``.error``."""

    def __init__(self, error: _ErrorData) -> None:
        super().__init__(error.message)
        self.error = error


@pytest.mark.parametrize(
    ("policy", "tool"), [("global-access", "current_time"), ("abac", "lookup_proxy")]
)
def test_both_captured_denials_classify_with_the_named_tool(policy: str, tool: str) -> None:
    denied = tool_access_denial(_captured(policy), server="com.example/inv/1.0.0")
    assert isinstance(denied, ToolNotInProfile)
    assert denied.tool_name == tool
    assert denied.server == "com.example/inv/1.0.0"
    assert denied.profile is None
    assert tool in str(denied) and "com.example/inv/1.0.0" in str(denied)


def test_the_mcp_error_classifies_through_groups_and_causes() -> None:
    err = _captured("abac")
    raised = _McpError(_ErrorData(err["code"], err["message"], err["data"]))
    try:
        try:
            raise raised
        except _McpError as inner:
            raise RuntimeError("framework wrapper") from inner
    except RuntimeError as wrapped:
        group = _Group("task group", [ValueError("other"), wrapped])
    denied = tool_access_denial(group)
    assert denied is not None and denied.tool_name == "lookup_proxy"


def test_data_without_a_tool_name_falls_back_to_the_called_tool() -> None:
    denied = tool_access_denial({"code": -32008, "message": "Unable to access"}, tool="add")
    assert denied is not None and denied.tool_name == "add"


@pytest.mark.parametrize(
    "error",
    [
        {"code": -32602, "message": "Invalid params", "data": "Access denied to: add"},
        {"code": -32008, "message": "Something else"},
        _McpError(_ErrorData(-32603, "Internal error")),
        ValueError("Unable to access"),
        # A tool that ran and failed is a result, never the denial.
        {"content": [{"type": "text", "text": "Unable to access"}], "isError": True},
        None,
    ],
)
def test_anything_else_is_not_the_denial(error: object) -> None:
    assert tool_access_denial(error) is None


def test_a_cyclic_chain_terminates() -> None:
    a, b = ValueError("a"), ValueError("b")
    a.__context__, b.__context__ = b, a
    assert tool_access_denial(a) is None


def test_the_refusal_is_a_configuration_policy_violation() -> None:
    assert issubclass(ToolNotInProfile, PolicyViolation)
    assert not issubclass(ToolNotInProfile, UpstreamModelError)
    assert ToolNotInProfile.policy == "mcp-access-control"
    assert "API Manager" in ToolNotInProfile("boom").remediation


def test_the_refusing_wrapper_raises_the_typed_denial_from_the_mcp_error() -> None:
    err = _captured("global-access")

    async def denied(**_: Any) -> Any:
        raise _McpError(_ErrorData(err["code"], err["message"], err["data"]))

    call = _refusing(
        denied, tool="current_time", server="com.example/inv/1.0.0", bound_for=_SERVICE
    )
    with pytest.raises(ToolNotInProfile) as caught:
        asyncio.run(call(x=1))
    assert isinstance(caught.value.__cause__, _McpError)
    assert caught.value.server == "com.example/inv/1.0.0"


def test_the_refusing_wrapper_passes_other_errors_and_results_through() -> None:
    async def boom(**_: Any) -> Any:
        raise ConnectionError("down")

    async def failed(**kwargs: Any) -> Any:
        return {"isError": True, "content": [], "args": kwargs}

    with pytest.raises(ConnectionError):
        asyncio.run(_refusing(boom, tool="add", server="s", bound_for=_SERVICE)())
    result = asyncio.run(_refusing(failed, tool="add", server="s", bound_for=_SERVICE)(a=1))
    assert result == {"isError": True, "content": [], "args": {"a": 1}}
    assert _refusing(failed, tool="add", server="s", bound_for=_SERVICE).__name__ == "failed"


def _server(asset_id: str, *names: str) -> McpServerHandle:
    return McpServerHandle(
        ref=AssetRef("com.acme", asset_id, "1.0.0"),
        endpoint_url=f"https://example.test/{asset_id}/mcp",
        tool_descriptors=tuple({"name": n, "description": n} for n in names),
    )


def test_excluded_lists_what_the_filters_removed() -> None:
    tools = ToolSet([_server("hr", "get_employee", "get_ssn", "delete_employee")])
    assert tools.excluded == ()
    narrowed = tools.filter(deny=["delete_*"]).filter(deny=["get_ssn"])
    assert {(e.name, e.reason) for e in narrowed.excluded} == {
        ("delete_employee", "filter"),
        ("get_ssn", "filter"),
    }
    assert all(isinstance(e, ToolExclusion) for e in narrowed.excluded)
    assert set(narrowed.name_map) == {"get_employee"}
