# CrewAI — governed model access

CrewAI gets a governed `LLM`, backed by LiteLLM under the hood, pointed at
the Agent Fabric LLM proxy through LiteLLM's own model-string and kwarg
conventions rather than raw OpenAI ones.

> **Supported at `connection_kwargs()` — not conformance-tested (`BG §1.8`)**, with two documented exemptions: LiteLLM
> owns its own transport, so the SDK's shared httpx client is not injected
> here. Per-run correlation and `donkey.last_call` response observation are
> unavailable as a result (see below) — the same exemptions as the
> [Google ADK](https://donkey-development-kit.github.io/donkey-development-kit/frameworks/adk.md) adapter.

## Install

```bash
pip install "donkey-kit[crewai]"
```

## Quickstart

```python
from donkey_kit.integrations.crewai import llm

model = llm("gpt-4o")
```

`model` is a real, native **`crewai.LLM`** instance. The model string is
auto-prefixed with `openai/` before it reaches LiteLLM (`openai/gpt-4o`),
because that's the prefix LiteLLM's OpenAI-compatible route expects — you
don't need to add it yourself.

There's no first-party Agent Fabric TypeScript SDK yet, and **CrewAI itself is
Python-only** — it has no TypeScript SDK. You can still make the same
governed call from TypeScript via the OpenAI-compatible proxy with the
official `openai` npm client:

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

## The manual equivalent (eject at any time)

```python
from crewai import LLM

model = LLM(
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
> header injection is full, but transport injection is not possible, exactly
> as with Google ADK.

> **`donkey.last_call` is unavailable for this adapter.** The same LiteLLM-owned
> transport means no response reaches the SDK's `_on_response` hook, so gateway
> identity, routing, and usage fields cannot be observed. The record reports
> `status == LastCallStatus.UNAVAILABLE`, `available == False`, and names
> `"crewai"` in `surface` rather than returning ambiguous empty fields. This is a
> documented, asserted `gateway_identity_observed` conformance exemption.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface through
LiteLLM's error path, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)
page for the current status of every constructor signature this adapter
depends on.
