# LlamaIndex

LlamaIndex gets a governed `OpenAILike` LLM pointed at the Agent Fabric LLM
proxy, with the chat-model flag a chat-only gateway requires already set.

**What you get**

- A native `llama_index.llms.openai_like.OpenAILike`.
- `is_chat_model=True` and `is_function_calling_model=True` set for you.
- Supported at `connection_kwargs()`. The client receives a static
  `default_headers` snapshot, so per-run correlation and `donkey.last_call` are
  not available (see [Notes](#notes)).

## Install

```bash
pip install "donkey-kit[llamaindex]"
```

## Quickstart

```python
from donkey_kit.integrations.llamaindex import llm

model = llm("gpt-4o")
```

`model` is a real `llama_index.llms.openai_like.OpenAILike` instance, ready to
hand to any LlamaIndex query engine, chat engine, or agent.

Call the proxy's OpenAI-compatible API with the official `openai` npm client.
The same base URL and `client_id`/`client_secret` headers also work with
**LlamaIndex.TS**.

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

## Manual equivalent

```python
from llama_index.llms.openai_like import OpenAILike

model = OpenAILike(
    model="gpt-4o",
    api_base=...,                     # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,              # client_id / client_secret header pair
    is_chat_model=True,               # required — see below
    is_function_calling_model=True,
)
```

LlamaIndex uses `api_base` rather than `base_url`; `connection_kwargs()`
already translates for you.

## Notes

- **Always set `is_chat_model=True`.** `OpenAILike` defaults to
  `is_chat_model=False`, which routes requests to the completions endpoint
  instead of chat — and that fails against a chat-only proxy like the Omni
  Gateway LLM proxy. `connection_kwargs()` always sets it (and
  `is_function_calling_model=True`); set it yourself if you construct
  `OpenAILike` outside the adapter.
- **No per-run correlation or `donkey.last_call`.** The client receives a
  static `default_headers` snapshot, which excludes the correlation ID bound
  later by `donkey.run(id=...)`, and the SDK's httpx client is not used. No
  response reaches the SDK, so gateway identity, routing, and usage fields
  can't be observed. When every adapter resolved on a `Donkey` is like this
  one, `donkey.last_call` reports `status == LastCallStatus.UNAVAILABLE` and
  `available == False`, and names the resolved adapters in `surface`. The
  conformance suite asserts both as documented exemptions.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for
the current status of every constructor signature this adapter depends on.
