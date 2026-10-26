"""Human approval for discovered MCP tools (BG §2.3, #1153).

``ToolSet.require_approval(tools=[...], risk=...)`` gives a discovered tool the
approval that ``@donkey.tool(approval="required")`` gives a local function
(#211). The rules here are pure: which exposed tool a rule matches, and the
check that every rule matches something. :func:`gated_tool` is the seam each
binding method applies to its per-tool invoker, once binding exists (#221,
docs/verified-apis.md §9). It wraps the invoker in :func:`core.approvals.gate`,
so a gated call raises the same ``ApprovalRequired``, records the same span
events, resolves through ``donkey.approvals`` and pauses in a LangGraph graph
through the same native hook (#212).

The approval is client-side and advisory, as for ``@donkey.tool``: the gateway
never learns that an approval was needed. Gateway step-up is #214.

MCP elicitation is not used, and is not a mapping target (#1236). A server
sends ``elicitation/create`` to the client while it handles a request, so it
cannot hold a call the client has not sent yet; the gate here holds it before
the binding's invoker sends it.
"""

from __future__ import annotations

import inspect
import keyword
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..core.approvals import NativePause, gate
from ..core.errors import ConfigError
from .filter import ToolDescriptor

__all__ = ["ApprovalRule", "check_rules", "gated_tool", "rule_for"]


@dataclass(frozen=True)
class ApprovalRule:
    """One ``require_approval(...)`` declaration: the tool names it gates, the
    risk label and the timeout in seconds.

    A bare name (``issue_refund``) matches that tool on every server. The
    server-prefixed form (``billing__issue_refund``, as collision resolution
    exposes it) matches only that server's tool.
    """

    tools: frozenset[str]
    risk: str | None
    timeout: float

    def matches(self, tool: ToolDescriptor) -> bool:
        """True when this rule gates ``tool``."""
        return tool.name in self.tools or tool.qualified_name in self.tools


def rule_for(rules: Sequence[ApprovalRule], tool: ToolDescriptor) -> ApprovalRule | None:
    """The rule that gates ``tool``: the last declared one that matches it, or
    ``None`` when no rule does."""
    for rule in reversed(rules):
        if rule.matches(tool):
            return rule
    return None


def check_rules(rules: Sequence[ApprovalRule], tools: Sequence[ToolDescriptor]) -> None:
    """Refuse a rule name that matches none of ``tools``, so a typo can never
    leave a tool ungated.

    ``tools`` is every tool the servers expose, before ``filter()``: a filter
    that removes a gated tool is fine, the tool is simply not bound.

    Raises:
        ConfigError: A name in a rule matches no tool.
    """
    known = {t.name for t in tools} | {t.qualified_name for t in tools}
    unknown = sorted({name for rule in rules for name in rule.tools} - known)
    if unknown:
        raise ConfigError(
            f"require_approval() names tools no server in this ToolSet exposes: "
            f"{', '.join(unknown)}.",
            remediation=(
                "Use a tool name the server exposes (see ToolSet.name_map), or "
                "'<server>__<tool>' to name one server's tool."
            ),
        )


def _signature(schema: Mapping[str, Any] | None) -> inspect.Signature:
    """Keyword-only parameters from a tool's JSON Schema ``inputSchema``, so the
    approval shows named arguments. Required properties have no default;
    optional ones take the schema default or ``None``. No schema, or a property
    name that is not a Python identifier, falls back to one ``**arguments``
    parameter."""
    keywords = inspect.Signature([inspect.Parameter("arguments", inspect.Parameter.VAR_KEYWORD)])
    properties = (schema or {}).get("properties")
    if not isinstance(properties, Mapping):
        return keywords
    required = (schema or {}).get("required")
    required = set(required) if isinstance(required, (list, tuple)) else set()
    names = [str(name) for name in properties]
    if not all(n.isidentifier() and not keyword.iskeyword(n) for n in names):
        return keywords
    params = []
    for name in sorted(names, key=lambda n: n not in required):
        prop = properties[name]
        default = (
            inspect.Parameter.empty
            if name in required
            else (prop.get("default") if isinstance(prop, Mapping) else None)
        )
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default))
    return inspect.Signature(params)


def gated_tool(
    name: str,
    invoke: Callable[..., Any],
    *,
    rule: ApprovalRule,
    schema: Mapping[str, Any] | None = None,
    natives: Sequence[NativePause] = (),
) -> Callable[..., Any]:
    """Wrap ``invoke``, the binding's call of one MCP tool, so a call asks for
    approval instead of running it.

    ``name`` is the exposed tool name the approval records. ``invoke`` takes the
    tool's arguments as keywords and may be ``async``; once approved it gets
    only the arguments the caller passed. ``natives`` are the framework pauses
    to try first, such as the LangGraph ``interrupt()`` hook (#212).
    """
    if inspect.iscoroutinefunction(invoke):

        async def acall(**arguments: Any) -> Any:  # noqa: ANN401
            return await invoke(**arguments)

        call: Callable[..., Any] = acall
    else:

        def call(**arguments: Any) -> Any:  # noqa: ANN401
            return invoke(**arguments)

    call.__name__ = call.__qualname__ = name
    call.__signature__ = _signature(schema)  # type: ignore[attr-defined]
    return gate(call, risk=rule.risk, timeout=rule.timeout, natives=natives)
