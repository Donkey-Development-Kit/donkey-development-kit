# Anthropic SDK example

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

**What this shows.** A one-line factory call gets you a *native*
`anthropic.AsyncAnthropic` client already pointed at the governed Agent Fabric LLM
proxy — `client_id`/`client_secret` header auth (not bearer), attribution
headers, and the SDK's shared transport (retry/telemetry hooks). The returned
object is Anthropic's own client, not a wrapper.

**Divergence, by design — the framework wins (BG §1.8).** Anthropic's native surface is a *client*,
and the model id is a per-call argument (`c.messages.create(model=..., ...)`),
not a constructor one. So this adapter exposes `client()` rather than the
`model(...)` factory the OpenAI-compatible adapters use.

**Unverified dependency (docs/verified-apis.md §2, #304).** MuleSoft Model Proxy
offers a native **Anthropic** ingress Format — one of three selectable Formats
(OpenAI / Gemini / Anthropic) fixed at proxy creation — so the Anthropic-native
route is a real product capability. But every DDK proxy is `Format=OpenAI`, so
this client pointed at them reaches Claude only as an *upstream provider*, not
natively; and the exact Anthropic ingress path (likely `/v1/messages`) + live
behavior are not yet captured against a `Format=Anthropic` proxy. Override
`base_url` via `**kw` to point at a `Format=Anthropic` proxy to use the native
surface. The first `client()` call emits a one-time unverified-route warning;
this example does not make a live call.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[Anthropic SDK](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/anthropic)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run

```bash
pip install "donkey-kit[anthropic]"

export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"

python examples/anthropic/main.py
```

## The manual equivalent

The factory call is equivalent to building `AsyncAnthropic` yourself with the
governed connection values (BG §1.8):

```python
import httpx
from anthropic import AsyncAnthropic

c = AsyncAnthropic(
    base_url=DONKEY_LLM_PROXY_URL,   # UNVERIFIED path/behavior: use a Format=Anthropic proxy
    api_key="unused",                  # proxy enforces client_id/client_secret headers
    default_headers={
        "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
        "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
    },
    http_client=httpx.AsyncClient(...),  # your own transport, retries, hooks
    max_retries=0,
)
```

The factory (`donkey_kit.integrations.anthropic.client`) fills in `base_url`,
`api_key`, `default_headers`, and the SDK's shared transport from one governed
config source.

## Links

- Anthropic Python SDK: https://github.com/anthropics/anthropic-sdk-python
