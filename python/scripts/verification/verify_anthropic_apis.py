#!/usr/bin/env python3
"""Offline installed-package evidence for docs/verified-apis.md §§9–10.

This probe does not implement DDK's guarded MCP/descriptor surfaces or establish
remote transport behavior. Run from python/ with this framework and its MCP
integration installed. Any assertion/import error is a failed verification.
"""

from __future__ import annotations

import inspect
import json
import sys
from importlib.metadata import version


def deny_network(event: str, args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError(f"Offline probe attempted network access: {event}")


def describe(symbol: object) -> dict[str, str]:
    return {
        "symbol": f"{symbol.__module__}.{symbol.__name__}",
        "signature": str(inspect.signature(symbol)),
    }


def lookup(city: str) -> str:
    """Look up a city."""
    return city


def main() -> None:
    import asyncio

    import httpx
    from anthropic import AsyncAnthropic
    from anthropic.types import ToolParam
    from anthropic.types.beta import BetaRequestMCPServerURLDefinitionParam

    assert {"name", "description", "input_schema"} <= ToolParam.__annotations__.keys()
    assert {"type", "name", "url"} <= BetaRequestMCPServerURLDefinitionParam.__annotations__.keys()
    tool = ToolParam(
        name="lookup",
        description="Look up a city.",
        input_schema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    server = BetaRequestMCPServerURLDefinitionParam(type="url", name="probe", url=URL)
    captured = []

    async def respond(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        # Synthetic SDK serialization probe, never an Anypoint response fixture.
        return httpx.Response(
            200,
            json={
                "id": "probe",
                "type": "message",
                "role": "assistant",
                "model": "probe",
                "content": [],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
            async with AsyncAnthropic(api_key="placeholder", http_client=transport) as client:
                await client.beta.messages.create(
                    model="probe",
                    max_tokens=1,
                    messages=[{"role": "user", "content": "probe"}],
                    mcp_servers=[server],
                    tools=[tool],
                )
                signature = str(inspect.signature(client.beta.messages.create))
                assert "mcp_servers" in inspect.signature(client.beta.messages.create).parameters
        assert captured[0]["mcp_servers"] == [server]
        assert captured[0]["tools"] == [tool]
        emit(
            ["anthropic", "httpx"],
            [{"symbol": "anthropic.AsyncAnthropic.beta.messages.create", "signature": signature}],
            {"mcp_servers": [server], "scope": "SDK request serialization only; no MCP transport"},
            {"tool": tool, "serialized_request": captured[0]},
        )

    asyncio.run(exercise())


URL = "https://placeholder.invalid/mcp"
HEADERS = {"x-probe": "offline"}


def emit(
    packages: list[str],
    symbols: list[dict[str, str]],
    binding: dict[str, object],
    descriptor: dict[str, object],
) -> None:
    print(
        json.dumps(
            {
                "python": sys.version,
                "versions": {package: version(package) for package in packages},
                "binding_symbols": symbols,
                "binding": binding,
                "descriptor": descriptor,
                "network": "denied by audit hook; no gateway or live MCP verification",
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    sys.addaudithook(deny_network)
    main()
