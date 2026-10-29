"""The SDK's own MCP client calls: ``tools/list`` for a ``ToolSet``'s descriptors (#221).

Opened only after the governance guard (ADR 0013), with the binding's credential
headers, over the official ``mcp`` package's streamable-HTTP client: the
baseline ``scripts/mcp_binding/probe_mcp_sdk.py`` round-trips (docs/verified-apis.md
§9). ``mcp`` 1.x builds that client on ``httpx`` and 2.x on ``httpx2``; the same
call works on both. The package is imported here, inside the functions, so
``import donkey_kit`` never needs it.

:func:`client_factory` and :func:`open_transport` build the HTTP client the
framework bindings in ``tools/session.py`` hand their framework's MCP client
(#1415): it sends the credential only to the server's origin, as the governed
clients do (``core/transport/headers.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from ..core.errors import AuthError, ToolInvocationError, classify
from ..core.step_up import GatewayApprovalDenied, GatewayApprovalPending
from ..core.transport.headers import CREDENTIAL_HEADERS, origin_of, strip_credential_headers

__all__ = ["client_factory", "list_tools", "open_session", "open_transport"]

MCP_INSTALL = "pip install 'donkey-kit[mcp]'"

#: The ``mcp`` transports' default timeouts, in seconds (``create_mcp_http_client``):
#: the read timeout is long because a server may hold a response stream open.
_TIMEOUT = 30.0
_READ_TIMEOUT = 300.0


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
    ``mcp`` 1.x client raises inside a task group. ``mcp`` 2.x raises an
    ``MCPError`` without the response instead; ``open_session`` records the
    status for that case."""
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
async def open_session(
    url: str, headers: Mapping[str, str], *, errors: list[Any] | None = None
) -> AsyncIterator[Any]:
    """An initialized ``mcp.ClientSession`` to ``url``, sending ``headers`` on
    every request. Each HTTP error response the server answers with is appended
    to ``errors``, its body read, so the caller can classify it."""
    streamable = _streamable()
    from mcp import ClientSession

    async def record(response: Any) -> None:  # noqa: ANN401
        if errors is not None and response.status_code >= 400:
            await response.aread()
            errors.append(response)

    http_lib = getattr(streamable, "httpx2", None) or streamable.httpx
    async with (
        http_lib.AsyncClient(headers=dict(headers), event_hooks={"response": [record]}) as http,
        streamable.streamable_http_client(url, http_client=http) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await session.initialize()
        yield session


def client_factory(url: str, credentials: Mapping[str, str]) -> Callable[..., Any]:
    """An ``mcp`` HTTP-client factory, ``(headers, timeout, auth) -> AsyncClient``,
    for a binding to ``url`` that sends ``credentials`` (#1415).

    Its clients (``httpx`` on ``mcp`` 1.x, ``httpx2`` on 2.x) do not follow
    redirects, and a request hook strips the ``credentials`` names and
    :data:`~donkey_kit.core.transport.headers.CREDENTIAL_HEADERS` from any
    request to another origin than ``url``'s. ``mcp`` 1.30 and 2.3 follow a
    redirect within the origin themselves; an earlier ``mcp`` builds its client
    with ``follow_redirects=True``, and httpx keeps every header but
    ``Authorization`` on a hop to another origin.

    ``mcp`` is imported when the framework calls the factory, not before.
    """
    origin = origin_of(url)
    names = CREDENTIAL_HEADERS | {name.lower() for name in credentials}

    async def guard(request: Any) -> None:  # noqa: ANN401
        if origin_of(str(request.url)) != origin:
            strip_credential_headers(request, names)

    def build(
        headers: Mapping[str, str] | None = None,
        timeout: Any = None,  # noqa: ANN401
        auth: Any = None,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        streamable = _streamable()
        http_lib = getattr(streamable, "httpx2", None) or streamable.httpx
        return http_lib.AsyncClient(
            headers=dict(headers or {}),
            timeout=timeout or http_lib.Timeout(_TIMEOUT, read=_READ_TIMEOUT),
            auth=auth,
            follow_redirects=False,
            event_hooks={"request": [guard]},
        )

    return build


@asynccontextmanager
async def open_transport(url: str, headers: Mapping[str, str]) -> AsyncIterator[Any]:
    """The streamable-HTTP streams to ``url`` over a :func:`client_factory`
    client sending ``headers``, closed with the transport, for a framework that
    takes a transport rather than a client (Strands)."""
    streamable = _streamable()
    async with (
        client_factory(url, headers)(headers=headers) as http,
        streamable.streamable_http_client(url, http_client=http) as streams,
    ):
        yield streams


async def list_tools(
    url: str, headers: Mapping[str, str], *, server: str
) -> tuple[dict[str, object], ...]:
    """Every tool ``url`` lists, as ``tools/list`` JSON (``name``, ``description``,
    ``inputSchema``), following ``nextCursor``.

    Raises:
        GatewayApprovalPending: The gateway is waiting for the user's CIBA approval (#1320).
        GatewayApprovalDenied: The user denied that approval.
        AuthError: The gateway refused the credential (401 or 403).
        ToolInvocationError: The server could not be reached or refused the call.
    """
    tools: list[dict[str, object]] = []
    errors: list[Any] = []
    _streamable()
    from mcp.types import PaginatedRequestParams

    try:
        async with open_session(url, headers, errors=errors) as session:
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
        if errors:
            # A CIBA step-up answer is a 401/403 too, but the credential was accepted: raise
            # it typed, so the caller neither drops the credential nor retries at once (#1320).
            step_up = classify(errors[0])
            if isinstance(step_up, (GatewayApprovalPending, GatewayApprovalDenied)):
                raise step_up from exc
        status = _status(exc) or (errors[0].status_code if errors else None)
        if status in (401, 403):
            raise AuthError(
                f"The gateway refused the credential when listing the tools of {server} "
                f"(HTTP {status}).",
            ) from exc
        detail = f"HTTP {status}" if status is not None else f"{type(exc).__name__}: {exc}"
        raise ToolInvocationError(f"Listing the tools of {server} failed ({detail}).") from exc
    return tuple(tools)
