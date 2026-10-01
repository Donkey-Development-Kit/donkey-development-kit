# LlamaIndex example

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

**What this shows.** A one-line factory call gets you a *native*
`llama_index.llms.openai_like.OpenAILike` pointed at the governed Agent Fabric
LLM proxy — correct `api_base`, `client_id`/`client_secret` header auth (not
bearer), attribution headers, and `is_chat_model=True` set for you.
`OpenAILike` defaults `is_chat_model` to `False`, which silently routes
requests to the completions endpoint and fails against a chat-only proxy —
the single most common LlamaIndex-with-a-gateway bug, and one this factory
eliminates by construction. The returned object is LlamaIndex's own class,
not a wrapper. This example only constructs the object; it deliberately
does not attempt a live inference call, since guessing which one-line
LlamaIndex call to use (`.chat`, `.achat`, `.complete`, ...) risks inventing
an API.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[LlamaIndex](https://docs.donkey-kit.dev/frameworks/llamaindex)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run

```bash
pip install "donkey-kit[llamaindex]"

export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"

python examples/llamaindex/main.py
```

The script reads its settings the way the SDK does (environment variables, then
`.donkey-kit.toml`) and stops with the SDK's own `ConfigError`, listing every
missing setting, when one is absent. For a model-wallet proxy, set
`DONKEY_LLM_PROXY_AUTH=jwt` and `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` instead of
the client id and secret; the wallet JWT comes from an `AuthProvider` you pass
to `Donkey(llm_auth=...)` in your own code
([Configuration](https://docs.donkey-kit.dev/reference/configuration#jwt--model-wallet-auth-mode)).

## The manual equivalent

The factory call is equivalent to building `OpenAILike` yourself with the
governed connection values (BG §1.8):

```python
from llama_index.llms.openai_like import OpenAILike

m = OpenAILike(
    model="gpt-4o",
    api_base=DONKEY_LLM_PROXY_URL,
    api_key="unused",  # the proxy enforces client_id/client_secret headers instead
    default_headers={
        "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
        "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
    },
    is_chat_model=True,  # never omit — defaults to False and silently breaks
    is_function_calling_model=True,
)
```

The factory (`donkey_kit.integrations.llamaindex.llm`) fills in
`api_base`, `api_key`, `default_headers`, and the two `is_*` flags from one
governed config source.

## Links

- LlamaIndex docs: see the framework's official documentation
