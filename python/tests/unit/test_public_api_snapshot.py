"""The stable surface, snapshotted (ADR 0006 rule 4, #1425).

``ROOT_ALL`` is ``donkey_kit.__all__``. ``MEMBERS`` is every public member of the
stable classes in ``_SNAPSHOTTED``: each method with its parameter names, kinds
and simple defaults, marked ``[async]`` when it is a coroutine function, and each
property marked ``[property]``. Annotations are left out, so the lists read the
same on every Python version. ``PROVISIONAL_MEMBERS`` is the public members of
those classes whose docstring carries ``PROVISIONAL_MARK`` (ADR 0020): they may
change in a minor release with a migration note. Every public member is in
exactly one of the two. The lists are hard-coded on purpose: a PR that changes
the surface or a member's tier changes them too, so the change is visible in
review.

To update them after a deliberate change, run this file from ``python/`` and
paste what it prints over the three lists::

    python tests/unit/test_public_api_snapshot.py

Removing or renaming a name that shipped in a release needs a deprecated alias
for one minor release first (ADR 0006 rule 2).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from typing import Any

import donkey_kit
from donkey_kit import Donkey, ToolSet, ToolsFacade

_SNAPSHOTTED: tuple[type, ...] = (Donkey, ToolsFacade, ToolSet)

#: The docstring words that make a public member of a stable class Provisional.
PROVISIONAL_MARK = "Provisional (ADR 0020)"

ROOT_ALL = [
    "A2AResult",
    "A2ATask",
    "AgentKilled",
    "AgentSpec",
    "Approval",
    "ApprovalRejected",
    "ApprovalRequired",
    "ApprovalTimedOut",
    "Approvals",
    "AttributionScope",
    "AuthError",
    "Budget",
    "BudgetReserveReached",
    "CacheControls",
    "CacheScope",
    "Classifier",
    "ConfigError",
    "ConfigOverrides",
    "ContentSafetyBlocked",
    "CostTags",
    "Donkey",
    "DonkeyAsyncClientView",
    "DonkeyClientView",
    "DonkeyConfig",
    "DonkeyError",
    "ExchangeRegistry",
    "GatewayApprovalDenied",
    "GatewayApprovalPending",
    "GatewayUnavailable",
    "GovernanceCriteria",
    "LLMClient",
    "LastCall",
    "LastCallStatus",
    "McpAuth",
    "ModelNotRoutable",
    "ModelSubstituted",
    "OnRefusal",
    "PIIDetected",
    "PolicyViolation",
    "PromptInjectionBlocked",
    "Region",
    "RequestRateLimitExceeded",
    "RequestWindow",
    "RunLedger",
    "RunRefusal",
    "RunScope",
    "TokenBudgetExceeded",
    "ToolNotInProfile",
    "ToolSet",
    "ToolSpec",
    "ToolsFacade",
    "TypedRefusals",
    "UngovernedMcpServer",
    "UpstreamModelError",
    "UpstreamQuota",
    "UpstreamRequestError",
    "UpstreamWindow",
    "UserScope",
    "__version__",
    "classify",
    "registered_agents",
    "registered_tools",
    "typed_refusals",
]

MEMBERS = [
    "Donkey.aclose() [async]",
    "Donkey.agent(*, name, skills=...)",
    "Donkey.approvals [property]",
    "Donkey.as_user(*, token)",
    "Donkey.attribution(*, business_group=None, team=None, project=None, env=None, enduser_id=None)",
    "Donkey.budget [property]",
    "Donkey.cache(*, skip=None, no_store=None, ttl=None, threshold=None, principal_id=None)",
    "Donkey.classify [property]",
    "Donkey.close()",
    "Donkey.config [property]",
    "Donkey.correlation_headers()",
    "Donkey.current_run [property]",
    "Donkey.flush_telemetry(timeout_s=2.0)",
    "Donkey.from_env(*, team=None, project=None, env=None, enduser_id=None, path=None, **overrides)",
    "Donkey.governed(func=None, *, team=None, project=None, env=None, enduser_id=None, business_group=None, typed_refusals=True)",
    "Donkey.http_client(*, sync=False)",
    "Donkey.last_call [property]",
    "Donkey.llm [property]",
    "Donkey.on(refusal)",
    "Donkey.openai(*, sync=False, **kw)",
    "Donkey.registry [property]",
    "Donkey.run(id=None, *, team=None, project=None, env=None, enduser_id=None, business_group=None, typed_refusals=True, ledger_max_entries=1000)",
    "Donkey.run_context(run_id=None)",
    "Donkey.run_from_headers(headers, *, team=None, project=None, env=None, enduser_id=None, business_group=None, typed_refusals=True, ledger_max_entries=1000)",
    "Donkey.serve(handler, *, host='127.0.0.1', port=8000, url=None, version='0.0.0')",
    "Donkey.simulate(error, *, times=1)",
    "Donkey.tool(func=None, /, *, approval=None, risk=None, approval_timeout='15m', server=None)",
    "Donkey.tools [property]",
    "ToolSet.adk() [async]",
    "ToolSet.agent_framework() [async]",
    "ToolSet.anthropic()",
    "ToolSet.crewai()",
    "ToolSet.describe() [async]",
    "ToolSet.descriptor_tokens()",
    "ToolSet.discovered_at [property]",
    "ToolSet.excluded [property]",
    "ToolSet.filter(*, allow=None, deny=None, predicate=None, servers=None)",
    "ToolSet.is_stale [property]",
    "ToolSet.langgraph() [async]",
    "ToolSet.llamaindex() [async]",
    "ToolSet.name_map [property]",
    "ToolSet.openai_agents() [async]",
    "ToolSet.require_approval(*, tools, risk=None, timeout='15m')",
    "ToolSet.servers [property]",
    "ToolSet.strands() [async]",
    "ToolSet.wait_for_gateway_approval(timeout='2m')",
    "ToolsFacade.discover(*, domain=None, tags=None, governed=True, locked=False, auth=None) [async]",
    "ToolsFacade.lock()",
    "ToolsFacade.refresh()",
    "ToolsFacade.warm(*, domain=None, tags=None, governed=True) [async]",
]

PROVISIONAL_MEMBERS = [
    "ToolsFacade.live_card(ref) [async]",
    "ToolsFacade.live_tools(ref) [async]",
]


class _Default:
    """A default rendered as its ``repr`` when it is a plain literal, else ``...``."""

    def __init__(self, value: object) -> None:
        simple = value is None or type(value) in (bool, int, float, str)
        self._text = repr(value) if simple else "..."

    def __repr__(self) -> str:
        return self._text


def _parameters(owner: type, attr: str, member: Callable[..., Any]) -> str:
    static = inspect.getattr_static(owner, attr)
    signature = inspect.signature(member)
    params = list(signature.parameters.values())
    if not isinstance(static, (staticmethod, classmethod)) and params:
        params = params[1:]  # self
    bare = [
        p.replace(
            annotation=inspect.Parameter.empty,
            default=p.default if p.default is inspect.Parameter.empty else _Default(p.default),
        )
        for p in params
    ]
    return str(signature.replace(parameters=bare, return_annotation=inspect.Signature.empty))


def _member_line(owner: type, attr: str, member: object) -> str:
    name = f"{owner.__name__}.{attr}"
    if isinstance(inspect.getattr_static(owner, attr), property):
        return f"{name} [property]"
    if not callable(member):
        return f"{name} [attribute]"
    suffix = " [async]" if inspect.iscoroutinefunction(member) else ""
    return f"{name}{_parameters(owner, attr, member)}{suffix}"


def current_members(*, provisional: bool | None = False) -> list[str]:
    """The public members of the snapshotted classes, as ``MEMBERS`` lists them:
    the stable ones, the ``provisional=True`` ones, or with ``None`` all of them."""
    return sorted(
        _member_line(owner, attr, member)
        for owner in _SNAPSHOTTED
        for attr, member in inspect.getmembers(owner)
        if not attr.startswith("_")
        if provisional is None or _is_provisional(member) is provisional
    )


def _is_provisional(member: object) -> bool:
    return PROVISIONAL_MARK in (inspect.getdoc(member) or "")


def _drift(name: str, expected: list[str], actual: list[str]) -> str:
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    lines = [f"the public surface changed: {name} no longer matches the snapshot."]
    lines += [f"  + {item}" for item in added]
    lines += [f"  - {item}" for item in removed]
    lines += [
        "If the change is deliberate, run `python tests/unit/test_public_api_snapshot.py`",
        "from python/ and paste its output over ROOT_ALL, MEMBERS and PROVISIONAL_MEMBERS",
        "in that file. A member is provisional when its docstring says "
        f"{PROVISIONAL_MARK!r} (ADR 0020).",
        "A name that shipped in a release keeps a deprecated alias for one minor",
        "release before it is removed or renamed (ADR 0006 rule 2).",
    ]
    return "\n".join(lines)


def test_root_all_matches_the_snapshot() -> None:
    actual = sorted(donkey_kit.__all__)
    assert actual == sorted(ROOT_ALL), _drift("donkey_kit.__all__", ROOT_ALL, actual)


def test_stable_class_members_match_the_snapshot() -> None:
    actual = current_members()
    assert actual == sorted(MEMBERS), _drift("MEMBERS", MEMBERS, actual)


def test_provisional_class_members_match_the_snapshot() -> None:
    """ADR 0020: the docstring says Provisional and the list says so too."""
    actual = current_members(provisional=True)
    assert actual == sorted(PROVISIONAL_MEMBERS), _drift(
        "PROVISIONAL_MEMBERS", PROVISIONAL_MEMBERS, actual
    )


def test_every_public_member_is_in_exactly_one_tier_list() -> None:
    assert not set(MEMBERS) & set(PROVISIONAL_MEMBERS)
    assert sorted(MEMBERS + PROVISIONAL_MEMBERS) == current_members(provisional=None)


def test_the_snapshot_lists_are_sorted_and_unique() -> None:
    lists = (
        ("ROOT_ALL", ROOT_ALL),
        ("MEMBERS", MEMBERS),
        ("PROVISIONAL_MEMBERS", PROVISIONAL_MEMBERS),
    )
    for name, items in lists:
        assert items == sorted(set(items)), f"{name} must be sorted, with no duplicates"


def _print_list(name: str, items: list[str]) -> None:
    print(f"{name} = [")
    for item in items:
        print(f"    {json.dumps(item)},")
    print("]")


def _print_lists() -> None:
    _print_list("ROOT_ALL", sorted(donkey_kit.__all__))
    print()
    _print_list("MEMBERS", current_members())
    print()
    _print_list("PROVISIONAL_MEMBERS", current_members(provisional=True))


if __name__ == "__main__":
    _print_lists()
