# LlamaIndex — governed model access

LlamaIndex gets a governed `OpenAILike` LLM pointed at the Agent Fabric LLM proxy,
with the one flag that a chat-only gateway absolutely requires forced on for
you.

> **Supported at `connection_kwargs()` — not conformance-tested (`BG §1.8`).** Full header injection, with one
> documented exemption: LlamaIndex receives `default_headers`, not the SDK's
> shared httpx client, so `donkey.last_call` response observation is unavailable
> (see below). Watch the `is_chat_model` gotcha if you build the client by hand.

## Install

```bash
pip install "donkey-kit[llamaindex]"
```

## Quickstart

```python
from donkey_kit.integrations.llamaindex import llm

model = llm("gpt-4o")
```

`model` is a real, native **`llama_index.llms.openai_like.OpenAILike`**
instance, ready to hand to any LlamaIndex query engine, chat engine, or agent.

There's no first-party Agent Fabric TypeScript SDK yet (it's on the
[roadmap](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)). The proxy is OpenAI-compatible, so point the
official `openai` npm client at it — the same base URL and
`client_id`/`client_secret` headers also drop into **LlamaIndex.TS**.

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
    model = donkey.llamaindex.llm("gpt-4o")
```

**2. Module-level factory** (shortest):

```python
from donkey_kit.integrations.llamaindex import llm

model = llm("gpt-4o")
```

**3. Governed kwargs, native constructor:**

```python
from donkey_kit import Donkey
from llama_index.llms.openai_like import OpenAILike

async with Donkey.from_env() as donkey:
    model = OpenAILike(model="gpt-4o", **donkey.llamaindex.connection_kwargs())
```

## The manual equivalent (eject at any time)

```python
from llama_index.llms.openai_like import OpenAILike

model = OpenAILike(
    model="gpt-4o",
    api_base=...,                     # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,              # client_id / client_secret header pair
    is_chat_model=True,               # see gotcha below — never omit this
    is_function_calling_model=True,
)
```

## Notes & limitations

> **Gotcha: `OpenAILike` defaults `is_chat_model=False`.** Left at its
> default, `OpenAILike` silently routes requests to the completions endpoint
> instead of the chat endpoint — which fails against a chat-only proxy like
> the Agent Fabric LLM proxy. This is the single most common LlamaIndex-with-a-
> gateway bug. The adapter's `connection_kwargs()` always forces
> `is_chat_model=True` (and `is_function_calling_model=True`) so you don't
> have to remember to set it — but if you ever construct `OpenAILike`
> yourself outside the adapter, set it explicitly.

> LlamaIndex uses `api_base` rather than `base_url` for its endpoint kwarg;
> `connection_kwargs()` already translates for you.

> **This adapter cannot populate `donkey.last_call`.** LlamaIndex receives
> the governed `default_headers`, but not the SDK's httpx client, so no response
> reaches `_on_response` and gateway identity, routing, and usage fields cannot
> be observed. When every adapter resolved on a `Donkey` is non-observing, the
> record reports `status == LastCallStatus.UNAVAILABLE`, `available == False`,
> and names the resolved adapters in `surface`. This is a documented, asserted
> `gateway_identity_observed` conformance exemption.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for
the current status of every constructor signature this adapter depends on.
