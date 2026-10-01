"""Strands Agents adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native ``strands.models.openai.OpenAIModel``
pointed at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.strands import model
    m = model("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route ``OpenAIModel`` calls)
and §8 records the ``OpenAIModel`` constructor and its kwargs. This example
only builds the model. It makes no inference call, because Strands models are
normally driven through a ``strands.Agent`` session, not a one-line method on
the model object, and guessing that call risks inventing an API (verification
discipline). Once you have ``m``, wrap it in your own ``strands.Agent`` per
Strands' own docs.
"""

from __future__ import annotations

import os

from donkey_kit import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.strands import model


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
        "This example stops at construction; drive this object with "
        "Strands' own Agent API (see this example's README) — that runtime "
        "call is UNVERIFIED here and deliberately not guessed (verification discipline)."
    )


if __name__ == "__main__":
    main()
