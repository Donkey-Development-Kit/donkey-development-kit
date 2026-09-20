# Strands Agents example

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

**What this shows.** A one-line factory call gets you a *native*
`strands.models.openai.OpenAIModel` pointed at the governed Agent Fabric LLM
proxy — correct `base_url`, `client_id`/`client_secret` header auth (not
bearer), attribution headers, and the SDK's shared transport, all forwarded
through Strands' `client_args`. Strands forwards `client_args` straight to
the underlying OpenAI client, so both header AND transport injection are
available (full injection, like LangGraph). The returned object is Strands'
own class, not a wrapper. This example only constructs the object; it
deliberately does not attempt a live inference call, since Strands models
are normally driven through a `strands.Agent` session rather than a simple
method call on the model.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[Strands Agents](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/strands)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run

```bash
pip install "donkey-kit[strands]"

export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"

python examples/strands/main.py
```

## The manual equivalent

The factory call is equivalent to building `OpenAIModel` yourself with the
governed connection values (BG §1.8):

```python
import httpx
from strands.models.openai import OpenAIModel

m = OpenAIModel(
    model_id="gpt-4o",
    client_args={
        "base_url": DONKEY_LLM_PROXY_URL,
        "api_key": "unused",  # the proxy enforces client_id/client_secret headers instead
        "default_headers": {
            "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
            "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
        },
        "http_client": httpx.AsyncClient(...),
    },
)
```

The factory (`donkey_kit.integrations.strands.model`) fills in all of
`client_args` from one governed config source, including the SDK's shared
transport.

## Links

- Strands Agents docs: see the framework's official documentation
