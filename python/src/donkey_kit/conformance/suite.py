"""The customer-facing conformance suite (#191, `BG §1.5`).

This module defines the ONE scenario set the pytest plugin
(:mod:`donkey_kit.conformance.plugin`) runs against a *customer's own* agent
via ``pytest --donkey-conformance --agent=my_app:build``. It is the public,
run-it-against-your-agent sibling of the SDK's internal adapter matrix
(``tests/conformance/suite.py``) — that one is ours, this one is theirs.

Each scenario asks a question a team usually cannot answer about its own code
and answers it framework-agnostically, by *observing the transport and the
logs* rather than the agent's internals:

- **retries a budget refusal** — counting wire sends: a ``TokenBudgetExceeded``
  is terminal, so more than one send for one run means the agent retried.
- **swallows PII as a generic error** — inspecting what ``run()`` lets escape:
  a bare non-:class:`~donkey_kit.core.errors.DonkeyError` means the typed
  refusal was lost.
- **propagates the correlation id** — binding a known id and grepping the
  agent's own log output for it.
- **works without budget headers** — serving a success with no ``x-token-*``
  and asserting the run still completes.

Framework isolation (the layered architecture): stdlib only at module top, plus the framework-free
``core.errors`` taxonomy. The :class:`ScenarioContext` protocol the checks call
is defined *here*, so ``suite.py`` never imports the harness — the harness
imports the suite, not the other way round. No web framework and no ``openai``
at import time, so this stays safe on the base import path that the pytest11
entry point auto-loads on every pytest run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..core.errors import DonkeyError, PIIDetected, TokenBudgetExceeded

__all__ = [
    "PROBE_CORRELATION_ID",
    "SCENARIOS",
    "SCENARIO_NAMES",
    "Observation",
    "Outcome",
    "Result",
    "Scenario",
    "ScenarioContext",
    "Status",
    "validate_known_limitations",
]

# A distinctive, deterministic id the correlation scenario binds via
# ``run_context`` and then greps for in the agent's logs. Fixed rather than
# random so the check never flakes and the value is easy to spot in output.
PROBE_CORRELATION_ID = "donkey-conformance-correlation-probe"

Status = Literal["pass", "fail", "exempt"]


@dataclass(frozen=True)
class Observation:
    """What the harness saw during one ``agent.run(...)``: the return value, the
    exception it let escape (if any), how many times it hit the gateway, and the
    text of every log record it emitted."""

    returned: Any
    raised: BaseException | None
    wire_sends: int
    log_text: str


@dataclass(frozen=True)
class Outcome:
    """A single scenario's verdict against one agent: pass/fail plus a
    one-line, human-facing reason phrased as a finding about the agent."""

    passed: bool
    detail: str


@dataclass(frozen=True)
class Result:
    """A scenario's place in the final table: pass, fail, or an asserted
    ``exempt`` (never a silent skip). ``detail`` carries the finding for a
    pass/fail and the asserted reason for an exemption."""

    scenario: str
    title: str
    status: Status
    detail: str


class ScenarioContext(Protocol):
    """The surface a scenario check drives. The harness implements it; defining
    it here keeps ``suite.py`` free of any dependency on the harness."""

    @property
    def agent(self) -> Any:
        """The agent instance built from the customer's factory for this run."""

    def serve_refusal(self, error: type[DonkeyError]) -> None:
        """Arm the gateway to return the captured fixture that ``classify()``
        maps back to ``error`` for every send in the next :meth:`run`."""

    def serve_success(self, *, budget_headers: bool = True) -> None:
        """Arm the gateway to return the captured success fixture. With
        ``budget_headers=False`` the ``x-token-*`` headers are stripped, so the
        run sees an unobserved budget."""

    async def run(self, *, correlation_id: str | None = None) -> Observation:
        """Invoke ``agent.run(<probe input>)`` (awaiting it if it is a
        coroutine), optionally inside a bound correlation id, and return what was
        observed."""


@dataclass(frozen=True)
class Scenario:
    """One conformance question: a stable ``name`` (the ``KNOWN_LIMITATIONS``
    key and the pytest item id), a human ``title`` and ``question`` for the
    report, and the async ``check`` that drives a :class:`ScenarioContext`."""

    name: str
    title: str
    question: str
    check: Callable[[ScenarioContext], Awaitable[Outcome]]


# --- the four scenario checks ------------------------------------------------
# Each check owns its own gateway setup (via the context) so the harness stays
# scenario-agnostic. Detail strings are phrased as findings about the agent,
# because the report shows them verbatim to the developer.


async def _retries_token_budget(ctx: ScenarioContext) -> Outcome:
    ctx.serve_refusal(TokenBudgetExceeded)
    obs = await ctx.run()
    if obs.wire_sends > 1:
        return Outcome(
            False,
            f"retried a TokenBudgetExceeded {obs.wire_sends}× — a budget refusal "
            f"is terminal; retrying only burns the same exhausted window",
        )
    if obs.wire_sends == 0:
        return Outcome(True, "made no model call, so nothing was retried")
    return Outcome(True, "issued one call and did not retry the budget refusal")


async def _swallows_pii_as_generic(ctx: ScenarioContext) -> Outcome:
    ctx.serve_refusal(PIIDetected)
    obs = await ctx.run()
    if obs.raised is None:
        return Outcome(
            True, "handled the PII refusal without leaking a generic exception"
        )
    if isinstance(obs.raised, PIIDetected):
        return Outcome(True, "surfaced the refusal as a typed PIIDetected")
    if isinstance(obs.raised, DonkeyError):
        return Outcome(
            False,
            f"raised {type(obs.raised).__name__}, not PIIDetected — the refusal "
            f"was misclassified",
        )
    return Outcome(
        False,
        f"raised a bare {type(obs.raised).__name__} — the PII refusal was "
        f"swallowed as a generic error (bridge it with classify())",
    )


async def _correlation_id_propagated(ctx: ScenarioContext) -> Outcome:
    ctx.serve_success()
    obs = await ctx.run(correlation_id=PROBE_CORRELATION_ID)
    if PROBE_CORRELATION_ID in obs.log_text:
        return Outcome(True, "emitted the run's correlation id in its own logs")
    return Outcome(
        False,
        "did not emit the correlation id in any log record — read it from "
        "current_correlation_id() and include it when you log",
    )


async def _works_without_budget_headers(ctx: ScenarioContext) -> Outcome:
    ctx.serve_success(budget_headers=False)
    obs = await ctx.run()
    if obs.raised is None:
        return Outcome(
            True, "completed a run when the gateway returned no budget headers"
        )
    return Outcome(
        False,
        f"raised {type(obs.raised).__name__} when budget headers were absent — "
        f"the agent must tolerate an unobserved budget",
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="retries_token_budget",
        title="Retries a budget refusal",
        question="Does your agent retry a TokenBudgetExceeded? It must not.",
        check=_retries_token_budget,
    ),
    Scenario(
        name="swallows_pii_as_generic",
        title="Swallows PII as a generic error",
        question="Does your agent surface PIIDetected as a typed refusal, "
        "or lose it as a generic exception?",
        check=_swallows_pii_as_generic,
    ),
    Scenario(
        name="correlation_id_propagated",
        title="Propagates the correlation id",
        question="Does your agent carry the run's correlation id into its logs?",
        check=_correlation_id_propagated,
    ),
    Scenario(
        name="works_without_budget_headers",
        title="Works without budget headers",
        question="Does your agent still work when the gateway sends no "
        "budget headers?",
        check=_works_without_budget_headers,
    ),
)

SCENARIO_NAMES: frozenset[str] = frozenset(s.name for s in SCENARIOS)


def validate_known_limitations(mapping: object) -> dict[str, str]:
    """Validate an agent's ``KNOWN_LIMITATIONS`` and return it as a plain dict.

    An exemption is an *asserted* claim, never a silent skip (the conformance kit): every key
    must name a real scenario and every reason must be a non-empty string. A bad
    key or an empty reason raises — the plugin lets that surface at collection
    time so the run fails loudly rather than quietly excusing a scenario.
    """
    if mapping is None:
        return {}
    if not isinstance(mapping, dict):
        raise TypeError(
            "KNOWN_LIMITATIONS must be a dict mapping a scenario name to a "
            f"non-empty reason, got {type(mapping).__name__}"
        )
    out: dict[str, str] = {}
    for name, reason in mapping.items():
        if name not in SCENARIO_NAMES:
            raise ValueError(
                f"KNOWN_LIMITATIONS names unknown scenario {name!r}; valid "
                f"scenarios are {sorted(SCENARIO_NAMES)}"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                f"KNOWN_LIMITATIONS[{name!r}] must be a non-empty reason string "
                f"— an asserted exemption, never a silent skip"
            )
        out[name] = reason
    return out
