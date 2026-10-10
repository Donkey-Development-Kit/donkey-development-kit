"""OpenAI Agents SDK adapter example (BG §1.8).

Supported and conformance-tested (BG §1.8): ``build(donkey)`` makes one governed
call through ``agents.Runner.run`` and passes the public conformance kit.

Demonstrates constructing a native ``agents.OpenAIChatCompletionsModel`` pointed
at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.openai_agents import model
    m = model("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route
``OpenAIChatCompletionsModel`` calls) and §8 records the constructor and its
kwargs. Because the adapter builds the underlying ``AsyncOpenAI`` client
itself, header AND transport injection are both available (full injection).
``main()`` builds the model; ``build()`` passes it to an ``agents.Agent`` and
drives one turn with ``Runner``, which is what the conformance kit runs.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import ConfigError, classify
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.openai_agents import model

if TYPE_CHECKING:
    from agents import Agent

logger = logging.getLogger("examples.openai_agents")


def _refusal(exc: BaseException) -> BaseException:
    """The typed Donkey error behind ``exc``, or ``exc`` itself.

    The Agents SDK lets the OpenAI client's ``APIStatusError`` escape ``Runner``;
    the gateway's HTTP response sits on it or on its cause chain, and
    ``classify()`` turns that into ``PIIDetected``, ``TokenBudgetExceeded`` and
    the rest."""
    seen: set[int] = set()
    current: BaseException | None = exc
    response = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        candidate = getattr(current, "response", None)
        if getattr(candidate, "status_code", 0) >= 400:
            response = candidate
        current = current.__cause__ or current.__context__
    return exc if response is None else classify(response)


class RunnerAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through ``agents.Runner``."""

    def __init__(self, agent: Agent[Any]) -> None:
        self._agent = agent

    async def run(self, text: str) -> object:
        from agents import RunConfig, Runner

        logger.info("runner: calling the model", extra={"correlation_id": current_correlation_id()})
        try:
            # Tracing off: the SDK's default trace exporter posts to OpenAI, not the proxy.
            return await Runner.run(self._agent, text, run_config=RunConfig(tracing_disabled=True))
        except Exception as exc:
            typed = _refusal(exc)
            if typed is exc:
                raise
            raise typed from exc


def build(donkey: Donkey) -> RunnerAgent:
    """Hand the governed model to an ``agents.Agent`` and return an agent that
    makes one call through ``Runner.run``."""
    from agents import Agent

    agent = Agent(
        name="triage",
        instructions="Reply briefly.",
        model=donkey.openai_agents.model(os.environ.get("DEMO_MODEL", "gpt-4o")),
    )
    return RunnerAgent(agent)


def main() -> None:
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
