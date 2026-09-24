<strong>Governed by the gateway. Understood by your code.</strong> DDK
      brings Omni Gateway awareness into the agents you already write — in
      LangGraph, Google ADK, Strands, CrewAI, LlamaIndex, the OpenAI Agents SDK,
      the Anthropic SDK or Microsoft Agent Framework — so governance is not
      just enforced on your agents, it is respected by them.
    </>
  }
  media={
    
  }
  actions={[
    { label: 'Quickstart', href: '/quickstart', primary: true },
    { label: 'Feature overview', href: '/feature-overview' },
    { label: 'Examples', href: '/examples' },
  ]}
/>

## Why gateway awareness

Enterprises put a gateway in front of their AI traffic for good reasons: one
place to authenticate every caller, meter every token, block personal data,
stop prompt injection and attribute cost to the team that spent it. MuleSoft
**Omni Gateway**, managed through **Agent Fabric**, does exactly that — for
LLMs, MCP servers, APIs and agents alike.

But a gateway that governs alone only solves half the problem. The agent on the
other side of the wire sees an opaque `403` or `429` and does what code does
with errors it does not understand: it retries a PII block as if it were a
network blip, burns the remaining budget on calls that will be refused, crashes
a nightly batch at 2am, and leaves no trace that connects its own run to the
gateway's audit log. Control is one-sided, and the cost of that shows up as
wasted tokens, broken runs and incident tickets.

**DDK makes governance a collaboration.** It brings the gateway's view of the
world into your agent code:

- a PII refusal arrives as a typed `PIIDetected`, not a generic HTTP error, so
  the agent can redact and continue instead of retrying;
- the remaining token budget is an object the agent can **pace** against,
  instead of a limit it discovers by failing;
- every call carries a correlation ID and cost tags, and emits an
  OpenTelemetry span that lines up with the gateway's own audit trail;
- and you can rehearse all of it on your laptop, against a local simulator,
  before an agent ever meets production policy.

The gateway stays the enforcement point — it always has the final word. What
changes is that your agents become **good citizens** of the platform: aware of
the rules, efficient within them, and observable end to end. That is the
enterprise vision behind DDK — control at the proxy, efficiency in the agent —
built for AI engineers, developers and the AI teams who have to run their
agents in production.

  
    Authentication, cost attribution, PII blocking and budget limits are set
    once at the gateway and inherited by every app that uses DDK.
  
  
    The same MuleSoft API management plane that already governs thousands of
    enterprise APIs, now covering model traffic. No second gateway to buy,
    staff or audit.
  
  
    One import, your framework, your IDE, your code. DDK returns native
    framework objects, not wrappers — eight frameworks, three lines to eject.
  
  
    Governed model access today, with governed tool access, agent-to-agent
    calls and publishing to the Agent Fabric registry on the same foundation.
  

  **DDK is an open-source, community-driven project** (Apache-2.0). It is **not
  an official Salesforce or MuleSoft product** and is not supported by
  Salesforce. "Agent Fabric", "Anypoint", "MuleSoft" and "Omni Gateway" are
  Salesforce trademarks; DDK uses them only to describe the platform it
  connects to. Meet the people behind it on the [Team](https://donkey-development-kit.github.io/donkey-development-kit/community/team.md) page.

## Architecture

DDK sits inside your agent process and speaks to the platform on three
fronts: governed calls through the gateway, assets published to the control
plane, and telemetry to your observability stack.

**The AI control plane** is where the platform team manages the AI estate:
the agent registry, cost control, gateway federation, and governance and
observability across every runtime.

**Omni Gateway** is the single data-plane entry point for APIs, MCP servers,
LLMs and agents. Its policies do the enforcing — authentication and identity,
token budgets and rate limits, PII detection, prompt-injection and
content-safety guardrails, model routing and fallback, audit trails, contract
drift and tool-poisoning detection — before traffic reaches the managed
upstreams: enterprise APIs and integrations, MCP servers, LLM providers, and
other AI apps and agents.

**DDK** is the developer-side half of that picture. It is wired into the
framework client your agent already uses and adds:

- **Governed calls and typed refusals** — every model request goes through the
  gateway with consumer credentials, correlation and attribution headers
  injected; every policy rejection comes back as a typed exception such as
  `PIIDetected` or `TokenBudgetExceeded`, never confused with an auth error.
- **Token budget awareness** — the gateway's rate-limit headers become a
  `Budget` object with `remaining`, `pace()` and `wait_for_reset()`.
- **Local testing** — `donkey mock` and `donkey.simulate()` replay real
  gateway rejection shapes on your laptop, and a pytest conformance suite
  proves your agent handles each one before it ships.
- **OpenTelemetry GenAI spans** — each call emits a span carrying the policy
  decision, policy type, budget and correlation ID, exported to whatever
  observability stack you run (Grafana, Datadog, Jaeger, and others) and
  joined to the gateway's audit record through the correlation ID.
- **Registry and agent-to-agent** — scanning your code to publish tools and
  agent cards to the control plane, and serving or exposing your agent to
  other agents over A2A.

The division of labour is deliberate. The gateway enforces; DDK makes the
enforcement legible and actionable inside the agent. Nothing in DDK
re-implements a policy client-side, and nothing in your process can override
the gateway.

## Before and after

Take the most ordinary piece of agent code there is: one model call.

**Without DDK**, a stock client talks straight to the model provider. It works
— and it is invisible. There are no centralised controls or policy
enforcement, no usage tracking, no record of which team or agent spent which
tokens, and nothing an auditor can follow.

```python
import openai

client = openai.OpenAI()
completion = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Capital of Switzerland?"}],
)
print(completion.choices[0].message.content)
```

**With DDK**, the call is the same shape and the client is the same native
`openai.OpenAI` — but it now goes through your organisation's Omni Gateway.
The request is authenticated and attributed, policy is applied, the agent
knows how much budget it has left, and the platform team sees the usage per
model and per consumer in Agent Fabric.

```python
from donkey_kit import Donkey

with Donkey.from_env() as donkey:
    client = donkey.llm.client(sync=True)      # a real openai.OpenAI, governed
    reply = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Capital of Switzerland?"}],
    )
    print(reply.choices[0].message.content)
    print(donkey.budget.remaining, "tokens left")
```

Three lines changed. Governed, observable and attributed — without leaving
your framework.

## Make a governed call

From Python, DDK hands you your framework's own objects. From any other
language, call the same governed proxy over its OpenAI-compatible HTTP API
with the `client_id` / `client_secret` header pair.

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

## Native objects, never wrappers

> **Adapters return the framework's native object — never a wrapper.**

`donkey.langgraph.chat_model("gpt-4o")` returns a real `langchain_openai.ChatOpenAI`.
`donkey.llamaindex.llm("gpt-4o")` returns a real `OpenAILike`. Hand them
straight to `create_agent`, a LlamaIndex query engine or a Strands `Agent`.
And if you ever want to drop DDK, you eject to three lines of native
constructor code — every [framework page](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md) shows exactly which
three.

A stock client with a `base_url` and two headers can reach the gateway. What
DDK adds is the **single place in your process** where every request enters
and every response leaves — which is where typed refusals, budget pacing,
correlation IDs, cost tags, spans and simulation all attach without you
wiring each one.

## Next steps

  
    Your first governed call in minutes — no gateway or credentials needed.
  
  
    Every capability, its purpose, and what it saves you.
  
  
    Install and quickstart for each of the eight supported frameworks.
  
  
    Runnable demos, from a first governed call to a full LangGraph agent.
  
  
    What is available now and what is coming next, phase by phase.
  
  
    DDK is open source — issues, docs, examples and adapters welcome.
