# Google ADK example

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

**What this shows.** A one-line factory call gets you a *native*
`google.adk.models.lite_llm.LiteLlm` pointed at the governed Agent Fabric LLM
proxy — correct `api_base`, `client_id`/`client_secret` auth via
`extra_headers` (not bearer), and the model id auto-prefixed with `openai/`
so LiteLLM routes it correctly. The returned object is ADK's own class, not
a wrapper. Note: LiteLLM owns its own HTTP transport, so (unlike LangGraph)
the SDK's shared http client/retry hooks are not injected here — this is a
documented conformance exemption, not an oversight. This example only
constructs the object; it deliberately does not attempt a live inference
call, since ADK drives models through its own `Runner`/`Agent` session
machinery rather than a simple method call.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[Google ADK](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/adk)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run

```bash
pip install "donkey-kit[adk]"

export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"

python examples/adk/main.py
```

## The manual equivalent

The factory call is equivalent to building `LiteLlm` yourself with the
governed connection values (BG §1.8):

```python
from google.adk.models.lite_llm import LiteLlm

m = LiteLlm(
    model="openai/gpt-4o",  # LiteLLM's OpenAI-compatible route needs this prefix
    api_base=DONKEY_LLM_PROXY_URL,
    api_key="unused",  # the proxy enforces client_id/client_secret headers instead
    extra_headers={
        "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
        "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
    },
)
```

The factory (`donkey_kit.integrations.adk.model`) fills in `api_base`,
`api_key`, `extra_headers`, and the `openai/` prefix from one governed
config source.

## Links

- Google Agent Development Kit (ADK) docs: see the framework's official
  documentation
