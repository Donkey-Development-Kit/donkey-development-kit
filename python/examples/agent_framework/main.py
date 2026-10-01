"""Microsoft Agent Framework adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native Agent Framework OpenAI-compatible chat
client pointed at the governed Agent Fabric LLM proxy with a single factory
call:

    from donkey_kit.integrations.agent_framework import chat_client
    client = chat_client("gpt-4o")

Status: docs/verified-apis.md §2 records the proxy contract (base URL,
client_id/secret auth, the ``/responses`` route ``OpenAIChatClient`` calls)
and §8 records the chat-client class path
(``agent_framework.openai.OpenAIChatClient``) and its kwargs: ``model``,
``base_url``, ``api_key`` and ``default_headers``. Agent Framework is young and
has renamed classes before, so if that import or construction fails the
factory raises ``NotImplementedError`` with a "blocked on verification"
message rather than guessing further. This example only builds the client. It
makes no inference call, because Agent Framework drives chat clients through
its own ``Agent`` object, not a method on the client, and guessing that call
risks inventing an API.
"""

from __future__ import annotations

import os

from donkey_kit import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.agent_framework import chat_client


def main() -> None:
    try:
        DonkeyConfig.from_env().validated(need="llm")
    except ConfigError as e:
        print(e)
        return

    model_id = os.environ.get("DEMO_MODEL", "gpt-4o")

    try:
        client = chat_client(model_id)
    except (ImportError, ConfigError) as e:
        print(e)
        return
    except NotImplementedError as e:
        print(f"Blocked on verification: {e}")
        return

    print(f"Constructed native object: {type(client).__module__}.{type(client).__name__}")
    print(
        "This example stops at construction; drive this object with "
        "Agent Framework's own Agent API (see this example's README) — that "
        "runtime call is UNVERIFIED here and deliberately not guessed (verification discipline)."
    )


if __name__ == "__main__":
    main()
