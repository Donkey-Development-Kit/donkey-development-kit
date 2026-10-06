"""Microsoft Agent Framework adapter example (BG §1.8).

Supported and conformance-tested (BG §1.8): ``build(donkey)`` makes one governed
call through ``agent_framework.Agent.run`` and passes the public conformance kit.

Demonstrates constructing a native Agent Framework OpenAI-compatible chat
client pointed at the governed Agent Fabric LLM proxy with a single factory
call:

    from donkey_kit.integrations.agent_framework import chat_client
    client = chat_client("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/responses`` route ``OpenAIChatClient`` calls)
and §8 records the chat-client class path
(``agent_framework.openai.OpenAIChatClient``) and its kwargs: ``model``,
``base_url``, ``api_key`` and ``default_headers``. Agent Framework is young and
has renamed classes before, so if that import or construction fails the
factory raises ``NotImplementedError`` with a "blocked on verification"
message rather than guessing further. ``main()`` builds the client; ``build()``
hands it to an ``Agent`` with the policy middleware and drives one turn, which is
what the conformance kit runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.agent_framework import chat_client

if TYPE_CHECKING:
    from agent_framework import Agent

logger = logging.getLogger("examples.agent_framework")

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


class ChatAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through ``agent_framework.Agent.run``."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def run(self, text: str) -> object:
        logger.info("agent: calling the model", extra={"correlation_id": current_correlation_id()})
        return await self._agent.run(text)


def build(donkey: Donkey) -> ChatAgent:
    """Hand the governed chat client to an ``agent_framework.Agent`` and return an
    agent that makes one call through ``Agent.run``. ``policy_middleware()`` ends
    the run on a proxy refusal with the typed error, not the framework's generic
    ``ChatClientException``."""
    from agent_framework import Agent

    agent = Agent(
        client=donkey.agent_framework.chat_client(os.environ.get("DEMO_MODEL", "gpt-4o")),
        name="triage",
        middleware=[donkey.agent_framework.policy_middleware()],
    )
    return ChatAgent(agent)


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
        client = chat_client(model_id)
    except (ImportError, ConfigError) as e:
        print(e)
        return
    except NotImplementedError as e:
        print(f"Blocked on verification: {e}")
        return

    print(f"Constructed native object: {type(client).__module__}.{type(client).__name__}")
    print("Run build(donkey) for the governed call through the framework's own entry point.")


if __name__ == "__main__":
    main()
