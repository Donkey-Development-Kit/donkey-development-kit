"""Tool filtering, collision resolution and descriptor token accounting (BG §2.7).

Pure logic, no framework or network dependency, so it is fully implemented and
unit-testable now.

A filter can only narrow (#222): ``ToolSet.filter()`` calls stack, a tool must
pass every one of them, and nothing here can add a tool the servers did not
expose.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from ..core.errors import ConfigError

__all__ = [
    "ToolDescriptor",
    "ToolFilter",
    "check_patterns",
    "descriptor_tokens",
    "resolve_collisions",
]

# A rough tokens-per-character ratio for English text and JSON under the common
# BPE tokenizers. descriptor_tokens() is an estimate, not a tokenizer count.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ToolDescriptor:
    """A tool as seen before binding: which server it came from, its name, and
    metadata used for filtering and token-cost accounting (BG §2.7).

    ``input_schema`` is the tool's JSON Schema ``inputSchema``, when the server
    published one. It is left out of equality and hashing."""

    server: str  # short server name
    name: str
    description: str | None = None
    tags: tuple[str, ...] = ()
    input_schema: Mapping[str, object] | None = field(default=None, compare=False)

    @property
    def qualified_name(self) -> str:
        """``<server>__<name>``, the name collision resolution exposes on a clash."""
        return f"{self.server}__{self.name}"


@dataclass(frozen=True)
class ToolExclusion:
    """A tool a server lists that a ``ToolSet`` does not expose (#312): its name,
    its server's short name and why, such as ``"filter"`` for one a
    ``filter()`` call removed."""

    name: str
    server: str
    reason: str


def _matches(patterns: Iterable[str], tool: ToolDescriptor) -> bool:
    return any(fnmatchcase(tool.name, p) or fnmatchcase(tool.qualified_name, p) for p in patterns)


@dataclass(frozen=True)
class ToolFilter:
    """Allow-list, deny-list and predicate applied to tool descriptors; deny wins.

    ``allow`` and ``deny`` hold exact names or glob patterns (``get_*``), each
    matched against the bare tool name and the server-qualified one
    (``hr__get_employee``, ``hr__*``)."""

    allow: frozenset[str] | None = None
    deny: frozenset[str] = frozenset()
    predicate: Callable[[ToolDescriptor], bool] | None = None

    def accepts(self, tool: ToolDescriptor) -> bool:
        """True when ``tool`` passes the deny-list, the allow-list and the predicate."""
        if _matches(self.deny, tool):
            return False
        if self.allow is not None and not _matches(self.allow, tool):
            return False
        if self.predicate is not None and not self.predicate(tool):
            return False
        return True


def check_patterns(f: ToolFilter, tools: Sequence[ToolDescriptor]) -> None:
    """Refuse an ``allow`` or ``deny`` entry that matches none of ``tools``, so a
    typo never silently empties the set or leaves a tool exposed.

    ``tools`` is every tool the servers expose, before any filter.

    Raises:
        ConfigError: An entry matches no tool.
    """
    unmatched = sorted(
        p for p in (f.allow or frozenset()) | f.deny if not any(_matches((p,), t) for t in tools)
    )
    if unmatched:
        raise ConfigError(
            f"filter() names tools no server in this ToolSet exposes: {', '.join(unmatched)}.",
            remediation=(
                "Use a tool name or glob pattern that matches a tool the servers expose "
                "(see ToolSet.name_map), or '<server>__<tool>' to name one server's tool."
            ),
        )


def resolve_collisions(
    tools: list[ToolDescriptor],
) -> tuple[dict[str, ToolDescriptor], dict[str, str]]:
    """Resolve name collisions across servers by prefixing with the server's
    short name (BG §2.7): ``hr__get_employee``.

    Returns ``(exposed_name -> descriptor, exposed_name -> original_name)`` so a
    developer can debug why the model called ``hr__get_employee`` via
    ``ToolSet.name_map`` (BG §2.7).

    Raises:
        ConfigError: Two tools would still be exposed under one name, such as
            two servers sharing a short name, or a tool literally named like
            another's prefixed form. Neither is dropped silently.
    """

    counts: dict[str, int] = {}
    for tool in tools:
        counts[tool.name] = counts.get(tool.name, 0) + 1

    exposed: dict[str, ToolDescriptor] = {}
    name_map: dict[str, str] = {}
    clashes: set[str] = set()
    for tool in tools:
        exposed_name = tool.qualified_name if counts[tool.name] > 1 else tool.name
        if exposed_name in exposed:
            clashes.add(exposed_name)
        exposed[exposed_name] = tool
        name_map[exposed_name] = tool.name
    if clashes:
        raise ConfigError(
            f"Several tools would be exposed to the model as: {', '.join(sorted(clashes))}.",
            remediation=(
                "Keep the clashing tools apart with filter(deny=[...]), or leave one of "
                "the servers that share a short name out of the ToolSet."
            ),
        )
    return exposed, name_map


def _model_form(name: str, tool: ToolDescriptor) -> dict[str, object]:
    """The descriptor as a model's ``tools`` list carries it."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": tool.description or "",
            "parameters": dict(tool.input_schema or {"type": "object", "properties": {}}),
        },
    }


def descriptor_tokens(exposed: Mapping[str, ToolDescriptor]) -> int:
    """Estimate the prompt tokens the ``exposed`` descriptors cost on every model
    call (#222): the JSON of each as an OpenAI-style function tool, at about four
    characters per token. Tokenizers differ by model, so treat it as a guide for
    comparing tool sets, not a bill."""
    chars = sum(
        len(json.dumps(_model_form(name, tool), separators=(",", ":"), default=str))
        for name, tool in exposed.items()
    )
    return math.ceil(chars / _CHARS_PER_TOKEN)
