"""The ``@donkey.tool`` marker and its introspectable registry (#200).

``@donkey.tool`` records a governed tool's name, signature and docstring
**without changing call behaviour**: it returns the same callable, attaches a
:data:`TOOL_MARKER` (``__donkey_tool__``) to it, and appends a :class:`ToolSpec`
to a process-global registry (:func:`registered_tools`).

That one marker is the single thing the Phase 2 scanner (BG §2.5) and the A2A
agent-card generator (BG §2.9) both read — so the annotation pays off three times
(#200). Neither consumer exists yet; this introduces the marker they will read.

Framework-free (core, the layered architecture): stdlib ``inspect`` + ``dataclasses`` only — no
httpx, no pydantic, no agent framework.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

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
    describe the tool without executing it; ``func`` is the **original** callable
    (identity preserved — the decorator does not wrap it). This is the shape the
    Phase 2 scanner and the A2A card generator read (#200).
    """

    name: str
    qualname: str
    signature: inspect.Signature
    docstring: str
    is_async: bool
    func: Callable[..., Any]


# Process-global, in decoration order. Tools are a process-wide concept (a
# scanner enumerates every tool defined in the running agent, not just those
# bound to one ``Donkey`` instance), so the registry is module-level rather than
# per-instance.
_REGISTRY: list[ToolSpec] = []


def register_tool(func: _F) -> _F:
    """Implementation behind ``@donkey.tool`` (#200).

    Returns ``func`` unchanged (identity preserved) after attaching a
    :data:`TOOL_MARKER` and recording a :class:`ToolSpec`. Raises
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


def _clear_registry() -> None:
    """Reset the process-global registry. For tests only — production code never
    unregisters a tool."""
    _REGISTRY.clear()
