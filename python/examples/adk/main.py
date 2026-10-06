"""Google ADK adapter example (BG §1.8).

Supported and conformance-tested (BG §1.8): ``build(donkey)`` makes one governed
call through ADK's ``InMemoryRunner`` and passes the public conformance kit.

Demonstrates constructing a native ``google.adk.models.lite_llm.LiteLlm``
pointed at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.adk import model
    m = model("gpt-4o")  # sent to LiteLLM as "openai/gpt-4o"

On a ``Format=Gemini`` proxy, ``gemini("gemini-2.5-flash")`` from the same
module returns ADK's native ``google.adk.models.Gemini`` instead (see README).

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route LiteLLM calls) and §8
records the ``LiteLlm`` constructor and its kwargs.
``main()`` builds the model; ``build()`` wires it into an ``LlmAgent`` and an
``InMemoryRunner`` and drives one turn, which is what the conformance kit runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import ConfigError, classify
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.adk import model

if TYPE_CHECKING:
    from google.adk.runners import Runner

logger = logging.getLogger("examples.adk")

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


_APP_NAME = "triage"
_USER_ID = "support"


def _typed_refusal(
    callback_context: object,  # noqa: ARG001 - ADK callback protocol
    llm_request: object,  # noqa: ARG001 - ADK callback protocol
    error: Exception,
) -> None:
    """ADK's ``on_model_error_callback``: raise a proxy refusal as the typed error.

    LiteLLM reports the gateway's rejection as its own error. The gateway's HTTP
    response sits on that error or on its cause chain, and ``classify()`` turns it
    into ``PIIDetected``, ``TokenBudgetExceeded`` and the rest. The callback runs
    where the model call failed, before ADK's ``Runner`` re-raises the error and
    drops that chain. An error with no gateway response behind it passes through."""
    seen: set[int] = set()
    current: BaseException | None = error
    response = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = getattr(current, "response", None)
        if getattr(candidate, "status_code", 0) >= 400:
            response = candidate
        current = current.__cause__ or current.__context__
    if response is not None:
        raise classify(response) from error


class RunnerAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through ADK's ``Runner``."""

    def __init__(self, runner: Runner) -> None:
        self._runner = runner

    async def run(self, text: str) -> object:
        from google.genai import types

        logger.info("runner: calling the model", extra={"correlation_id": current_correlation_id()})
        session = await self._runner.session_service.create_session(
            app_name=_APP_NAME, user_id=_USER_ID
        )
        message = types.Content(role="user", parts=[types.Part(text=text)])
        last_event = None
        async for event in self._runner.run_async(
            user_id=_USER_ID, session_id=session.id, new_message=message
        ):
            last_event = event
        return last_event


def build(donkey: Donkey) -> RunnerAgent:
    """Hand the governed ``LiteLlm`` to an ``LlmAgent`` and return an agent that
    makes one call through ``InMemoryRunner.run_async``."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner

    agent = LlmAgent(
        name=_APP_NAME,
        model=donkey.adk.model(os.environ.get("DEMO_MODEL", "gpt-4o")),
        instruction="Reply briefly.",
        on_model_error_callback=_typed_refusal,
    )
    return RunnerAgent(InMemoryRunner(agent=agent, app_name=_APP_NAME))


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
        m = model(model_id)
    except (ImportError, ConfigError) as e:
        print(e)
        return

    print(f"Constructed native object: {type(m).__module__}.{type(m).__name__}")
    print("Run build(donkey) for the governed call through the framework's own entry point.")


if __name__ == "__main__":
    main()
