# Anthropic SDK

The Anthropic SDK gets a governed `AsyncAnthropic` client pointed at the Omni
Gateway LLM proxy, with the SDK's shared transport and proxy headers passed
straight into the client constructor.

**What you get**

- A native `anthropic.AsyncAnthropic` client.
- Full header **and** transport injection.
- Supported at `connection_kwargs()`.

  **Requires a `Format=Anthropic` proxy.** The native Anthropic Messages route
  (`POST /<base-path>/v1/messages`) is only served by a proxy provisioned with
  the Anthropic ingress Format. Default DDK proxies are `Format=OpenAI`: there,
  `/v1/messages` returns 404 and Claude is reachable only as an upstream
  provider through the OpenAI-compatible adapters. See
  [Model access](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md) for how ingress Format works.

## Install

```bash
pip install "donkey-kit[anthropic]"
```

## Quickstart

```python
from donkey_kit.integrations.anthropic import client

llm = client()
```

`llm` is a real `anthropic.AsyncAnthropic` instance. Unlike the other
adapters, the factory takes no `model` argument — pass the model ID per call,
as the Anthropic SDK expects:

```python
reply = await llm.messages.create(
    model="claude-...",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Say hi in three words."}],
)
```

Use the official **`@anthropic-ai/sdk`** client pointed at a `Format=Anthropic`
proxy, with the same `client_id` / `client_secret` header pair and the model ID
passed per call:

```typescript
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({
  baseURL: process.env.DONKEY_LLM_PROXY_URL,   // a Format=Anthropic proxy
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

## Manual equivalent

```python
from anthropic import AsyncAnthropic

llm = AsyncAnthropic(
    base_url=...,          # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    default_headers=...,   # client_id / client_secret header pair
    http_client=...,       # the SDK's shared httpx client
    max_retries=0,         # the SDK retries in its own transport layer
)
```

`connection_kwargs()` returns exactly these keys, so you can drop the factory
and construct `AsyncAnthropic` by hand at any time.

## Notes

- **`client()`, not `model(...)`.** The other adapters return a framework
  object already bound to a model ID, because their native constructors accept
  `model`. `AsyncAnthropic` is a bare client and the model ID is an argument to
  `.messages.create()`, so `donkey.anthropic.client()` takes no model argument.
- **Proxy Format.** MuleSoft Model Proxy offers three ingress Formats (OpenAI /
  Gemini / Anthropic), fixed when the proxy is created
  ([MuleSoft docs](https://docs.mulesoft.com/general/model-proxy)). A
  `Format=Anthropic` proxy returns a native Anthropic body from
  `/v1/messages` and 404s an OpenAI-shaped `/chat/completions` request. Auth is
  the same `client_id` / `client_secret` header pair as every other proxy.
  To use the native surface, set `DONKEY_LLM_PROXY_URL` (or `llm_proxy_url`)
  to a `Format=Anthropic` proxy.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/develop/docs/verified-apis.md) for
the current status of every constructor signature this adapter depends on.
