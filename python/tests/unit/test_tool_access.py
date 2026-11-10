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

import httpx
import pytest

from donkey_kit.core.errors import AuthError, PIIDetected, PolicyViolation, UpstreamModelError
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


@pytest.mark.parametrize("capture", ["pii-request", "pii-response-sse"])
def test_the_refusing_wrapper_types_captured_pii(capture: str) -> None:
    fixture = _FIXTURES.parent / "mcp_policies" / f"{capture}.json"
    error = json.loads(json.loads(fixture.read_text("utf-8"))["body"])["error"]

    async def denied() -> Any:
        raise _McpError(_ErrorData(error["code"], error["message"], error["data"]))

    with pytest.raises(PIIDetected) as caught:
        asyncio.run(_refusing(denied, tool="add", server="s", bound_for=_SERVICE)())
    assert caught.value.phase == ("request" if capture == "pii-request" else "response")
    assert caught.value.__cause__ is None
    assert isinstance(caught.value.framework_error, _McpError)


@pytest.mark.parametrize("wrapper", ["user", "401", "500"])
def test_refusing_respects_bridge_chain_boundaries(wrapper: str) -> None:
    inner = _McpError(_ErrorData(-32008, "Unable to access"))
    if wrapper == "user":
        original = RuntimeError("domain failure")
    else:
        request = httpx.Request("POST", "https://unrelated.test/")
        response = httpx.Response(int(wrapper), request=request)
        original = httpx.HTTPStatusError("unrelated", request=request, response=response)
    original.__cause__ = inner

    async def denied() -> Any:
        raise original

    with pytest.raises(type(original)) as caught:
        asyncio.run(_refusing(denied, tool="add", server="s", bound_for=_SERVICE)())
    assert caught.value is original


@pytest.mark.parametrize("kind", [PIIDetected, ToolNotInProfile])
def test_refusing_preserves_already_typed_error(kind: type[PolicyViolation]) -> None:
    original = kind("blocked")
    cause = ValueError("original cause")
    framework = RuntimeError("original framework")
    original.__cause__ = cause
    original.framework_error = framework

    async def denied() -> Any:
        raise original

    with pytest.raises(kind) as caught:
        asyncio.run(_refusing(denied, tool="add", server="s", bound_for=_SERVICE)())
    assert caught.value is original
    assert original.__cause__ is cause
    assert original.framework_error is framework


def test_refusing_access_denial_without_named_tool_keeps_binding_coordinates() -> None:
    async def denied() -> Any:
        raise _McpError(_ErrorData(-32008, "Unable to access"))

    with pytest.raises(ToolNotInProfile) as caught:
        asyncio.run(_refusing(denied, tool="add", server="s", bound_for=_SERVICE)())
    assert caught.value.tool_name == "add"
    assert caught.value.server == "s"


def test_refusing_rejects_wrong_requester_before_invoking() -> None:
    called = False

    async def invoke() -> Any:
        nonlocal called
        called = True
        return {"ok": True}

    call = _refusing(invoke, tool="add", server="s", bound_for=Requester("user", None, None))
    with pytest.raises(AuthError):
        asyncio.run(call())
    assert not called


def test_refusing_keeps_input_validation_error_and_ordinary_result() -> None:
    original = ValueError("invalid tool arguments")

    async def invalid() -> Any:
        raise original

    result = {"content": [{"type": "text", "text": "PII detected"}], "isError": False}

    async def ordinary() -> Any:
        return result

    with pytest.raises(ValueError) as caught:
        asyncio.run(_refusing(invalid, tool="add", server="s", bound_for=_SERVICE)())
    assert caught.value is original
    assert asyncio.run(_refusing(ordinary, tool="add", server="s", bound_for=_SERVICE)()) is result


def test_refusing_preserves_recovered_typed_cause() -> None:
    typed = PIIDetected("blocked")
    cause = OSError("transport failure")
    typed.__cause__ = cause
    wrapper = httpx.ConnectError(
        "framework failure", request=httpx.Request("POST", "https://proxy/mcp/")
    )
    wrapper.__cause__ = typed

    async def denied() -> Any:
        raise wrapper

    with pytest.raises(PIIDetected) as caught:
        asyncio.run(_refusing(denied, tool="add", server="s", bound_for=_SERVICE)())
    assert caught.value is typed
    assert typed.__cause__ is cause
    assert typed.framework_error is wrapper


@pytest.mark.parametrize("capture", ["schema-invalid-args", "poisoning-block-list"])
def test_the_refusing_wrapper_does_not_infer_ambiguous_policies(capture: str) -> None:
    fixture = _FIXTURES.parent / "mcp_policies" / f"{capture}.json"
    body = json.loads(fixture.read_text("utf-8"))["body"]
    if body.startswith("event:"):
        body = next(
            line.removeprefix("data: ") for line in body.splitlines() if line.startswith("data: ")
        )
    error = json.loads(body)["error"]
    original = _McpError(_ErrorData(error["code"], error["message"], error.get("data")))

    async def denied() -> Any:
        raise original

    with pytest.raises(_McpError) as caught:
        asyncio.run(_refusing(denied, tool="add", server="s", bound_for=_SERVICE)())
    assert caught.value is original


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
