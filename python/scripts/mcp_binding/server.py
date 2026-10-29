#!/usr/bin/env python3
"""Local MCP reference server for the §9 binding probes (docs/verified-apis.md).

A real MCP server (the official ``mcp`` SDK) served over streamable HTTP on
127.0.0.1 at ``/mcp/``. The trailing slash is deliberate: the governed gateway
only routes the path with the slash (ledger §6), so the probes bind to the same
URL shape they would use against it. Two tools, ``lookup_sku`` and ``add``.

``--deny <tool>`` answers a ``tools/call`` of that tool the way the gateway's
MCP access policies refuse it (ledger §9, #303): HTTP 200 with the JSON-RPC
error ``-32008`` "Unable to access", the tool still listed (a stale descriptor),
for the #312 binding probes.

``--record <path>`` appends one JSON line per HTTP request: method, path, the
lower-cased header names, and the values of only ``client_id``, ``client_secret``
and ``x-correlation-id`` (the probes send fake values; nothing else is stored).

It prints ``READY <port>`` once listening and exits cleanly on SIGTERM/SIGINT.
Runs on mcp 1.x (``FastMCP``) and mcp 2.x (``MCPServer``).

Usage:
    python scripts/mcp_binding/server.py --port 8765 --record /tmp/requests.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

import uvicorn

HOST = "127.0.0.1"
MCP_PATH = "/mcp/"
RECORDED_VALUES = ("client_id", "client_secret", "x-correlation-id")
SKU_RECORD = {"sku": "SKU-1001", "name": "Widget", "price": 9.99, "in_stock": True}


def lookup_sku(sku: str) -> dict[str, Any]:
    """Look up a product by SKU and return its record.

    Args:
        sku: The stock keeping unit to look up.
    """
    return {**SKU_RECORD, "sku": sku}


def add(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a: The first number.
        b: The second number.
    """
    return a + b


def build_app() -> Any:
    """Build the ASGI app: the MCP streamable-HTTP app served at ``/mcp/``."""
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP

        server = FastMCP("ddk-mcp-binding", host=HOST, streamable_http_path=MCP_PATH)
        server.tool()(lookup_sku)
        server.tool()(add)
        return server.streamable_http_app()
    except ImportError:  # mcp 2.x renamed FastMCP to MCPServer and moved the path
        from mcp.server.mcpserver import MCPServer

        server2 = MCPServer("ddk-mcp-binding")
        server2.tool()(lookup_sku)
        server2.tool()(add)
        return server2.streamable_http_app(streamable_http_path=MCP_PATH, host=HOST)


class RecordingMiddleware:
    """Pure-ASGI middleware appending one JSON line per HTTP request."""

    def __init__(self, app: Any, record: Path | None) -> None:
        self.app = app
        self.record = record

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and self.record is not None:
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]
            }
            entry = {
                "method": scope["method"],
                "path": scope["path"],
                "header_names": sorted(headers),
                **{name: headers.get(name) for name in RECORDED_VALUES},
            }
            with self.record.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        await self.app(scope, receive, send)


class DenyMiddleware:
    """Pure-ASGI middleware refusing ``tools/call`` of one tool with the
    gateway's captured access denial (``tests/fixtures/anypoint/mcp_access``)."""

    def __init__(self, app: Any, tool: str | None) -> None:
        self.app = app
        self.tool = tool

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or self.tool is None or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        try:
            request = json.loads(body)
        except ValueError:
            request = None
        if (
            isinstance(request, dict)
            and request.get("method") == "tools/call"
            and (request.get("params") or {}).get("name") == self.tool
        ):
            denial = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32008,
                    "message": "Unable to access",
                    "data": f"Access denied to: {self.tool}",
                },
            }
            payload = json.dumps(denial, separators=(",", ":")).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        sent = False

        async def replay() -> Any:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def serve(port: int, record: Path | None, deny: str | None = None) -> None:
    """Serve until SIGTERM/SIGINT, printing READY once the socket is listening."""
    app = RecordingMiddleware(DenyMiddleware(build_app(), deny), record)
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(server, "should_exit", True))
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(0.05)
    if server.started:
        print(f"READY {port}", flush=True)
    await task


def main() -> int:
    """CLI entry point."""
    summary = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=summary)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--record", type=Path, default=None, help="append request JSON lines here")
    parser.add_argument("--deny", default=None, help="refuse tools/call of this tool (#312)")
    args = parser.parse_args()
    if args.record is not None:
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.touch()
    asyncio.run(serve(args.port, args.record, args.deny))
    return 0


if __name__ == "__main__":
    sys.exit(main())
