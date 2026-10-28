#!/usr/bin/env python3
"""Google ADK probe: ``McpToolset`` + ``StreamableHTTPConnectionParams`` against the local server.

Lists the tools through ``McpToolset.get_tools()``, then has a scripted model call ``add``
through a real ``Runner`` so the call goes through ADK's own tool-call path
(``McpTool.run_async``), not a raw MCP session. Then reads the server's record to confirm
the gateway's Client ID Enforcement headers arrived.

Usage (throwaway venv with ``google-adk`` and ``mcp``), from python/:
    python scripts/mcp_binding/probe_adk.py [--write]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from collections.abc import AsyncGenerator
from typing import Any

from _harness import LocalServer, distributions, headers_seen, local_mcp_server, write_fixture
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.mcp_tool import McpTool, McpToolset, StreamableHTTPConnectionParams
from google.genai import types

HEADERS = {
    "client_id": "probe-id",
    "client_secret": "probe-secret",
    "x-correlation-id": "probe-correlation-id",
}
ARGS = {"a": 2, "b": 3}


class CallsAdd(BaseLlm):
    """Scripted model: asks for ``add(2, 3)`` once, then answers with the tool result."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        """Emit the function call, or "done" once a function response is in the history."""
        del stream  # the scripted model never streams
        answered = any(
            part.function_response is not None
            for content in llm_request.contents
            for part in content.parts or ()
        )
        part = (
            types.Part(text="done")
            if answered
            else types.Part(function_call=types.FunctionCall(id="fc-1", name="add", args=ARGS))
        )
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def _qualname(obj: Any) -> str:
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


def _text_of(response: Any) -> str | None:
    """The text of an MCP tool result as ADK returns it (``content[0].text`` or a bare value)."""
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            text = first.get("text") if isinstance(first, dict) else getattr(first, "text", None)
            return None if text is None else str(text)
        if "result" in response:
            return str(response["result"])
    return None if response is None else str(response)


async def run(srv: LocalServer) -> dict[str, Any]:
    """Bind the toolset, list tools, and call ``add`` through a ``Runner``."""
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=srv.url, headers=HEADERS),
    )
    try:
        tools = await toolset.get_tools()
        listed = sorted(t.name for t in tools)
        assert listed == ["add", "lookup_sku"], listed
        assert all(isinstance(t, McpTool) for t in tools), [_qualname(t) for t in tools]
        native = sorted({_qualname(t) for t in tools})

        agent = LlmAgent(name="probe", model=CallsAdd(model="scripted"), tools=[toolset])
        sessions = InMemorySessionService()
        runner = Runner(app_name="probe", agent=agent, session_service=sessions)
        session = await sessions.create_session(app_name="probe", user_id="u")
        message = types.Content(role="user", parts=[types.Part(text="add 2 and 3")])
        responses: list[Any] = []
        async for event in runner.run_async(
            user_id="u", session_id=session.id, new_message=message
        ):
            responses.extend(r.response for r in event.get_function_responses())
        assert len(responses) == 1, responses
        result = _text_of(responses[0])
        raw = repr(responses[0])
    finally:
        await toolset.close()
    return {"listed": listed, "native": native, "result": result, "raw": raw}


async def slash_probe(srv: LocalServer) -> str:
    """Informational: does the URL without the trailing slash work? (the real gateway needs it)"""
    toolset = McpToolset(
        connection_params=StreamableHTTPConnectionParams(url=srv.url.rstrip("/"), headers=HEADERS),
    )
    try:
        return f"ok {sorted(t.name for t in await toolset.get_tools())}"
    except BaseException as exc:  # noqa: BLE001 - informational only
        return f"failed {type(exc).__name__}: {str(exc)[:200]}"
    finally:
        await toolset.close()


def main() -> None:
    """Run the probe; with ``--write`` record the fixture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    with local_mcp_server() as srv:
        out = asyncio.run(run(srv))
        seen = headers_seen(srv.record)
        no_slash = asyncio.run(slash_probe(srv))
    assert seen == {"client_id": "probe-id", "client_secret": "probe-secret"}, seen
    assert out["result"] == "5", out
    fixture = {
        "framework": "adk",
        "distributions": distributions("google-adk", "litellm"),
        "binding": {
            "class": "McpToolset",
            "module": "google.adk.tools.mcp_tool",
            "call": (
                "McpToolset(connection_params=StreamableHTTPConnectionParams(url=..., "
                "headers=...)); await get_tools(); LlmAgent(tools=[toolset]) run by Runner"
            ),
        },
        "url_shape": "http://127.0.0.1:<port>/mcp/",
        "tools_listed": out["listed"],
        "native_tool_types": out["native"],
        "tool_call": {"name": "add", "args": ARGS, "result": out["result"]},
        "headers_seen": {k: seen[k] for k in ("client_id", "client_secret")},
        "date": dt.date.today().isoformat(),
    }
    print(json.dumps(fixture, indent=2, sort_keys=True))
    print("raw function_response:", out["raw"])
    print("without trailing slash (informational):", no_slash)
    if args.write:
        print("wrote", write_fixture("adk", fixture))


if __name__ == "__main__":
    main()
