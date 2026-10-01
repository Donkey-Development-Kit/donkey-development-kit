# Anthropic SDK example

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

**What this shows.** A one-line factory call gets you a *native*
`anthropic.AsyncAnthropic` client already pointed at the governed Agent Fabric LLM
proxy — `client_id`/`client_secret` header auth (not bearer), attribution
headers, and the SDK's shared transport (retry/telemetry hooks), on `anthropic`
0.x and 1.x alike. The returned object is Anthropic's own client, not a wrapper.

**Divergence, by design — the framework wins (BG §1.8).** Anthropic's native surface is a *client*,
and the model id is a per-call argument (`c.messages.create(model=..., ...)`),
not a constructor one. So this adapter exposes `client()` rather than the
`model(...)` factory the OpenAI-compatible adapters use.

**Proxy route (docs/verified-apis.md §2, #304).** MuleSoft Model Proxy offers a
native **Anthropic** ingress Format — one of three selectable Formats (OpenAI /
Gemini / Anthropic) fixed at proxy creation. A `Format=Anthropic` proxy serves the Anthropic Messages API natively at
`POST /<base-path>/v1/messages` (an OpenAI-shaped `/chat/completions` request 404s
there). Usage caveat: the SDK's own default DDK proxies are `Format=OpenAI`, so
this client pointed at them reaches Claude only as an *upstream provider*, not
natively. Override `base_url` via `**kw` to point at a `Format=Anthropic` proxy to
use the native surface. This example does not make a live call.

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

The script reads its settings the way the SDK does (environment variables, then
`.donkey-kit.toml`) and stops with the SDK's own `ConfigError`, listing every
missing setting, when one is absent. For a model-wallet proxy, set
`DONKEY_LLM_PROXY_AUTH=jwt` and `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` instead of
the client id and secret; the wallet JWT comes from an `AuthProvider` you pass
to `Donkey(llm_auth=...)` in your own code
([Configuration](https://donkey-development-kit.github.io/donkey-development-kit/reference/configuration#jwt--model-wallet-auth-mode)).

## The manual equivalent

The factory call is equivalent to building `AsyncAnthropic` yourself with the
governed connection values (BG §1.8):

```python
import httpx2  # anthropic 0.x is built on httpx: use httpx.AsyncClient there
from anthropic import AsyncAnthropic

c = AsyncAnthropic(
    base_url=DONKEY_LLM_PROXY_URL,   # a Format=Anthropic proxy: the native /v1/messages route (docs/verified-apis.md §2)
    api_key="unused",                  # proxy enforces client_id/client_secret headers
    default_headers={
        "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
        "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
    },
    http_client=httpx2.AsyncClient(...),  # your own transport, retries, hooks
    max_retries=0,
)
```

The factory (`donkey_kit.integrations.anthropic.client`) fills in `base_url`,
`api_key`, `default_headers`, and the SDK's shared transport from one governed
config source.

**`anthropic` 0.x and 1.x (#701).** `anthropic` 1.0 moved from `httpx` to
`httpx2` and rejects an `httpx` client, so the factory passes the shared
`httpx.AsyncClient` on 0.x and, on 1.0 and later, an `httpx2.AsyncClient` whose
transport sends every request through that same shared client. Governed
headers, retries, telemetry and `donkey.last_call` behave the same on both.

## Links

- Anthropic Python SDK: https://github.com/anthropics/anthropic-sdk-python
