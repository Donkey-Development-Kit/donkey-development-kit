"""The SDK's own MCP client calls: ``tools/list`` for a ``ToolSet``'s descriptors (#221).

Opened only after the governance guard (ADR 0013), with the binding's credential
headers, over the official ``mcp`` package's streamable-HTTP client: the
baseline ``scripts/mcp_binding/probe_mcp_sdk.py`` round-trips (docs/verified-apis.md
§9). ``mcp`` 1.x builds that client on ``httpx`` and 2.x on ``httpx2``; the same
call works on both. The package is imported here, inside the functions, so
``import donkey_kit`` never needs it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from ..core.errors import AuthError, ToolInvocationError

__all__ = ["list_tools", "open_session"]

MCP_INSTALL = "pip install 'donkey-kit[mcp]'"


def _streamable() -> Any:  # noqa: ANN401
    try:
        import mcp.client.streamable_http as streamable
    except ImportError as exc:
        raise ImportError(
            f"Binding a ToolSet needs the MCP Python SDK. Install it with: {MCP_INSTALL}"
        ) from exc
    return streamable


def _status(exc: BaseException) -> int | None:
    """The HTTP status of the first response error inside ``exc``, which the
    ``mcp`` client raises inside a task group."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    for inner in getattr(exc, "exceptions", ()) or ():
        found = _status(inner)
        if found is not None:
            return found
    cause = exc.__cause__ or exc.__context__
    return _status(cause) if cause is not None and cause is not exc else None


@asynccontextmanager
async def open_session(url: str, headers: Mapping[str, str]) -> AsyncIterator[Any]:
    """An initialized ``mcp.ClientSession`` to ``url``, sending ``headers`` on every request."""
    streamable = _streamable()
    from mcp import ClientSession

    http_lib = getattr(streamable, "httpx2", None) or streamable.httpx
    async with (
        http_lib.AsyncClient(headers=dict(headers)) as http,
        streamable.streamable_http_client(url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        yield session


async def list_tools(
    url: str, headers: Mapping[str, str], *, server: str
) -> tuple[dict[str, object], ...]:
    """Every tool ``url`` lists, as ``tools/list`` JSON (``name``, ``description``,
    ``inputSchema``), following ``nextCursor``.

    Raises:
        AuthError: The gateway refused the credential (401 or 403).
        ToolInvocationError: The server could not be reached or refused the call.
    """
    tools: list[dict[str, object]] = []
    _streamable()
    from mcp.types import PaginatedRequestParams

    try:
        async with open_session(url, headers) as session:
            cursor: str | None = None
            while True:
                params = PaginatedRequestParams(cursor=cursor) if cursor else None
                listed = await session.list_tools(params=params)
                tools.extend(
                    t.model_dump(by_alias=True, exclude_none=True, mode="json")
                    for t in listed.tools
                )
                cursor = getattr(listed, "next_cursor", None) or getattr(listed, "nextCursor", None)
                if not cursor:
                    break
    except ImportError:
        raise
    except Exception as exc:  # the mcp client wraps HTTP errors in task groups
        status = _status(exc)
        if status in (401, 403):
            raise AuthError(
                f"The gateway refused the credential when listing the tools of {server} "
                f"(HTTP {status}).",
            ) from exc
        detail = f"HTTP {status}" if status is not None else f"{type(exc).__name__}: {exc}"
        raise ToolInvocationError(f"Listing the tools of {server} failed ({detail}).") from exc
    return tuple(tools)
