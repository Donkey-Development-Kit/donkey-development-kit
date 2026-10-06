"""Anthropic SDK adapter example (BG §1.8).

Supported and conformance-tested (BG §1.8): ``build(donkey)`` makes one governed
call through ``AsyncAnthropic.messages.create`` and passes the public conformance kit.

Demonstrates constructing a native ``anthropic.AsyncAnthropic`` client pointed
at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.anthropic import client
    c = client()   # the model id is a per-call argument, not a constructor one

PROXY ROUTE (docs/verified-apis.md §2, #304): MuleSoft Model Proxy offers a
native **Anthropic** ingress Format (one of three — OpenAI / Gemini / Anthropic —
fixed at proxy creation). A ``Format=Anthropic`` proxy serves the Anthropic Messages API natively at
``POST /<base-path>/v1/messages``. Usage caveat: the SDK's own default DDK proxies
are ``Format=OpenAI``, so this client pointed at them reaches Claude only as an
*upstream provider*, not natively (``/v1/messages`` 404s there). Point
``base_url`` (via ``**kw``) at a ``Format=Anthropic`` proxy to use the native
surface. ``main()`` only constructs the client; ``build()`` makes the one governed
``messages.create`` call the conformance kit drives.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from donkey_kit import Donkey, DonkeyConfig
from donkey_kit.core.errors import ConfigError, classify
from donkey_kit.core.telemetry import current_correlation_id
from donkey_kit.integrations.anthropic import client

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

logger = logging.getLogger("examples.anthropic")

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


class MessagesAgent:
    """Minimal agent surface the conformance harness drives: an awaitable
    ``run(text)`` that sends one turn through ``messages.create``."""

    def __init__(self, anthropic_client: AsyncAnthropic, model_id: str) -> None:
        self._client = anthropic_client
        self._model_id = model_id

    async def run(self, text: str) -> object:
        from anthropic import APIStatusError

        logger.info(
            "messages: calling the model",
            extra={"correlation_id": current_correlation_id()},
        )
        try:
            return await self._client.messages.create(
                model=self._model_id,
                max_tokens=256,
                messages=[{"role": "user", "content": text}],
            )
        except APIStatusError as exc:
            # The gateway's refusal rides on the SDK's error; classify() types it.
            raise classify(exc.response) from exc


def build(donkey: Donkey) -> MessagesAgent:
    """Build the governed ``AsyncAnthropic`` from ``donkey`` and return an agent
    that makes one call through ``client.messages.create``."""
    model_id = os.environ.get("DEMO_MODEL", "claude-sonnet-4-5")
    return MessagesAgent(donkey.anthropic.client(), model_id)


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

    try:
        c = client()
    except (ImportError, ConfigError) as e:
        print(e)
        return

    print(f"Constructed native object: {type(c).__module__}.{type(c).__name__}")
    print(
        "A Format=Anthropic proxy serves the native Messages route at "
        "POST /<base-path>/v1/messages (docs/verified-apis.md §2, #304). Point "
        "base_url at a Format=Anthropic proxy (DDK defaults are Format=OpenAI), "
        "then call c.messages.create(model=..., ...) per Anthropic's own docs."
    )


if __name__ == "__main__":
    main()
