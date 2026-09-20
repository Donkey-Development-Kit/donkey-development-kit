"""Tool filtering + name-collision resolution (BG §2.7).

Pure logic, no framework or network dependency, so it is fully implemented and
unit-testable now.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolDescriptor:
    """A tool as seen before binding: which server it came from, its name, and
    metadata used for filtering and token-cost logging (BG §2.7)."""

    server: str  # short server name
    name: str
    description: str | None = None
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolFilter:
    allow: frozenset[str] | None = None
    deny: frozenset[str] = frozenset()
    predicate: Callable[[ToolDescriptor], bool] | None = None

    def accepts(self, tool: ToolDescriptor) -> bool:
        if tool.name in self.deny:
            return False
        if self.allow is not None and tool.name not in self.allow:
            return False
        if self.predicate is not None and not self.predicate(tool):
            return False
        return True


def resolve_collisions(
    tools: list[ToolDescriptor],
) -> tuple[dict[str, ToolDescriptor], dict[str, str]]:
    """Resolve name collisions across servers by prefixing with the server's
    short name (BG §2.7): ``hr__get_employee``.

    Returns ``(exposed_name -> descriptor, exposed_name -> original_name)`` so a
    developer can debug why the model called ``hr__get_employee`` via
    ``ToolSet.name_map`` (BG §2.7).
    """

    counts: dict[str, int] = {}
    for tool in tools:
        counts[tool.name] = counts.get(tool.name, 0) + 1

    exposed: dict[str, ToolDescriptor] = {}
    name_map: dict[str, str] = {}
    for tool in tools:
        exposed_name = f"{tool.server}__{tool.name}" if counts[tool.name] > 1 else tool.name
        exposed[exposed_name] = tool
        name_map[exposed_name] = tool.name
    return exposed, name_map
