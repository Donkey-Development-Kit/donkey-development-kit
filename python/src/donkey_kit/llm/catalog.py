"""Model catalog (BG §1.1).

``list_models()`` returns the logical model names the proxy exposes, not raw
provider names. Prefer the proxy's own ``/models`` endpoint if it has one; fall
back to the registry, then to a bundled heuristic capability table.

``ModelHandle`` carries enough capability metadata to feed any framework that
requires explicit capability flags and to let a developer branch on
function-calling support (BG §1.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCapabilities:
    """Capability flags. Conservative defaults where unknown (BG §1.1)."""

    function_calling: bool = True
    vision: bool = False
    json_output: bool = False
    # True when derived from the bundled heuristic table rather than the platform.
    is_heuristic: bool = True


@dataclass(frozen=True)
class ModelHandle:
    id: str
    provider: str | None = None
    display_name: str | None = None
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)


# Bundled heuristic capability table (BG §1.1), keyed by well-known model IDs.
# Clearly a heuristic; users may override. NOT authoritative.
_HEURISTIC_CAPS: dict[str, ModelCapabilities] = {
    "gpt-4o": ModelCapabilities(function_calling=True, vision=True, json_output=True),
    "gpt-4o-mini": ModelCapabilities(function_calling=True, vision=True, json_output=True),
    "claude-opus-5": ModelCapabilities(function_calling=True, vision=True, json_output=True),
    "claude-sonnet-5": ModelCapabilities(function_calling=True, vision=True, json_output=True),
    "claude-haiku-4-5": ModelCapabilities(function_calling=True, vision=True, json_output=True),
}


def heuristic_capabilities(model_id: str) -> ModelCapabilities:
    """Best-effort capability lookup, marked ``is_heuristic=True``.

    Conservative fallback for unknown models: function calling on, vision/json
    off. Frameworks that demand explicit capability flags can seed them from
    this (BG §1.1).
    """

    return _HEURISTIC_CAPS.get(model_id, ModelCapabilities())
