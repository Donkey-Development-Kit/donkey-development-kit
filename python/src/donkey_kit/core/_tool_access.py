"""Legacy tool-access type and single-error rule beneath the shared walk (BG §1.2)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .errors import PolicyViolation

__all__ = [
    "TOOL_ACCESS_DENIED_CODE",
    "TOOL_ACCESS_DENIED_MESSAGE",
    "ToolNotInProfile",
    "match_tool_access",
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

    # Keep the shipped type's module identity for introspection and pickling.
    __module__ = "donkey_kit.core.tool_access"
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


def match_tool_access(
    error: object, *, tool: str | None = None, server: str | None = None
) -> ToolNotInProfile | None:
    """Match the captured -32008 / Unable to access pair, parsing only the tool name."""
    data = _denial(error)
    if data is ...:
        return None
    match = _DENIED_TOOL_RE.match(data.strip()) if isinstance(data, str) else None
    name = match.group("tool") if match else tool
    where = f" on {server}" if server else ""
    return ToolNotInProfile(
        f"The gateway refused the MCP tool {name or '(unnamed)'}{where}: it is not in "
        "this consumer's access profile.",
        tool_name=name,
        server=server,
    )
