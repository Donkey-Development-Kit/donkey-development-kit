# OpenAI Agents SDK

The OpenAI Agents SDK (pip package `openai-agents`) gets a governed
`OpenAIChatCompletionsModel` backed by a pre-built `AsyncOpenAI` client. The
adapter builds that client itself, with the SDK's shared HTTP client and proxy
headers, and hands it to the Agents SDK ready-made.

**What you get**

- A native `agents.OpenAIChatCompletionsModel`.
- Full header **and** transport injection — both travel together in one
  `AsyncOpenAI` object.
- Supported at `connection_kwargs()`.

## Install

```bash
pip install "donkey-kit[openai-agents]"
```

## Quickstart

```python
from donkey_kit.integrations.openai_agents import model

llm = model("gpt-4o")
```

`llm` is a real `agents.OpenAIChatCompletionsModel` instance — pass it to
`Agent(model=...)` as you would any other Agents SDK model.

Call the proxy's OpenAI-compatible API with the official `openai` npm client.
The same base URL and `client_id`/`client_secret` headers also work with the
**OpenAI Agents SDK for JS** (`@openai/agents`).

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

## Manual equivalent

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

## Notes

- **A pre-built client is the preferred integration point.** When a framework
  accepts a ready-made `AsyncOpenAI` instead of loose kwargs, the shared
  transport and every proxy header travel together as one object, with no
  risk of a kwarg being dropped. That's why injection is full here even though
  the model object never sees `base_url` or `default_headers` directly.
- `openai-agents` is distinct from the plain `openai` package:
  `agents.OpenAIChatCompletionsModel` lives in the Agents SDK. Installing
  `donkey-kit[openai-agents]` pulls it in for you. For the raw governed client
  with no framework, use `donkey.openai()` (from `donkey-kit[llm]`) instead.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for
the current status of every constructor signature this adapter depends on.
