Consume <strong>Agent Fabric</strong> capabilities — governed model
      access, and the budget, refusal, telemetry and simulation machinery that
      hangs off it — from your own agent framework, in your own IDE,{' '}
      <strong>without adopting Mule</strong>.
    </>
  }
  media={
    
  }
  actions={[
    { label: 'Quickstart', href: '/quickstart', primary: true },
    { label: 'Pick your framework', href: '/frameworks' },
  ]}
/>

You keep writing LangGraph, Google ADK, Strands, LlamaIndex, CrewAI, the OpenAI
Agents SDK, the Anthropic SDK, or Microsoft Agent Framework code exactly as you
do today. The SDK wires those frameworks to your organisation's governed **Omni
Gateway LLM proxy** so every model call is authenticated, attributed, and
policy-checked — and it hands you back your framework's **own native objects**,
never a wrapper you have to code around.

  **"Agent Fabric" is a MuleSoft (Salesforce) product name**, not a generic
  term. This project is an SDK *for* that product; it is descriptive, not a
  first-party release. See [the trademark/support boundary in the README](https://github.com/Donkey-Development-Kit/donkey-development-kit).

## The one design rule

> **Adapters return the framework's native object — never a wrapper.**

`donkey.langgraph.chat_model("gpt-4o")` returns a real `langchain_openai.ChatOpenAI`.
`donkey.llamaindex.llm("gpt-4o")` returns a real `OpenAILike`. You can hand these
straight to `create_agent`, a LlamaIndex query engine, a Strands `Agent`,
and so on. If you ever want to drop the SDK, you eject to three lines of native
constructor code — every framework page shows you exactly which three.

## Make a governed call

The Python SDK returns native framework objects. From any other language you hit
the *same* governed proxy over its OpenAI-compatible HTTP API — the verified base
URL plus the `client_id` / `client_secret` headers. (No first-party TypeScript
SDK yet.)

```python
from donkey_kit.integrations.langgraph import chat_model

# A real langchain_openai.ChatOpenAI, already pointed at your governed proxy.
model = chat_model("gpt-4o", temperature=0)
reply = await model.ainvoke([("user", "Explain quantum computing in simple terms.")])
print(reply.content)
```

```python
from donkey_kit import Donkey

# A real openai.OpenAI, already pointed at your governed proxy — no event loop.
with Donkey.from_env() as donkey:
    client = donkey.llm.client(sync=True)
    reply = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Explain quantum computing in simple terms."}],
    )
    print(reply.choices[0].message.content)
```

```typescript
const base = process.env.DONKEY_LLM_PROXY_URL!;   // ends in "/", no /v1
const resp = await fetch(`${base}chat/completions`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    client_id: process.env.DONKEY_LLM_PROXY_CLIENT_ID!,
    client_secret: process.env.DONKEY_LLM_PROXY_CLIENT_SECRET!,
  },
  body: JSON.stringify({
    model: "gpt-4o",
    messages: [{ role: "user", content: "Explain quantum computing in simple terms." }],
  }),
});
console.log((await resp.json()).choices[0].message.content);
```

```bash
curl "${DONKEY_LLM_PROXY_URL}chat/completions" \
  -H "content-type: application/json" \
  -H "client_id: ${DONKEY_LLM_PROXY_CLIENT_ID}" \
  -H "client_secret: ${DONKEY_LLM_PROXY_CLIENT_SECRET}" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Explain quantum computing in simple terms."}]
  }'
```

## Why not just set `base_url`?

Fair question, and the honest answer shapes everything else here:

> A stock OpenAI client with a `base_url` and two headers **can** reach the
> governed proxy. This SDK is not selling you that.

What it sells is the **skeleton** — one place in your process where every
request enters and every response leaves. That is the only place where budget
headers, error classification, correlation IDs, cost tags, OTel spans,
simulation, and refusal handlers can all attach *without you wiring each one*.

  
    Point your framework at the governed proxy. Verified auth, attribution,
    retries, and a typed error taxonomy — injected for you.
  
  
    Typed refusals, budget pacing, a local simulator, a conformance plugin,
    OTel GenAI spans, and correlation IDs — one release, because the skeleton
    is worth the sum of what hangs on it.
  
  
    Discover and bind governed MCP tools across frameworks, with governed-only
    filtering. The page covers what's blocked and why.
  

## What is live today

  **Governed model access is live-verified** against a real Anypoint sandbox:
  the proxy base-URL shape (`https://<ingress>/<instance>/`, no `/v1`), the
  `client_id` + `client_secret` consumer-auth header pair, the attribution
  headers, and the four policy rejection shapes (auth `401`, PII `403`,
  token-budget `429`, upstream passthrough). The framework-free client and the
  adapters are wired to that verified contract.

Beyond the live-verified contract above, shipped SDK capability pages say what
is implemented and call out any remaining platform-verification caveats.
Planned Phase 2 and Phase 3 pages carry roadmap badges and describe proposal
API shapes. Where a platform fact is not confirmed, the SDK raises
`NotImplementedError("blocked on verification: …")` rather than fabricate an
endpoint, header, or class name — see [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md)
for the current ledger, and the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) for what lands when.

## Next steps

  
    Make your first governed model call in under five minutes.
  
  
    A tour of everything governed model access gives you today.
  
  
    Per-framework install, quickstart, and the manual equivalent.
  
  
    Branch on governance outcomes instead of parsing error bodies.
  
  
    What ships when — and what this SDK deliberately will not build.
