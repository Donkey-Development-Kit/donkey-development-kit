"""CrewAI adapter example (BG §1.8).

Supported and conformance-tested (BG §1.8): ``build(donkey)`` makes one governed
call through ``crewai.Agent.kickoff_async`` and passes the public conformance
kit, with every scenario an asserted exemption (``KNOWN_LIMITATIONS``, #740).

Demonstrates constructing a native CrewAI LLM (a ``crewai.BaseLLM``, concretely
``OpenAICompletion``) pointed at the governed Agent Fabric LLM proxy with a
single factory call:

    from donkey_kit.integrations.crewai import llm
    model = llm("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route the OpenAI provider
calls) and §8 records the ``crewai.LLM`` factory. ``crewai.LLM`` is a factory:
the ``openai/`` model prefix plus ``base_url`` routes it to CrewAI's native
OpenAI provider, with header injection via ``extra_headers``. That provider
owns the transport, so per-run correlation degrades (a documented conformance
exemption). ``main()`` builds the LLM; ``build()`` hands it to a ``crewai.Agent`` and
drives one turn with ``kickoff_async``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.crewai import llm

if TYPE_CHECKING:
    from crewai import Agent

logger = logging.getLogger("examples.crewai")

# #748: opt-in strict mode for CI (the nightly framework-legs job, and the PR
# adapter-contract leg). Default behaviour (unset) stays the user-friendly
# "print install guidance and exit 0" path below; strict mode instead lets a
# missing framework's ImportError propagate out of main() (process exit != 0)
# and drives build() through the conformance kit (governed calls, in-process).
_STRICT_ENV = "DONKEY_STRICT_EXAMPLE"


def _strict() -> bool:
    return os.environ.get(_STRICT_ENV) == "1"


def _strict_governed_call() -> None:
    """Strict mode's smoke (#748). Constructs the native object through the real
    ``build(donkey)`` (a missing or drifted framework raises here, so the process
    exits non-zero), then drives ``build`` through the public conformance kit,
    which makes governed calls in-process against the captured gateway fixtures:
    no network, no credentials, no simulator. A scenario this example records in
    ``KNOWN_LIMITATIONS`` is an asserted exemption, not a call (CrewAI owns its
    transport, so its smoke is construction only)."""
    from donkey_kit.conformance.harness import offline_config, run_conformance

    async def go() -> None:
        donkey = Donkey(offline_config())
        try:
            build(donkey)
        finally:
            await donkey.aclose()
        known = globals().get("KNOWN_LIMITATIONS")
        results = await run_conformance(build, known_limitations=known)
        failed = [f"{r.scenario}: {r.detail}" for r in results if r.status == "fail"]
        if failed:
            raise RuntimeError("strict smoke: conformance scenario(s) failed: " + "; ".join(failed))

    asyncio.run(go())


# CrewAI's native OpenAI provider owns the transport: one client_params dict feeds
# both its sync OpenAI and its AsyncOpenAI, and its interceptor path replaces
# http_client with its own httpx client, so the Donkey's client cannot be injected
# (#740). The conformance harness observes the Donkey's transport and blocks every
# other, so no scenario can see a CrewAI call: each is an asserted exemption
# (BG §1.8), never a skip. ``pytest --donkey-conformance`` reads this dict from
# the agent module.
_TRANSPORT_OWNED = (
    "CrewAI's native OpenAI provider owns the transport, so its model calls never "
    "pass through the Donkey client the conformance harness observes (#740); "
    "donkey.crewai reports observes_last_call = False."
)
KNOWN_LIMITATIONS: dict[str, str] = {
    "retries_token_budget": _TRANSPORT_OWNED,
    "swallows_pii_as_generic": _TRANSPORT_OWNED,
    "correlation_id_propagated": _TRANSPORT_OWNED,
    "works_without_budget_headers": _TRANSPORT_OWNED,
}


class KickoffAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through ``crewai.Agent.kickoff_async``."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def run(self, text: str) -> object:
        logger.info("agent: calling the model", extra={"correlation_id": current_correlation_id()})
        return await self._agent.kickoff_async(text)


def build(donkey: Donkey) -> KickoffAgent:
    """Hand the governed LLM to a ``crewai.Agent`` and return an agent that makes
    one call through ``Agent.kickoff_async``."""
    from crewai import Agent

    agent = Agent(
        role="Support triage",
        goal="Draft a one-line reply to a support ticket.",
        backstory="You answer support tickets briefly.",
        llm=donkey.crewai.llm(os.environ.get("DEMO_MODEL", "gpt-4o")),
    )
    return KickoffAgent(agent)


def main() -> None:
    if _strict():
        _strict_governed_call()
        print("Strict smoke OK: constructed the native object and made one governed call.")
        return

    try:
        DonkeyConfig.from_env().validated(need="llm")
    except ConfigError as e:
        print(e)
        return

    model_id = os.environ.get("DEMO_MODEL", "gpt-4o")

    try:
        model = llm(model_id)
    except (ImportError, ConfigError) as e:
        print(e)
        return

    print(f"Constructed native object: {type(model).__module__}.{type(model).__name__}")
    print("Run build(donkey) for the governed call through the framework's own entry point.")


if __name__ == "__main__":
    main()
