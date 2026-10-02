"""Google ADK adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native ``google.adk.models.lite_llm.LiteLlm``
pointed at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.adk import model
    m = model("gpt-4o")  # sent to LiteLLM as "openai/gpt-4o"

On a ``Format=Gemini`` proxy, ``gemini("gemini-2.5-flash")`` from the same
module returns ADK's native ``google.adk.models.Gemini`` instead (see README).

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/chat/completions`` route LiteLLM calls) and §8
records the ``LiteLlm`` constructor and its kwargs. This example only builds
the model. It makes no inference call, because ADK drives models through its
own ``Runner``/``Agent`` session machinery, not a one-line method on the model
object, and guessing that call risks inventing an API (verification
discipline). Once you have ``m``, wire it into your own ADK ``Agent``/``Runner``
per ADK's own docs.
"""

from __future__ import annotations

import os

from donkey_kit import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.adk import model


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
        "ADK's own Agent/Runner API (see this example's README) — that "
        "runtime call is UNVERIFIED here and deliberately not guessed (verification discipline)."
    )


if __name__ == "__main__":
    main()
