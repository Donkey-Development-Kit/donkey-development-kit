"""Anthropic SDK adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native ``anthropic.AsyncAnthropic`` client pointed
at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.anthropic import client
    c = client()   # the model id is a per-call argument, not a constructor one

UNVERIFIED DEPENDENCY (docs/verified-apis.md §2, #304): MuleSoft Model Proxy
offers a native **Anthropic** ingress Format (one of three — OpenAI / Gemini /
Anthropic — fixed at proxy creation), so the Anthropic-native route is a real
product capability. But every DDK proxy is ``Format=OpenAI``, so this client
pointed at them reaches Claude only as an *upstream provider*, not natively; and
the exact Anthropic ingress path + live behavior are not yet captured against a
``Format=Anthropic`` proxy. Point ``base_url`` (via ``**kw``) at a
``Format=Anthropic`` proxy to use the native surface. The first ``client()``
call emits a one-time unverified-route warning. This example constructs the
client but does NOT make a live call.
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
        "Construction is the SDK's verified surface. The Anthropic ingress Format "
        "is documented but the exact path + live behavior are UNVERIFIED "
        "(docs/verified-apis.md §2, #304) — point base_url at a Format=Anthropic "
        "proxy, then call c.messages.create(model=..., ...) per Anthropic's own docs."
    )


if __name__ == "__main__":
    main()
