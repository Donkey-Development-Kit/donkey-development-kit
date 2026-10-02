"""OpenAI Agents SDK adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native ``agents.OpenAIChatCompletionsModel`` pointed
at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.openai_agents import model
    m = model("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route
``OpenAIChatCompletionsModel`` calls) and §8 records the constructor and its
kwargs. Because the adapter builds the underlying ``AsyncOpenAI`` client
itself, header AND transport injection are both available (full injection).
This example only builds the model. It makes no inference call, because the
Agents SDK runs models through an ``agents.Agent`` + ``Runner``, and guessing
that runtime call risks inventing an API (verification discipline). Pass ``m``
into ``agents.Agent(model=m)`` per the SDK's docs.
"""

from __future__ import annotations

import os

from donkey_kit import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.openai_agents import model


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
    print(
        "This example stops at construction; pass this object into "
        "agents.Agent(model=...) and drive it with Runner per the SDK's own "
        "docs — that runtime call is UNVERIFIED here and deliberately not "
        "guessed (verification discipline)."
    )


if __name__ == "__main__":
    main()
