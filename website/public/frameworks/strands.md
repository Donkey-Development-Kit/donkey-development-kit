# Strands Agents

Strands Agents gets a governed `OpenAIModel` pointed at the Agent Fabric LLM
proxy. The connection details travel in Strands' `client_args`, which Strands
passes straight through to its underlying OpenAI client.

**What you get**

- A native `strands.models.openai.OpenAIModel`.
- Full header **and** transport injection through `client_args` — per-run
  correlation IDs and `donkey.last_call` work, as with LangGraph.
- Supported at `connection_kwargs()`.

## Install

```bash
pip install "donkey-kit[strands]"
```

## Quickstart

```python
from donkey_kit.integrations.strands import model

llm = model("gpt-4o")
```

`llm` is a real `strands.models.openai.OpenAIModel` instance — pass it to your
`Agent` as you would any other Strands model.

Call the proxy's OpenAI-compatible API with the official `openai` npm client.
The same base URL and `client_id`/`client_secret` headers also work with the
**Strands TypeScript SDK** (`@strands-agents/sdk`).

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

## Manual equivalent

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
Strands forwards to its internal OpenAI client.

## Notes

- Strands forwards `client_args` verbatim to the underlying OpenAI client, so
  both header injection (`default_headers`) and transport injection
  (`http_client`) are available.
- Strands also exposes lifecycle hooks (`BeforeToolCallEvent` and friends).
  The SDK uses them for the policy-termination pattern — see the error taxonomy
  for how a `PolicyViolation` should end a run cleanly rather than trigger a
  retry loop.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface as typed
exceptions, and the [verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page for
the current status of every constructor signature this adapter depends on.
