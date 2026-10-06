"""LlamaIndex adapter example (BG §1.8).

Supported and conformance-tested (BG §1.8): ``build(donkey)`` makes one governed
call through ``OpenAILike.acomplete`` and passes the public conformance kit.

Demonstrates constructing a native
``llama_index.llms.openai_like.OpenAILike`` pointed at the governed Agent Fabric
LLM proxy with a single factory call:

    from donkey_kit.integrations.llamaindex import llm
    m = llm("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route ``OpenAILike`` calls)
and §8 records the ``OpenAILike`` constructor and its kwargs, including
``is_chat_model=True``. The factory always sets it: ``OpenAILike`` defaults it
to ``False``, which silently routes to the completions endpoint against a
chat-only proxy, the most common LlamaIndex-with-a-gateway bug.
``main()`` builds the LLM; ``build()`` makes one ``acomplete`` call inside
``typed_refusals()``, which is what the conformance kit runs.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.llamaindex import llm, typed_refusals

if TYPE_CHECKING:
    from llama_index.llms.openai_like import OpenAILike

logger = logging.getLogger("examples.llamaindex")

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


class CompletionAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through the LLM's ``acomplete``."""

    def __init__(self, model: OpenAILike) -> None:
        self._model = model

    async def run(self, text: str) -> object:
        logger.info("llm: calling the model", extra={"correlation_id": current_correlation_id()})
        # A proxy refusal comes back typed, not as the OpenAI client's APIStatusError.
        with typed_refusals():
            return await self._model.acomplete(text)


def build(donkey: Donkey) -> CompletionAgent:
    """Build the governed ``OpenAILike`` from ``donkey`` and return an agent that
    makes one call through ``acomplete``."""
    return CompletionAgent(donkey.llamaindex.llm(os.environ.get("DEMO_MODEL", "gpt-4o")))


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
        m = llm(model_id)
    except (ImportError, ConfigError) as e:
        print(e)
        return

    print(f"Constructed native object: {type(m).__module__}.{type(m).__name__}")
    print("Run build(donkey) for the governed call through the framework's own entry point.")


if __name__ == "__main__":
    main()
