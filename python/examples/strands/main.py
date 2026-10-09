"""Strands Agents adapter example (BG §1.8).

Supported and conformance-tested (BG §1.8): ``build(donkey)`` makes one governed
call through ``strands.Agent.invoke_async`` and passes the public conformance kit.

Demonstrates constructing a native ``strands.models.openai.OpenAIModel``
pointed at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.strands import model
    m = model("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route ``OpenAIModel`` calls)
and §8 records the ``OpenAIModel`` constructor and its kwargs.
``main()`` builds the model; ``build()`` wraps it in a ``strands.Agent`` and
drives one turn, which is what the conformance kit runs.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import ConfigError, classify
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.strands import model

if TYPE_CHECKING:
    from strands import Agent

logger = logging.getLogger("examples.strands")


def _refusal(exc: BaseException) -> BaseException:
    """The typed Donkey error behind ``exc``, or ``exc`` itself.

    Strands surfaces a proxy refusal as the OpenAI client's own error (or a
    Strands wrapper around it). The gateway's HTTP response sits on that error
    or on its cause chain; ``classify()`` turns it into ``PIIDetected``,
    ``TokenBudgetExceeded`` and the rest."""
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


class StrandsAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through ``strands.Agent``."""

    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    async def run(self, text: str) -> object:
        logger.info("triage: calling the model", extra={"correlation_id": current_correlation_id()})
        try:
            return await self._agent.invoke_async(text)
        except Exception as exc:
            typed = _refusal(exc)
            if typed is exc:
                raise
            raise typed from exc


def build(donkey: Donkey) -> StrandsAgent:
    """Wrap the governed ``OpenAIModel`` in a ``strands.Agent`` and return an
    agent that makes one call through ``Agent.invoke_async``. The default
    ``Agent`` is safe here: ``model()`` raises a budget ``429`` as the typed
    ``TokenBudgetExceeded``, which Strands' throttle retry leaves alone, so the
    refusal is sent once. Only an ``OpenAIModel`` built from
    ``connection_kwargs()`` needs ``retry_strategy=None``."""
    from strands import Agent

    agent = Agent(
        model=donkey.strands.model(os.environ.get("DEMO_MODEL", "gpt-4o")),
        callback_handler=None,
    )
    return StrandsAgent(agent)


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
