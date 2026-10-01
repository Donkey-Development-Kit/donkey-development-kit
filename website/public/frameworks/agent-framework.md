# Microsoft Agent Framework

Microsoft Agent Framework gets a governed chat client pointed at the Omni
Gateway LLM proxy, plus policy middleware that ends a run on a governance
rejection with the SDK's typed refusal.

**What you get**

- A native `agent_framework.openai.OpenAIChatClient`, checked against
  agent-framework 1.19.0.
- `policy_middleware()`, which ends a run on a proxy refusal with a typed
  `PolicyViolation` such as `PIIDetected`.
- Supported at `connection_kwargs()`. The client gets an `async_client` that
  sends through the SDK's shared HTTP client, so per-run correlation, retries,
  spans, `donkey.last_call` and the `jwt`-mode JWT apply (see [Notes](#notes)).

## Install

```bash
pip install "donkey-kit[agent_framework]"
```

## Quickstart

```python
from donkey_kit.integrations.agent_framework import chat_client

llm = chat_client("gpt-4o")
```

`llm` is a real `agent_framework.openai.OpenAIChatClient` instance.

Microsoft Agent Framework ships for .NET, Python, and Go, not TypeScript. From
TypeScript, call the proxy's OpenAI-compatible API directly with the official
`openai` npm client:

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

## Manual equivalent

```python
from agent_framework.openai import OpenAIChatClient

llm = OpenAIChatClient(
    model=...,            # `model`, not `model_id`
    base_url=...,         # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,  # client_id / client_secret header pair
    async_client=...,     # an AsyncOpenAI on the SDK's shared client
)
```

`async_client` is present when the `openai` package is installed. The
constructor uses it as given, so a `base_url` passed to `chat_client()` gets a
client built on that URL; the URL must pass the
[`https://` rule](https://donkey-development-kit.github.io/donkey-development-kit/reference/configuration.md#endpoints-must-use-https).

## Policy middleware

`donkey.agent_framework.policy_middleware()` returns a chat middleware for
`Agent(..., middleware=[...])`:

```python
from agent_framework import Agent
from donkey_kit import Donkey, PIIDetected

async with Donkey.from_env() as donkey:
    agent = Agent(
        client=donkey.agent_framework.chat_client("gpt-4o"),
        middleware=[donkey.agent_framework.policy_middleware()],
    )
    try:
        await agent.run("...")
    except PIIDetected as err:
        print(err.correlation_id, err.entities)
```

Without it, a proxy refusal reaches you as Agent Framework's generic
`ChatClientException`. With it, the refusal goes through
`donkey_kit.core.errors.classify()` and the run ends with the typed error,
for example `PIIDetected` or `TokenBudgetExceeded`. The error carries the
correlation and call ids that were sent, and keeps the original exception on
`.framework_error`. This also works for streaming runs
(`agent.run(..., stream=True)`): the typed error is raised while you iterate
the stream. The chat client sends with retries off, so a refused request is
sent once.

Errors that have no proxy response behind them, such as a connection failure,
pass through unchanged. The middleware is marked with Agent Framework's
`@chat_middleware` decorator. That is confirmed offline against
agent-framework 1.19.0, with no live round-trip yet. If the decorator is
missing from your installed version, `policy_middleware()` raises a
`NotImplementedError` naming it.

## Notes

- **Constructor signature.** `OpenAIChatClient` takes `model`, `base_url`,
  `api_key`, and `default_headers` (agent-framework 1.19.0; `model_id` is not
  accepted). If the import fails or an upstream release renames a kwarg,
  `chat_client()` raises a `NotImplementedError` naming the class path or
  signature to check, rather than a raw `ImportError` or `TypeError`.
- **`donkey.last_call` is set in the context that made the call.** A cold read
  (no call yet in this context) on a `Donkey` that resolved only adapters like
  this one still reports `status == LastCallStatus.UNAVAILABLE` rather than
  `UNOBSERVED`, because MS Agent Framework is still listed as not observing calls. Aligning
  that, and the matching conformance exemptions, is tracked in [#740](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/740).

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for the full `PolicyViolation` hierarchy
that `policy_middleware()` raises.
