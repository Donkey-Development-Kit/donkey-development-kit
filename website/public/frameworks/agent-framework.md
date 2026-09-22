# Microsoft Agent Framework — governed model access

Microsoft Agent Framework gets a governed chat client at the Agent Fabric LLM
proxy, plus first-class policy middleware for terminating a run cleanly on a
governance rejection instead of letting the agent loop retry.

> **Supported at `connection_kwargs()` — not conformance-tested (`BG §1.8`).**
> This adapter receives a static `default_headers` snapshot, not the SDK's shared
> httpx client, so per-run correlation and `donkey.last_call` response
> observation are documented exemptions. It also ships the strongest
> policy-integration story of the eight frameworks
> (`policy_middleware()`), but its native client class is young and
> **UNVERIFIED** — read the callouts below before depending on it.

## Install

```bash
pip install "donkey-kit[agent_framework]"
```

## Quickstart

```python
from donkey_kit.integrations.agent_framework import chat_client

llm = chat_client("gpt-4o")
```

`llm` is intended to be a native **`agent_framework.openai.OpenAIChatClient`**
instance. If that import fails on your installed version, the factory raises
`NotImplementedError("blocked on verification: ...")` rather than guessing —
see the UNVERIFIED callout below.

There's no first-party Agent Fabric TypeScript SDK yet, and **Microsoft Agent
Framework has no TypeScript SDK** (it ships for .NET, Python, and Go). You can
still make the same governed call from TypeScript via the OpenAI-compatible
proxy with the official `openai` npm client:

```typescript
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: process.env.DONKEY_LLM_PROXY_URL,   // no /v1
  apiKey: "unused",                              // required slot; proxy uses the headers below
  defaultHeaders: {
    client_id: process.env.DONKEY_LLM_PROXY_CLIENT_ID!,
    client_secret: process.env.DONKEY_LLM_PROXY_CLIENT_SECRET!,
  },
});

const reply = await client.chat.completions.create({
  model: "gpt-4o",
  messages: [{ role: "user", content: "Say hi in three words." }],
});
console.log(reply.choices[0].message.content);
```

## Three ways to construct

**1. Off a shared `Donkey` instance:**

```python
from donkey_kit import Donkey

async with Donkey.from_env() as donkey:
    llm = donkey.agent_framework.chat_client("gpt-4o")
```

**2. Module-level factory** (shortest):

```python
from donkey_kit.integrations.agent_framework import chat_client

llm = chat_client("gpt-4o")
```

**3. Governed kwargs, native constructor:**

```python
from donkey_kit import Donkey
from agent_framework.openai import OpenAIChatClient

async with Donkey.from_env() as donkey:
    llm = OpenAIChatClient(
        model="gpt-4o",
        **donkey.agent_framework.connection_kwargs(),
    )
```

## The manual equivalent (eject at any time)

```python
from agent_framework.openai import OpenAIChatClient

llm = OpenAIChatClient(
    model=...,           # verified against agent-framework 1.19.0
    base_url=...,        # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,  # client_id / client_secret header pair
)
```

## Notes & limitations

> **VERIFIED** against agent-framework 1.19.0.
> `agent_framework.openai.OpenAIChatClient` takes `model`, `base_url`,
> `api_key` and `default_headers` (`model_id` is **not** accepted). The
> `chat_client()` factory stays guarded on both ends: if the import fails or
> the constructor signature changes upstream, it raises
> `NotImplementedError("blocked on verification")` instead of leaking a raw
> `ImportError`/`TypeError` — so a future upstream rename surfaces as a clear
> verification refusal rather than a cryptic error.

> Agent Framework has first-class middleware for intercepting agent actions.
> The adapter ships `policy_middleware()`, which catches a `PolicyViolation`
> and re-raises it so the host terminates the run cleanly rather than
> retrying — the best policy-integration story of any of the eight
> frameworks. The exact middleware signature Agent Framework expects is also
> **UNVERIFIED**; the shipped middleware is a plain async wrapper pending
> confirmation of the framework's middleware protocol.

> **This adapter cannot propagate per-run correlation or populate
> `donkey.last_call`.** Agent Framework receives a static `default_headers`
> snapshot, which deliberately excludes the correlation ID bound later by
> `donkey.run(id=...)`, and not the SDK's httpx client. No response reaches
> `_on_response`, so gateway identity, routing, and usage fields cannot be
> observed either. When every adapter resolved on a `Donkey` is non-observing,
> the record reports `status == LastCallStatus.UNAVAILABLE`, `available == False`,
> and names the resolved adapters in `surface`. These are documented, asserted
> `correlation_id_propagated` and `gateway_identity_observed` conformance
> exemptions.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for the full `PolicyViolation` hierarchy
that `policy_middleware()` catches, and the
[verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for the current
verification status of this adapter's constructor and middleware shape.
