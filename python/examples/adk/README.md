# Google ADK example

Supported at connection_kwargs(). The example's `build(donkey)` makes one governed
call through ADK's `InMemoryRunner` and runs through the public conformance kit
(`tests/conformance/test_adapter_contract.py`).

**What this shows.** A one-line factory call gets you a *native*
`google.adk.models.lite_llm.LiteLlm` pointed at the governed Agent Fabric LLM
proxy — correct `api_base`, `client_id`/`client_secret` auth via
`extra_headers` (not bearer), and the model id auto-prefixed with `openai/`
so LiteLLM routes it correctly. The returned object is ADK's own class, not
a wrapper. LiteLLM takes a pre-built OpenAI client, so the adapter hands it one that
sends through the SDK's shared client (per-run correlation, `last_call`).
`main()` only constructs the object; `build(donkey)` wires it into an
`LlmAgent` and an `InMemoryRunner` and drives one turn, which is what the
conformance kit runs.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[Google ADK](https://docs.donkey-kit.dev/frameworks/adk)**.
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

The script reads its settings the way the SDK does (environment variables, then
`.donkey-kit.toml`) and stops with the SDK's own `ConfigError`, listing every
missing setting, when one is absent. For a model-wallet proxy, set
`DONKEY_LLM_PROXY_AUTH=jwt` and `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` instead of
the client id and secret; the wallet JWT comes from an `AuthProvider` you pass
to `Donkey(llm_auth=...)` in your own code
([Configuration](https://docs.donkey-kit.dev/reference/configuration#jwt--model-wallet-auth-mode)).

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

## Native Gemini (`Format=Gemini` proxy)

On a proxy provisioned **Format = Gemini**, `gemini()` returns ADK's native
`google.adk.models.Gemini` with the SDK's shared http client injected, so
per-run correlation, spans, usage and `donkey.last_call` all work. Pass
`base_url` when the Gemini proxy is not `DONKEY_LLM_PROXY_URL`:

```python
from donkey_kit.integrations.adk import gemini

m = gemini("gemini-2.5-flash", base_url="https://<ingress-gw>/<gemini-instance>/")
```

The manual equivalent is
`Gemini(model="gemini-2.5-flash", **donkey.adk.gemini_connection_kwargs())`;
the docs page lists every kwarg it fills in.

## Links

- Google Agent Development Kit (ADK) docs: see the framework's official
  documentation
