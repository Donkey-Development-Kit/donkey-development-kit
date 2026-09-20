# OpenAI Agents SDK — governed model access

The OpenAI Agents SDK (pip package `openai-agents`) gets a governed
`OpenAIChatCompletionsModel` backed by a pre-built `AsyncOpenAI` client — the
SDK constructs the OpenAI client itself, wired with its shared http client and
verified headers, and hands it to the Agents SDK ready-made.

> **Supported at `connection_kwargs()` — not conformance-tested (`BG §1.8`).** Full header AND transport injection —
> the adapter builds the `AsyncOpenAI` client itself, so both travel together
> as one object.

## Install

```bash
pip install "donkey-kit[openai-agents]"
```

## Quickstart

```python
from donkey_kit.integrations.openai_agents import model

llm = model("gpt-4o")
```

`llm` is a real, native **`agents.OpenAIChatCompletionsModel`** instance —
pass it to your `Agent(model=...)` as you would any other Agents SDK model.

There's no first-party Agent Fabric TypeScript SDK yet (it's on the
[roadmap](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)). The proxy is OpenAI-compatible, so point the
official `openai` npm client at it — the same base URL and
`client_id`/`client_secret` headers also drop into the **OpenAI Agents SDK for
JS** (`@openai/agents`).

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
    llm = donkey.openai_agents.model("gpt-4o")
```

**2. Module-level factory** (shortest):

```python
from donkey_kit.integrations.openai_agents import model

llm = model("gpt-4o")
```

**3. Governed kwargs, native constructor:**

```python
from donkey_kit import Donkey
from agents import OpenAIChatCompletionsModel

async with Donkey.from_env() as donkey:
    llm = OpenAIChatCompletionsModel(
        model="gpt-4o",
        **donkey.openai_agents.connection_kwargs(),
    )
```

## The manual equivalent (eject at any time)

```python
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel

async_client = AsyncOpenAI(
    base_url=...,          # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,   # client_id / client_secret header pair
    http_client=...,       # the SDK's shared httpx client
)

llm = OpenAIChatCompletionsModel(
    model="gpt-4o",
    openai_client=async_client,
)
```

`connection_kwargs()` returns exactly one key, `openai_client`, holding this
pre-built `AsyncOpenAI` instance.

## Notes & limitations

> **Preferred pattern: hand the framework a pre-built OpenAI client.**
> Wherever a framework accepts a ready-made `AsyncOpenAI` instance instead of
> a set of loose kwargs, that's the adapter's preferred integration point —
> it guarantees the shared transport and every governed header travel
> together as one object, with no risk of a kwarg being dropped along the
> way. This is why header injection here is full even though the model
> object itself never sees `base_url` or `default_headers` directly.

> The `openai-agents` package is distinct from the plain `openai` client —
> `agents.OpenAIChatCompletionsModel` lives in the Agents SDK, not in the base
> OpenAI Python SDK. Installing `donkey-kit[openai-agents]` pulls in
> `openai-agents` for you. For the raw governed client with no framework, use
> `donkey.openai()` (from `donkey-kit[llm]`) instead.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for
the current status of every constructor signature this adapter depends on.
