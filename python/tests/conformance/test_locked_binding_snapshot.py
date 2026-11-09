"""Native per-tool conversion uses the reviewed catalog without a second listing."""

from __future__ import annotations

import copy
import json

import httpx
import pytest
from test_tool_binding import BINDINGS, _require

from donkey_kit._testing import mcp_factory_scope
from donkey_kit.registry.criteria import GovernanceReport
from donkey_kit.registry.models import AssetRef, McpServerHandle
from donkey_kit.simulator.mcp import mcp_baseline
from donkey_kit.tools.auth import ClientIdAuth
from donkey_kit.tools.lockfile import build_lock
from donkey_kit.tools.session import ToolSet


@pytest.mark.parametrize("binding", ["langgraph", "llamaindex", "anthropic"])
async def test_real_locked_binding_converts_snapshot_and_calls(binding: str) -> None:
    _require(BINDINGS[binding])
    url = "https://gateway.example.test/mcp/"
    descriptor = {
        "name": "lookup",
        "description": "Reviewed description",
        "inputSchema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    }
    handle = McpServerHandle(
        ref=AssetRef("example", "inventory", "1.0.0"),
        endpoint_url=url,
        governance=GovernanceReport(governed=True, checks=[]),
        tool_descriptors=(copy.deepcopy(descriptor),),
    )
    tools = ToolSet([handle], _auth=ClientIdAuth("id", "secret"), _lock=build_lock([handle]))
    baseline = mcp_baseline(
        url,
        tools=[{**descriptor, "description": "UNREVIEWED"}],
        call_tool=lambda name, arguments: {"content": [{"type": "text", "text": arguments["sku"]}]},
    )
    from mcp.client import streamable_http

    http_lib = getattr(streamable_http, "httpx2", None) or streamable_http.httpx
    methods: list[str] = []

    def factory(url, credentials=None, **kwargs):
        async def respond(request):
            content = await request.aread()
            if request.method == "POST":
                methods.append(json.loads(content)["method"])
            reply = baseline.handle_request(
                httpx.Request(request.method, str(request.url), content=content)
            )
            return http_lib.Response(
                reply.status_code, headers=reply.headers, content=reply.content
            )

        return http_lib.AsyncClient(
            headers=kwargs.get("headers") or credentials,
            transport=http_lib.MockTransport(respond),
        )

    with mcp_factory_scope(tools, factory):
        if binding == "anthropic":
            async with tools.anthropic() as bound:
                (tool,) = bound
                assert tool.description == descriptor["description"]
                assert tool.input_schema == descriptor["inputSchema"]
                assert "SKU-1" in str(await tool.call({"sku": "SKU-1"}))
        elif binding == "langgraph":
            (tool,) = await tools.langgraph()
            assert tool.description == descriptor["description"]
            assert tool.args_schema == descriptor["inputSchema"]
            assert "SKU-1" in str(await tool.ainvoke({"sku": "SKU-1"}))
        else:
            (tool,) = await tools.llamaindex()
            assert tool.metadata.description == descriptor["description"]
            assert tool.metadata.fn_schema.model_json_schema()["required"] == ["sku"]
            assert "SKU-1" in str(await tool.acall(sku="SKU-1"))
    assert "tools/call" in methods
    # The MCP SDK may list after a call to validate its result, not to build tools.
    assert "tools/list" not in methods[: methods.index("tools/call")]
