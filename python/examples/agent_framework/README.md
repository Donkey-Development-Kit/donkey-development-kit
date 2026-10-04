# Microsoft Agent Framework example

Supported at connection_kwargs(). The example's `build(donkey)` makes one governed
call through `Agent.run` and runs through the public conformance kit
(`tests/conformance/test_adapter_contract.py`).

**What this shows.** A one-line factory call builds a native Agent Framework
chat client, `agent_framework.openai.OpenAIChatClient`, pointed at the
governed Agent Fabric LLM proxy. That client calls the proxy's `/responses`
route. docs/verified-apis.md §2 records the proxy contract (base URL,
`client_id`/`client_secret` header auth, the route) and §8 records the class
path and its kwargs (`model`, not `model_id`). Agent Framework is a young
package that has renamed classes before. If the import or the construction
fails, the factory raises a `NotImplementedError` ("blocked on verification")
rather than guessing further; this example catches that and prints it
plainly. `main()` only constructs the object; `build(donkey)` hands the client to an
`Agent` with the policy middleware and drives one turn with `Agent.run`,
which is what the conformance kit runs.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[Microsoft Agent Framework](https://docs.donkey-kit.dev/frameworks/agent-framework)**.
> This README duplicates the runnable essentials on purpose so you can run it in
> place; if the two ever differ, the docs page is canonical.

## Run

```bash
pip install "donkey-kit[agent_framework]"

export DONKEY_LLM_PROXY_URL="https://<ingress-gw>/<instance>/"   # note: no /v1
export DONKEY_LLM_PROXY_CLIENT_ID="<consumer client id>"
export DONKEY_LLM_PROXY_CLIENT_SECRET="<consumer client secret>"

python examples/agent_framework/main.py
```

The script reads its settings the way the SDK does (environment variables, then
`.donkey-kit.toml`) and stops with the SDK's own `ConfigError`, listing every
missing setting, when one is absent. For a model-wallet proxy, set
`DONKEY_LLM_PROXY_AUTH=jwt` and `DONKEY_LLM_PROXY_WALLET_CLIENT_ID` instead of
the client id and secret; the wallet JWT comes from an `AuthProvider` you pass
to `Donkey(llm_auth=...)` in your own code
([Configuration](https://docs.donkey-kit.dev/reference/configuration#jwt--model-wallet-auth-mode)).

## The manual equivalent

The factory call is equivalent to building the chat client yourself with the
governed connection values (BG §1.8). docs/verified-apis.md §8 records the
agent-framework version these names were checked against:

```python
from agent_framework.openai import OpenAIChatClient

client = OpenAIChatClient(
    model="gpt-4o",  # `model`, not `model_id`
    base_url=DONKEY_LLM_PROXY_URL,
    api_key="unused",  # the proxy enforces client_id/client_secret headers instead
    default_headers={
        "client_id": DONKEY_LLM_PROXY_CLIENT_ID,
        "client_secret": DONKEY_LLM_PROXY_CLIENT_SECRET,
    },
)
```

The factory (`donkey_kit.integrations.agent_framework.chat_client`) fills
in `base_url`, `api_key`, and `default_headers` from one governed config
source, and raises a clear "blocked on verification" error instead of
silently guessing if the class import or construction fails.

`OpenAIChatClient` calls the Responses API (`POST /responses`). A proxy route
that does not serve it, such as Azure OpenAI (a 404), needs the Chat
Completions client instead: `chat_client("…", api="chat_completions")` builds
an `agent_framework.openai.OpenAIChatCompletionClient` with the same kwargs.

## Links

- Microsoft Agent Framework docs: see the framework's official documentation
