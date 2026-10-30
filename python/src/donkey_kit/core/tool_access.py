"""The gateway's MCP tool-access denial, typed (#312, BG §1.2).

The Omni Gateway MCP access policies (MCP Global Access, Attribute-Based Access
Control) refuse a ``tools/call`` outside the consumer's access profile with HTTP
200 and a JSON-RPC error, not an HTTP error (docs/verified-apis.md §9, #303), so
:func:`~donkey_kit.core.errors.classify`, which reads HTTP responses, never sees
it. :func:`tool_access_denial` reads the JSON-RPC error instead, from the ``mcp``
client's exception or the raw error object, and the ``ToolSet`` per-tool
bindings raise what it returns.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .errors import PolicyViolation

__all__ = [
    "TOOL_ACCESS_DENIED_CODE",
    "TOOL_ACCESS_DENIED_MESSAGE",
    "ToolNotInProfile",
    "tool_access_denial",
]


class ToolNotInProfile(PolicyViolation):
    """The gateway refused an MCP tool call: the tool is outside this consumer's
    access profile (#312).

    Raised from the denial the Omni Gateway MCP access policies (MCP Global
    Access, Attribute-Based Access Control) send for a Mule-hosted and a
    framework-built server alike (docs/verified-apis.md §9, #303): HTTP 200 with
    the JSON-RPC error ``-32008`` "Unable to access". A configuration problem,
    not a runtime one: no retry or rephrasing can succeed until someone grants
    the tool. A tool that ran and failed (an MCP result with ``isError: true``)
    is a result, never this.

    ``tool_name`` is the tool the gateway named in the error's ``data``, else the
    one that was called. ``server`` is the server's ``group/asset/version`` when
    known. ``profile`` is always ``None``: the gateway does not say which profile
    or rule refused the call."""

    policy = "mcp-access-control"
    remediation: str = (
        "The gateway refused this MCP tool: it is not in this consumer's access profile. This is "
        "a configuration problem and was NOT retried. Ask the server's owner to grant the tool "
        "in the MCP access policy (Global Access or Attribute-Based Access Control) on its API "
        "instance in API Manager, or stop offering the tool to the agent."
    )

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        server: str | None = None,
        profile: str | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(message, **kw)
        self.tool_name = tool_name
        self.server = server
        self.profile = profile


#: The JSON-RPC error both Omni Gateway MCP access policies answer a denied
#: ``tools/call`` with (docs/verified-apis.md §9, #303). ``data`` differs by policy
#: and is only read for the tool name.
TOOL_ACCESS_DENIED_CODE = -32008
TOOL_ACCESS_DENIED_MESSAGE = "Unable to access"
_DENIED_TOOL_RE = re.compile(r'^Access denied to:\s*"?(?P<tool>[^"]+?)"?\.?$')


def _error_data(found: Any) -> tuple[Any, Any, Any] | None:  # noqa: ANN401
    """``(code, message, data)`` of a JSON-RPC error object or mapping, else ``None``."""
    if isinstance(found, Mapping):
        return found.get("code"), found.get("message"), found.get("data")
    if hasattr(found, "code") and hasattr(found, "message"):
        return found.code, found.message, getattr(found, "data", None)
    return None


def _denial(found: Any) -> Any:  # noqa: ANN401
    """The ``data`` of ``found`` when it is the access denial, ``...`` otherwise."""
    fields = _error_data(found)
    if fields is None:
        return ...
    code, message, data = fields
    if code == TOOL_ACCESS_DENIED_CODE and message == TOOL_ACCESS_DENIED_MESSAGE:
        return data
    return ...


def tool_access_denial(
    error: object, *, tool: str | None = None, server: str | None = None
) -> ToolNotInProfile | None:
    """A :class:`ToolNotInProfile` when ``error`` is the gateway's MCP tool-access
    denial, else ``None`` (#312).

    ``error`` is a JSON-RPC ``error`` object as a mapping, or an exception: the
    ``mcp`` package's ``McpError`` (1.x) / ``MCPError`` (2.x), or anything whose
    cause, context or exception group holds one, such as the ``ExceptionGroup``
    the ``mcp`` client raises from its task group or a framework's own wrapper
    around it. The discriminator is ``code == -32008`` with the message "Unable to
    access" (docs/verified-apis.md §9). ``tool`` is the tool that was called,
    used when the error's ``data`` names none; ``server`` its coordinates.

    The ToolSet's LangGraph, LlamaIndex and Anthropic bindings already raise the
    typed error. For a framework that raises the ``mcp`` error itself, convert it::

        except Exception as exc:
            if (denied := tool_access_denial(exc)) is not None:
                raise denied from exc
            raise
    """
    stack: list[object] = [error]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        data = _denial(getattr(current, "error", current))
        if data is not ...:
            match = _DENIED_TOOL_RE.match(data.strip()) if isinstance(data, str) else None
            name = match.group("tool") if match else tool
            where = f" on {server}" if server else ""
            return ToolNotInProfile(
                f"The gateway refused the MCP tool {name or '(unnamed)'}{where}: it is not in "
                "this consumer's access profile.",
                tool_name=name,
                server=server,
            )
        if isinstance(current, BaseException):
            stack.extend(getattr(current, "exceptions", ()) or ())
            stack.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return None
