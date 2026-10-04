# Strands Agents example

Supported at connection_kwargs(). The example's `build(donkey)` makes one governed
call through `strands.Agent.invoke_async` and runs through the public conformance kit
(`tests/conformance/test_adapter_contract.py`).

**What this shows.** A one-line factory call gets you a *native*
`strands.models.openai.OpenAIModel` pointed at the governed Agent Fabric LLM
proxy — correct `base_url`, `client_id`/`client_secret` header auth (not
bearer), attribution headers, and the SDK's shared transport, all forwarded
through Strands' `client_args`. Strands forwards `client_args` straight to
the underlying OpenAI client, so both header AND transport injection are
available (full injection, like LangGraph). The returned object is Strands'
own class, not a wrapper. `main()` only constructs the object; `build(donkey)` wraps it in a
`strands.Agent` and drives one turn with `invoke_async`, which is what the
conformance kit runs.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[Strands Agents](https://docs.donkey-kit.dev/frameworks/strands)**.
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

The script reads its settings the way the SDK does (environment variables, then
`.donkey-kit.toml`) and stops with the SDK's own `ConfigError`, listing every
missing setting, when one is absent. For a model-wallet proxy, set
`DONKEY_LLM_PROXY_AUTH=jwt` and `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` instead of
the client id and secret; the wallet JWT comes from an `AuthProvider` you pass
to `Donkey(llm_auth=...)` in your own code
([Configuration](https://docs.donkey-kit.dev/reference/configuration#jwt--model-wallet-auth-mode)).

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
    stream=False,  # a Gemini-routed proxy sends no chunk deltas when streaming
)
```

The factory (`donkey_kit.integrations.strands.model`) fills in all of
`client_args` from one governed config source, including the SDK's shared
transport.

## Links

- Strands Agents docs: see the framework's official documentation
