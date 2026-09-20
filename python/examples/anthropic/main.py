"""Anthropic SDK adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native ``anthropic.AsyncAnthropic`` client pointed
at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.anthropic import client
    c = client()   # the model id is a per-call argument, not a constructor one

Honest status (verification discipline / docs/verified-apis.md §8): the proxy *contract* over the
OpenAI-compatible route (base URL, client_id/secret auth, attribution headers) is live-verified.
What is UNVERIFIED is whether the proxy exposes an **Anthropic-native Messages API
route** at all (an open M0 item) — so this example constructs the client and
emits the SDK's one-time unverified-route warning, but does NOT make a live
call. Once a real Anthropic-native route is confirmed, override ``base_url`` and
drive ``c.messages.create(model=..., ...)`` per Anthropic's own docs.
"""

from __future__ import annotations

import os

from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.anthropic import client


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

    try:
        c = client()
    except ImportError:
        print("Anthropic SDK not installed. Install it with:")
        print('    pip install "donkey-kit[anthropic]"')
        return
    except ConfigError as e:
        print(f"Config error: {e}")
        return

    print(f"Constructed native object: {type(c).__module__}.{type(c).__name__}")
    print(
        "Construction is the SDK's verified surface. The proxy's Anthropic-native "
        "route is UNVERIFIED (docs/verified-apis.md §8) — confirm it, "
        "override base_url if needed, then "
        "call c.messages.create(model=..., ...) per Anthropic's own docs."
    )


if __name__ == "__main__":
    main()
