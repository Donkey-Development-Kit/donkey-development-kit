"""Compatibility surface for the gateway's MCP tool-access denial (BG §1.2)."""

from __future__ import annotations

from ._tool_access import TOOL_ACCESS_DENIED_CODE, TOOL_ACCESS_DENIED_MESSAGE, ToolNotInProfile
from .jsonrpc_refusals import jsonrpc_refusal

__all__ = [
    "TOOL_ACCESS_DENIED_CODE",
    "TOOL_ACCESS_DENIED_MESSAGE",
    "ToolNotInProfile",
    "tool_access_denial",
]


def tool_access_denial(
    error: object, *, tool: str | None = None, server: str | None = None
) -> ToolNotInProfile | None:
    """Recover only the legacy MCP tool-access denial, through the shared walk.

    Preserves the captured code/message discriminator, tool-name fallback and
    public type; other JSON-RPC policies are not binding-wired here (#1388).
    """
    denied = jsonrpc_refusal(error, tool=tool, server=server)
    return denied if isinstance(denied, ToolNotInProfile) else None
