# Strands Agents — governed model access

Strands Agents gets a governed `OpenAIModel`, with proxy connection details
forwarded through Strands' `client_args`, which Strands passes straight
through to the underlying OpenAI client.

> **Supported at `connection_kwargs()` — not conformance-tested (`BG §1.8`).** `client_args` gives full header AND
> transport injection, on par with the LangGraph adapter.

## Install

```bash
pip install "donkey-kit[strands]"
```

## Quickstart

```python
from donkey_kit.integrations.strands import model

llm = model("gpt-4o")
```

`llm` is a real, native **`strands.models.openai.OpenAIModel`** instance —
pass it to your `Agent` as you would any other Strands model.

There's no first-party Agent Fabric TypeScript SDK yet (it's on the
[roadmap](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)). The proxy is OpenAI-compatible, so point the
official `openai` npm client at it — the same base URL and
`client_id`/`client_secret` headers also drop into the **Strands TypeScript
SDK** (`@strands-agents/sdk`).

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
    llm = donkey.strands.model("gpt-4o")
```

**2. Module-level factory** (shortest):

```python
from donkey_kit.integrations.strands import model

llm = model("gpt-4o")
```

**3. Governed kwargs, native constructor:**

```python
from donkey_kit import Donkey
from strands.models.openai import OpenAIModel

async with Donkey.from_env() as donkey:
    llm = OpenAIModel(model_id="gpt-4o", **donkey.strands.connection_kwargs())
```

## The manual equivalent (eject at any time)

```python
from strands.models.openai import OpenAIModel

llm = OpenAIModel(
    model_id="gpt-4o",
    client_args={
        "base_url": ...,          # from DONKEY_LLM_PROXY_URL, no /v1 suffix
        "api_key": ...,
        "default_headers": ...,   # client_id / client_secret header pair
        "http_client": ...,       # the SDK's shared httpx client
    },
)
```

Everything the SDK injects lives inside the single `client_args` dict that
Strands forwards to its internal OpenAI client — nothing is bolted on
separately.

## Notes & limitations

> Because Strands forwards `client_args` verbatim to the underlying OpenAI
> client, both header injection (`default_headers`) AND transport injection
> (`http_client`) are available — full conformance, same tier as LangGraph.

> Strands also exposes lifecycle hooks (`BeforeToolCallEvent` and friends),
> which the SDK uses elsewhere for the policy-termination pattern — see the
> error taxonomy for how a `PolicyViolation` should end a run cleanly rather
> than triggering a retry loop.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for
the current status of every constructor signature this adapter depends on.
