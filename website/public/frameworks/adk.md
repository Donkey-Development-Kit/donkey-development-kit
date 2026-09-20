# Google ADK — governed model access

Google's Agent Development Kit (ADK) is Gemini-first; it reaches the
Agent Fabric LLM proxy through ADK's `LiteLlm` model wrapper, which speaks
LiteLLM's own model-string and kwarg conventions rather than raw OpenAI ones.

> **Supported at `connection_kwargs()` — not conformance-tested (`BG §1.8`)**, with one documented exemption: LiteLLM
> owns its own transport, so the SDK's shared httpx client is not injected
> here (see below).

## Install

```bash
pip install "donkey-kit[adk]"
```

## Quickstart

```python
from donkey_kit.integrations.adk import model

llm = model("gpt-4o")
```

`llm` is a real, native **`google.adk.models.lite_llm.LiteLlm`** instance. The
model string is auto-prefixed with `openai/` before it reaches LiteLLM
(`openai/gpt-4o`), because that's the prefix LiteLLM's OpenAI-compatible route
expects — you don't need to add it yourself.

There's no first-party Agent Fabric TypeScript SDK yet (it's on the
[roadmap](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)). The proxy is OpenAI-compatible, so point the
official `openai` npm client at it — the same base URL and
`client_id`/`client_secret` headers also drop into **ADK for TypeScript**
(`@google/adk`).

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
    llm = donkey.adk.model("gpt-4o")
```

**2. Module-level factory** (shortest):

```python
from donkey_kit.integrations.adk import model

llm = model("gpt-4o")
```

**3. Governed kwargs, native constructor:**

```python
from donkey_kit import Donkey
from google.adk.models.lite_llm import LiteLlm

async with Donkey.from_env() as donkey:
    llm = LiteLlm(model="openai/gpt-4o", **donkey.adk.connection_kwargs())
```

## The manual equivalent (eject at any time)

```python
from google.adk.models.lite_llm import LiteLlm

llm = LiteLlm(
    model="openai/gpt-4o",
    api_base=...,       # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    extra_headers=...,  # client_id / client_secret header pair
)
```

Note the kwarg names: LiteLLM uses `api_base` and `extra_headers`, not
`base_url` / `default_headers` — `connection_kwargs()` already translates for
you.

## Notes & limitations

> **Correlation IDs are per-client, not per-run, for this adapter.** LiteLLM
> owns its own HTTP transport, so the SDK's shared httpx client — and the
> per-run correlation ID it stamps on every request — cannot be injected into
> it. This is a documented, asserted conformance exemption, not an oversight:
> ADK is the one adapter in the set where header injection is full but
> transport injection is not possible.

> `google-adk` requires `litellm>=1.84` as a floor (not a ceiling) — pin your
> own upper bound if you need one.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface through
LiteLLM's error path, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)
page for the current status of every constructor signature this adapter
depends on.
