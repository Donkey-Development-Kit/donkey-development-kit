"""LlamaIndex adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

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
chat-only proxy, the most common LlamaIndex-with-a-gateway bug. This example
only builds the LLM. It makes no inference call, because guessing the right
one-line LlamaIndex call (``.chat``, ``.achat``, ``.complete``, ...) risks
inventing an API (verification discipline). Once you have ``m``, use it with
LlamaIndex's own query/chat engines per its own docs.
"""

from __future__ import annotations

import os

from donkey_kit import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.llamaindex import llm


def main() -> None:
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
    print(
        "This example stops at construction; drive this object with "
        "LlamaIndex's own query/chat engine API (see this example's README) "
        "— that runtime call is UNVERIFIED here and deliberately not guessed "
        "(verification discipline)."
    )


if __name__ == "__main__":
    main()
