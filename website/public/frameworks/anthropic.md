# Anthropic SDK — governed model access

The Anthropic SDK gets a governed `AsyncAnthropic` client pointed at the
Agent Fabric LLM proxy, with the SDK's shared transport and verified headers
injected directly into the client constructor.

> **Supported at `connection_kwargs()` — not conformance-tested (`BG §1.8`)**, with one important divergence from
> every other adapter in this roster: this factory returns a bare **client**,
> not a model-bound object — see below before you reach for it.

## Install

```bash
pip install "donkey-kit[anthropic]"
```

## Quickstart

```python
from donkey_kit.integrations.anthropic import client

llm = client()
```

`llm` is a real, native **`anthropic.AsyncAnthropic`** instance. Unlike every
other framework in this roster, there is no `model` argument on the factory —
pass the model id per call, exactly as the native Anthropic SDK expects:

```python
reply = await llm.messages.create(
    model="claude-...",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Say hi in three words."}],
)
```

There's no first-party Agent Fabric TypeScript SDK yet (it's on the
[roadmap](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)). The native match here is the official
**`@anthropic-ai/sdk`** client pointed at the proxy — same `client_id` /
`client_secret` header pair, model id passed per call. **Caveat (unverified):**
the proxy is verified OpenAI-compatible; whether it exposes an Anthropic-native
Messages route is still an open item, so confirm it and override `baseURL` if
needed before relying on this path.

```typescript
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({
  baseURL: process.env.DONKEY_LLM_PROXY_URL,   // UNVERIFIED: needs an Anthropic-native route
  apiKey: "unused",                              // required slot; proxy uses the headers below
  defaultHeaders: {
    client_id: process.env.DONKEY_LLM_PROXY_CLIENT_ID!,
    client_secret: process.env.DONKEY_LLM_PROXY_CLIENT_SECRET!,
  },
});

const reply = await client.messages.create({
  model: "claude-...",
  max_tokens: 1024,
  messages: [{ role: "user", content: "Say hi in three words." }],
});
console.log(reply.content);
```

## Three ways to construct

**1. Off a shared `Donkey` instance:**

```python
from donkey_kit import Donkey

async with Donkey.from_env() as donkey:
    llm = donkey.anthropic.client()
```

**2. Module-level factory** (shortest):

```python
from donkey_kit.integrations.anthropic import client

llm = client()
```

**3. Governed kwargs, native constructor:**

```python
from donkey_kit import Donkey
from anthropic import AsyncAnthropic

async with Donkey.from_env() as donkey:
    llm = AsyncAnthropic(**donkey.anthropic.connection_kwargs())
```

## The manual equivalent (eject at any time)

```python
from anthropic import AsyncAnthropic

llm = AsyncAnthropic(
    base_url=...,          # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,   # client_id / client_secret header pair
    http_client=...,       # the SDK's shared httpx client
)
```

Nothing here is hidden — `connection_kwargs()` returns exactly these keys, so
you can always drop the factory and construct `AsyncAnthropic` by hand.

## Notes & limitations

> **Divergence: `client()`, not `model(...)`.** Every other adapter in this
> roster returns a framework object already bound to a model id, because the
> underlying native constructor accepts `model` as a kwarg. `AsyncAnthropic`
> doesn't work that way — it's a bare client, and the model id is an argument
> to `.messages.create()`, not to the constructor. `donkey.anthropic.client()`
> therefore takes no model argument at all; you supply the model id yourself
> on every call.

> **UNVERIFIED: the proxy's Anthropic-native Messages route.**
> The governed proxy's contract has been live-verified for its
> OpenAI-compatible `chat/completions` surface (see the
> [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)); pointing `AsyncAnthropic` at
> the same proxy exercises Anthropic's own native `/v1/messages` route
> instead, and that route has **not** been confirmed against a live proxy.
> `client()` emits a one-time runtime warning the first time it's called,
> flagging this gap rather than silently assuming the route works. Confirm
> this against your own sandbox before depending on it in production.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for
the current status of every constructor signature this adapter depends on.
