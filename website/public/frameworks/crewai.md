# CrewAI

CrewAI gets a governed `LLM`, backed by LiteLLM, pointed at the Agent Fabric
LLM proxy. The adapter translates the governed connection into LiteLLM's own
model-string and kwarg conventions for you.

**What you get**

- A native `crewai.LLM`, with the proxy auth and attribution headers set.
- The `openai/` model prefix and LiteLLM kwarg names handled automatically.
- Supported at `connection_kwargs()`. CrewAI's model calls go through
  LiteLLM, so correlation is per client and `donkey.last_call` is not
  populated — the same as [Google ADK](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/adk.md) (see [Notes](#notes)).

## Install

```bash
pip install "donkey-kit[crewai]"
```

## Quickstart

```python
from donkey_kit.integrations.crewai import llm

model = llm("gpt-4o")
```

`model` is a real `crewai.LLM` instance. The model string is prefixed with
`openai/` before it reaches LiteLLM (`openai/gpt-4o`), which is the prefix
LiteLLM's OpenAI-compatible route expects — you don't add it yourself.

CrewAI is Python-only. From TypeScript, call the proxy's OpenAI-compatible API
directly with the official `openai` npm client:

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
    model = donkey.crewai.llm("gpt-4o")
```

**2. Module-level factory** (shortest):

```python
from donkey_kit.integrations.crewai import llm

model = llm("gpt-4o")
```

**3. Governed kwargs, native constructor:**

```python
from donkey_kit import Donkey
from crewai import LLM

async with Donkey.from_env() as donkey:
    model = LLM(model="openai/gpt-4o", **donkey.crewai.connection_kwargs())
```

## Manual equivalent

```python
from crewai import LLM

model = LLM(
    model="openai/gpt-4o",
    api_base=...,       # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    extra_headers=...,  # client_id / client_secret header pair
)
```

LiteLLM uses `api_base` and `extra_headers`, not `base_url` /
`default_headers` — `connection_kwargs()` already translates for you.

## Notes

- **Correlation IDs are per-client, not per-run.** CrewAI sends requests through
  its built-in LiteLLM model layer rather than the SDK's shared HTTP client, so
  the correlation ID is set once per client instead of per `donkey.run()`. Every
  governance header is still sent on every request. The conformance suite
  checks this as a documented behaviour.
- **`donkey.last_call` is unavailable.** Because the response is handled by LiteLLM,
  gateway identity, routing, and usage fields can't be observed. When every
  adapter resolved on a `Donkey` is like this one, `donkey.last_call` reports
  `status == LastCallStatus.UNAVAILABLE` and `available == False`, and names the
  resolved adapters in `surface`.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface through
CrewAI's LiteLLM layer.
