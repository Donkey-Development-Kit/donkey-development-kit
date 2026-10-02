"""Anthropic SDK adapter example (BG §1.8).

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

Demonstrates constructing a native ``anthropic.AsyncAnthropic`` client pointed
at the governed Agent Fabric LLM proxy with a single factory call:

    from donkey_kit.integrations.anthropic import client
    c = client()   # the model id is a per-call argument, not a constructor one

PROXY ROUTE (docs/verified-apis.md §2, #304): MuleSoft Model Proxy offers a
native **Anthropic** ingress Format (one of three — OpenAI / Gemini / Anthropic —
fixed at proxy creation). A ``Format=Anthropic`` proxy serves the Anthropic Messages API natively at
``POST /<base-path>/v1/messages``. Usage caveat: the SDK's own default DDK proxies
are ``Format=OpenAI``, so this client pointed at them reaches Claude only as an
*upstream provider*, not natively (``/v1/messages`` 404s there). Point
``base_url`` (via ``**kw``) at a ``Format=Anthropic`` proxy to use the native
surface. This example constructs the client but does NOT make a live call.
"""

from __future__ import annotations

from donkey_kit import DonkeyConfig
from donkey_kit.core.errors import ConfigError
from donkey_kit.integrations.anthropic import client


def main() -> None:
    try:
        DonkeyConfig.from_env().validated(need="llm")
    except ConfigError as e:
        print(e)
        return

    try:
        c = client()
    except (ImportError, ConfigError) as e:
        print(e)
        return

    print(f"Constructed native object: {type(c).__module__}.{type(c).__name__}")
    print(
        "A Format=Anthropic proxy serves the native Messages route at "
        "POST /<base-path>/v1/messages (docs/verified-apis.md §2, #304). Point "
        "base_url at a Format=Anthropic proxy (DDK defaults are Format=OpenAI), "
        "then call c.messages.create(model=..., ...) per Anthropic's own docs."
    )


if __name__ == "__main__":
    main()
