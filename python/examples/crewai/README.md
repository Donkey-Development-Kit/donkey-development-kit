# CrewAI example

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

**What this shows.** A one-line factory call gets you a *native* CrewAI LLM
(`crewai.BaseLLM`, concretely `OpenAICompletion`) already pointed at the
governed Agent Fabric LLM proxy — correct base URL,
`client_id`/`client_secret` header auth (not bearer), and attribution headers.
The returned object is CrewAI's own class, not a wrapper, so it drops straight
into a `crewai` `Agent`/`Crew`.

`crewai.LLM` is a factory: the `openai/` model prefix plus `base_url` routes it
to CrewAI's native OpenAI provider, and headers go via `extra_headers`. That
provider owns the transport, so the SDK's per-run correlation ID degrades to
per-client — a documented conformance exemption, the same one ADK has.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[CrewAI](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/crewai)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run

```bash
pip install "donkey-kit[crewai]"

export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"

python examples/crewai/main.py
```

The script reads its settings the way the SDK does (environment variables, then
`.donkey-kit.toml`) and stops with the SDK's own `ConfigError`, listing every
missing setting, when one is absent. For a model-wallet proxy, set
`DONKEY_LLM_PROXY_AUTH=jwt` and `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` instead of
the client id and secret; the wallet JWT comes from an `AuthProvider` you pass
to `Donkey(llm_auth=...)` in your own code
([Configuration](https://donkey-development-kit.github.io/donkey-development-kit/reference/configuration#jwt--model-wallet-auth-mode)).

## The manual equivalent

The factory call is equivalent to building `crewai.LLM` yourself with the
governed connection values (BG §1.8):

```python
from crewai import LLM

model = LLM(
    model="openai/gpt-4o",          # routes to CrewAI's native OpenAI provider
    base_url=DONKEY_LLM_PROXY_URL,
    api_key="unused",               # proxy enforces client_id/client_secret headers
    extra_headers={
        "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
        "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
    },
)
```

The factory (`donkey_kit.integrations.crewai.llm`) fills in the `openai/`
prefix, `base_url`, `api_key`, and `extra_headers` from one governed config
source.

## Links

- CrewAI docs: https://docs.crewai.com/
