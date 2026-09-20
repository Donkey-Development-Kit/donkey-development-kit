"""LlamaIndex adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native
``llama_index.llms.openai_like.OpenAILike`` pointed at the governed Agent Fabric
LLM proxy with a single factory call:

    from donkey_kit.integrations.llamaindex import llm
    m = llm("gpt-4o")

Honest status (verification discipline / docs/verified-apis.md §8): the proxy *contract* (base URL,
client_id/secret auth, attribution headers) is live-verified, and ``OpenAILike``/its kwargs
are verified per the FACTS table — including ``is_chat_model=True``, which
the factory always sets (``OpenAILike`` defaults it to ``False``, which
silently routes to the completions endpoint against a chat-only proxy; the
single most common LlamaIndex-with-a-gateway bug). What is NOT attempted
here is a live inference call: guessing the right one-line LlamaIndex call
(``.chat``, ``.achat``, ``.complete``, ...) risks inventing an API (verification discipline).
Construction is this example's verified surface — once you have ``m``, use
it with LlamaIndex's own query/chat engines per its own docs.
"""

from __future__ import annotations

import os

from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.llamaindex import llm


def _missing_env() -> list[str]:
    names = (
        "DONKEY_LLM_PROXY_URL",
        "DONKEY_LLM_PROXY_CLIENT_ID",
        "DONKEY_LLM_PROXY_CLIENT_SECRET",
    )
    return [n for n in names if not os.environ.get(n)]


def main() -> None:
    missing = _missing_env()
    if missing:
        print("Set the following environment variables and re-run:")
        print('    export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"  # no /v1')
        print('    export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"')
        print('    export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"')
        return

    model_id = os.environ.get("DEMO_MODEL", "gpt-4o")

    try:
        m = llm(model_id)
    except ImportError:
        print("LlamaIndex not installed. Install it with:")
        print('    pip install "donkey-kit[llamaindex]"')
        return
    except ConfigError as e:
        print(f"Config error: {e}")
        return

    print(f"Constructed native object: {type(m).__module__}.{type(m).__name__}")
    print(
        "Construction is the SDK's verified surface; drive this object with "
        "LlamaIndex's own query/chat engine API (see this example's README) "
        "— that runtime call is UNVERIFIED here and deliberately not guessed "
        "(verification discipline)."
    )


if __name__ == "__main__":
    main()
