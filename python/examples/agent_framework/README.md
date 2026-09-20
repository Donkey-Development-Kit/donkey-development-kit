# Microsoft Agent Framework example

Supported at connection_kwargs() — not conformance-tested (BG §1.8).

**What this shows.** A one-line factory call attempts to build a native
Agent Framework OpenAI-compatible chat client pointed at the governed
Agent Fabric LLM proxy. The proxy *contract* (base URL, `client_id`/
`client_secret` header auth, attribution headers) is live-verified. The
chat-client class name/path itself — `agent_framework.openai.OpenAIChatClient`
— and its base-URL kwarg (`model_id`) are **UNVERIFIED** (docs/verified-apis.md §8): Agent
Framework is a young package that has renamed classes recently. If the
import fails, the factory raises a `NotImplementedError` ("blocked on
verification") rather than guessing further; this example catches that and
prints it plainly. This example only constructs the object; it deliberately
does not attempt a live inference call, since Agent Framework drives chat
clients through its own `Agent` object rather than a method on the client
itself.

> 📖 **Prefer reading to running?** The canonical walkthrough — install,
> configure, and the manual equivalent — is in the docs:
> **[Microsoft Agent Framework](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/agent-framework)**.
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

## The manual equivalent

The factory call is equivalent to attempting to build the chat client
yourself with the governed connection values (BG §1.8) — **class name and
kwarg UNVERIFIED (docs/verified-apis.md §8), confirm against your installed version**:

```python
# CLASS NAME/PATH AND KWARG NAMES UNVERIFIED (docs/verified-apis.md §8) — confirm before relying on this
from agent_framework.openai import OpenAIChatClient

client = OpenAIChatClient(
    model_id="gpt-4o",  # kwarg name UNVERIFIED
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
silently guessing if the class import fails.

## Links

- Microsoft Agent Framework docs: see the framework's official documentation
