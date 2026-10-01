# CrewAI

CrewAI gets a governed native LLM object pointed at the Agent Fabric LLM
proxy. The adapter translates the governed connection into CrewAI's own
model-string and kwarg conventions for you.

**What you get**

- A native `crewai.BaseLLM` — concretely `OpenAICompletion`, CrewAI's native
  OpenAI provider. `crewai.LLM`'s own `__new__` factory routes an
  `openai/`-prefixed model with an explicit `base_url` to that provider rather
  than returning an `LLM` instance itself. The proxy auth and attribution
  headers are set.
- The `openai/` model prefix and CrewAI's kwarg names handled automatically.
- Supported at `connection_kwargs()`. CrewAI's native provider builds its own
  HTTP client rather than using the SDK's, so there is no run correlation ID
  and `donkey.last_call` is not populated (see [Notes](#notes)).

## Install

```bash
pip install "donkey-kit[crewai]"
```

## Quickstart

```python
from donkey_kit.integrations.crewai import llm

model = llm("gpt-4o")
```

`model` is a real `crewai.BaseLLM` instance (`OpenAICompletion`). The adapter
prefixes the model string with `openai/` (`openai/gpt-4o`) — you don't add it
yourself. That prefix, together with the proxy `base_url`, is what routes
`crewai.LLM`'s factory to its native OpenAI provider; the provider strips the
prefix again, so the proxy receives the bare model id (`gpt-4o`).

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
    base_url=...,       # from DONKEY_LLM_PROXY_URL, no /v1 suffix
    api_key=...,
    extra_headers=...,  # client_id / client_secret header pair
)
```

`connection_kwargs()` fills in `base_url`, `api_key`, and `extra_headers` (the
`client_id` / `client_secret` header pair) for you. When CrewAI is installed it
also adds an `interceptor`: with one set, CrewAI's provider builds HTTP clients
that don't follow redirects, and the interceptor removes the credential headers
from any request to an origin other than the proxy (see
[Credentials go only to checked endpoints](https://donkey-development-kit.github.io/donkey-development-kit/reference/configuration.md#credentials-go-only-to-checked-endpoints)).
A `base_url` / `api_base` passed to `llm()` must pass the
[`https://` rule](https://donkey-development-kit.github.io/donkey-development-kit/reference/configuration.md#endpoints-must-use-https).

## Notes

- **No run correlation ID.** CrewAI sends requests through its native OpenAI
  provider's own HTTP client rather than the SDK's shared one, so the
  correlation ID bound by `donkey.run()` doesn't reach the request. The auth
  and attribution headers are still sent on every request. The conformance
  suite checks this as a documented behaviour.
- **No JWT in `jwt` mode.** The JWT is added only by the SDK's shared client,
  so a wallet proxy answers `401`; see the
  [`jwt` mode note](https://donkey-development-kit.github.io/donkey-development-kit/reference/configuration.md#jwt--model-wallet-auth-mode).
- **Printing the model shows the API key.** `OpenAICompletion`'s own `repr()` /
  `str()` include `api_key`. Don't print or log it.
- **`donkey.last_call` is unavailable.** Because the response is handled by
  CrewAI's own client, gateway identity, routing, and usage fields can't be
  observed. When every adapter resolved on a `Donkey` is like this one,
  `donkey.last_call` reports
  `status == LastCallStatus.UNAVAILABLE` and `available == False`, and names the
  resolved adapters in `surface`.

See the [error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md) for how proxy rejections surface through
CrewAI.
