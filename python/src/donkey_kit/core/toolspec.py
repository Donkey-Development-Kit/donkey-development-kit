"""The ``@donkey.tool`` and ``@donkey.agent`` markers and their introspectable
registries (#200, #217).

``@donkey.tool`` records a governed tool's name, signature and docstring
**without changing call behaviour**: it returns the same callable, attaches a
:data:`TOOL_MARKER` (``__donkey_tool__``) to it, and appends a :class:`ToolSpec`
to a process-global registry (:func:`registered_tools`).

That one marker is the single thing the Phase 2 scanner (BG §2.5) and the A2A
agent-card generator (BG §2.9) both read — so the annotation pays off three times
(#200). ``@donkey.agent`` does the same for an agent entry point
(:class:`AgentSpec`, :func:`registered_agents`): ``donkey scan`` reads it
statically (#217) and ``donkey.serve()`` will build on it (#224).

Framework-free (core, the layered architecture): stdlib ``inspect`` + ``dataclasses`` only — no
httpx, no pydantic, no agent framework.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

__all__ = [
    "AGENT_MARKER",
    "TOOL_MARKER",
    "AgentSpec",
    "ToolSpec",
    "agent_spec",
    "register_agent",
    "register_tool",
    "registered_agents",
    "registered_tools",
    "tool_spec",
    "validate_service_id",
]

# The attribute ``@donkey.tool`` attaches to a marked callable. Reading
# ``getattr(fn, TOOL_MARKER, None)`` (or :func:`tool_spec`) is how a scanner or
# card generator recognises a governed tool without importing this module's
# registry — the marker travels on the function itself.
TOOL_MARKER = "__donkey_tool__"

_F = TypeVar("_F", bound=Callable[..., Any])


@dataclass(frozen=True)
class ToolSpec:
    """An introspectable record of a ``@donkey.tool``-marked callable.

    ``name`` / ``qualname`` / ``signature`` / ``docstring`` / ``is_async``
    describe the tool without executing it; ``func`` is the callable the
    decorator returned: the original (identity preserved), or for an
    approval-gated tool the gate that wraps it, so a consumer calling it gets the
    approval too. ``approval`` and ``risk`` are the ``@donkey.tool(...)``
    arguments (BG §2.3, #211). ``server`` is the service ID of the MCP server the
    tool belongs to (``@donkey.tool(server=...)``, #503), or ``None`` when the
    author has not attributed it; a scanner reports that as unattributed rather
    than guessing. This is the shape the Phase 2 scanner and the A2A card
    generator read (#200).
    """

    name: str
    qualname: str
    signature: inspect.Signature
    docstring: str
    is_async: bool
    func: Callable[..., Any]
    approval: str | None = None
    risk: str | None = None
    server: str | None = None


# A service ID names an MCP server (and, for #217, an agent) in the registry. It
# is permanent: the self-managed scanner keys a service on it forever (#1104) and
# the duplicate guard matches on it (#1107), so it is always set by the author
# and never inferred from a path or function name, which change on refactor
# (#503). Lowercase kebab-case is a conservative SDK-side rule; the platforms'
# own limits are unconfirmed (#1101).
_SERVICE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def validate_service_id(value: object, *, what: str = "server") -> str:
    """Return ``value`` when it is a valid service ID, else raise ``ValueError``.

    A service ID is lowercase kebab-case (``sku-manager``): letters, digits and
    single hyphens, starting and ending with a letter or digit (#503).
    """
    if not isinstance(value, str) or _SERVICE_ID.fullmatch(value) is None:
        raise ValueError(
            f"{what} must be a lowercase kebab-case service ID such as 'sku-manager', "
            f"not {value!r}. It names the service permanently, so it is set by you and "
            "never inferred (#503)."
        )
    return value


# Process-global, in decoration order. Tools are a process-wide concept (a
# scanner enumerates every tool defined in the running agent, not just those
# bound to one ``Donkey`` instance), so the registry is module-level rather than
# per-instance.
_REGISTRY: list[ToolSpec] = []


def register_tool(
    func: _F,
    *,
    approval: str | None = None,
    risk: str | None = None,
    server: str | None = None,
) -> _F:
    """Implementation behind ``@donkey.tool`` (#200).

    Returns ``func`` unchanged (identity preserved) after attaching a
    :data:`TOOL_MARKER` and recording a :class:`ToolSpec` with ``approval``,
    ``risk`` (BG §2.3) and ``server`` (#503). Raises
    :class:`ValueError` when ``func`` has no docstring — an undescribed tool is
    useless to a model and to the registry, so it is rejected rather than
    recorded blank (#200).
    """

    doc = inspect.getdoc(func)
    if doc is None or not doc.strip():
        raise ValueError(
            f"@donkey.tool requires a docstring on {getattr(func, '__qualname__', func)!r}: "
            "an undescribed tool is useless to a model and to the registry (#200). "
            "Add a short description of what the tool does."
        )
    spec = ToolSpec(
        name=func.__name__,
        qualname=func.__qualname__,
        signature=inspect.signature(func),
        docstring=doc.strip(),
        is_async=inspect.iscoroutinefunction(func),
        func=func,
        approval=approval,
        risk=risk,
        server=server,
    )
    setattr(func, TOOL_MARKER, spec)
    _REGISTRY.append(spec)
    return func


def registered_tools() -> tuple[ToolSpec, ...]:
    """Every callable marked with ``@donkey.tool`` in this process, in decoration
    order. The introspection entry point for the scanner / card generator (#200)."""
    return tuple(_REGISTRY)


def tool_spec(func: Callable[..., Any]) -> ToolSpec | None:
    """The :class:`ToolSpec` a ``@donkey.tool`` marker attached to ``func``, or
    ``None`` if ``func`` is not a marked tool."""
    marker = getattr(func, TOOL_MARKER, None)
    return marker if isinstance(marker, ToolSpec) else None


# The attribute ``@donkey.agent`` attaches to a marked agent entry point (#217).
AGENT_MARKER = "__donkey_agent__"


@dataclass(frozen=True)
class AgentSpec:
    """An introspectable record of a ``@donkey.agent``-marked entry point (#217).

    ``name`` is the agent's service ID (validated like a tool's ``server``,
    #503), ``skills`` the skill IDs it advertises and ``docstring`` its
    description. This is the shape ``donkey scan`` and the A2A card generator
    read (BG §2.5, BG §2.9), so the two cannot drift.
    """

    name: str
    qualname: str
    docstring: str
    skills: tuple[str, ...]
    is_async: bool
    func: Callable[..., Any]


_AGENTS: list[AgentSpec] = []


def register_agent(func: _F, *, name: str, skills: tuple[str, ...] = ()) -> _F:
    """Implementation behind ``@donkey.agent`` (#217).

    Returns ``func`` unchanged after attaching an :data:`AGENT_MARKER` and
    recording an :class:`AgentSpec`. Raises :class:`ValueError` when ``func``
    has no docstring: the docstring is the agent's description in the registry
    and on its A2A card.
    """
    doc = inspect.getdoc(func)
    if doc is None or not doc.strip():
        raise ValueError(
            f"@donkey.agent requires a docstring on {getattr(func, '__qualname__', func)!r}: "
            "it is the agent's description in the registry and on its A2A card (#217)."
        )
    spec = AgentSpec(
        name=name,
        qualname=func.__qualname__,
        docstring=doc.strip(),
        skills=skills,
        is_async=inspect.iscoroutinefunction(func),
        func=func,
    )
    setattr(func, AGENT_MARKER, spec)
    _AGENTS.append(spec)
    return func


def registered_agents() -> tuple[AgentSpec, ...]:
    """Every entry point marked with ``@donkey.agent`` in this process, in
    decoration order (#217)."""
    return tuple(_AGENTS)


def agent_spec(func: Callable[..., Any]) -> AgentSpec | None:
    """The :class:`AgentSpec` a ``@donkey.agent`` marker attached to ``func``,
    or ``None`` if ``func`` is not a marked agent."""
    marker = getattr(func, AGENT_MARKER, None)
    return marker if isinstance(marker, AgentSpec) else None


def _reset_for_tests() -> None:
    """Reset the process-global registries (#750). Called only from the autouse
    fixture in ``tests/conftest.py`` — production code never unregisters a tool
    or an agent."""
    _REGISTRY.clear()
    _AGENTS.clear()
