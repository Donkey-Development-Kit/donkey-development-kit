"""CrewAI adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native ``crewai.LLM`` pointed at the governed
Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.crewai import llm
    model = llm("gpt-4o")

Honest status (verification discipline / docs/verified-apis.md §8): the proxy *contract* (base URL,
client_id/secret auth, attribution headers) is live-verified. ``crewai.LLM`` wraps LiteLLM, so
the OpenAI-compatible route uses the ``openai/`` model prefix and header
injection via ``extra_headers``; LiteLLM owns the transport, so per-run
correlation degrades (a documented conformance exemption). No live
inference call is attempted here: CrewAI LLMs are driven through a ``Crew`` /
``Agent``, and guessing that runtime call risks inventing an API (verification discipline).
Construction is this example's verified surface.
"""

from __future__ import annotations

import os

from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.crewai import llm


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
        model = llm(model_id)
    except ImportError:
        print("CrewAI not installed. Install it with:")
        print('    pip install "donkey-kit[crewai]"')
        return
    except ConfigError as e:
        print(f"Config error: {e}")
        return

    print(f"Constructed native object: {type(model).__module__}.{type(model).__name__}")
    print(
        "Construction is the SDK's verified surface; pass this object into a "
        "crewai Agent/Crew per CrewAI's own docs — that runtime call is "
        "UNVERIFIED here and deliberately not guessed (verification discipline)."
    )


if __name__ == "__main__":
    main()
