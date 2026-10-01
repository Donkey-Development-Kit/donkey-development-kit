"""CrewAI adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

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
exemption). This example only builds the LLM. It makes no inference call,
because CrewAI LLMs are driven through a ``Crew`` / ``Agent``, and guessing
that runtime call risks inventing an API (verification discipline).
"""

from __future__ import annotations

import os

from donkey_kit import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.crewai import llm


def main() -> None:
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
    print(
        "This example stops at construction; pass this object into a "
        "crewai Agent/Crew per CrewAI's own docs — that runtime call is "
        "UNVERIFIED here and deliberately not guessed (verification discipline)."
    )


if __name__ == "__main__":
    main()
