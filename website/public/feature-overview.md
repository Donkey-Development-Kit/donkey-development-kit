# Feature overview

DDK is **gateway-aware**: control stays at the proxy, efficiency moves into the
agent. The gateway enforces policy; DDK makes each decision visible and
actionable in your code, so the agent reacts to a refusal, paces its budget and
reports what it did.

Everything below hangs off a single shared transport inside your process — the
one place where every request enters and every response leaves. Each capability
attaches there once, so you never wire it call by call.

## Model access

  
    **Purpose:** point any of eight agent frameworks at your governed Omni
    Gateway proxy. **Advantage:** you get your framework's own object —
    `ChatOpenAI`, `LiteLlm`, `OpenAIModel`, `crewai.LLM` … — with credentials,
    correlation, attribution and retry policy injected. No wrapper to code
    around, three lines to eject.
  
  
    **Purpose:** use the OpenAI SDK directly. **Advantage:**
    `donkey.llm.client()` returns a native `AsyncOpenAI` (or `OpenAI` with
    `sync=True`) — Chat Completions and Responses, streaming included — governed
    on identical terms.
  

| Framework | Call | Returns |
|---|---|---|
| LangGraph | `donkey.langgraph.chat_model("gpt-4o")` | `langchain_openai.ChatOpenAI` |
| Google ADK | `donkey.adk.model("gpt-4o")` | `LiteLlm` |
| Strands | `donkey.strands.model("gpt-4o")` | `OpenAIModel` |
| MS Agent Framework | `donkey.agent_framework.chat_client("gpt-4o")` | Agent Framework chat client |
| LlamaIndex | `donkey.llamaindex.llm("gpt-4o")` | `OpenAILike` |
| OpenAI Agents SDK | `donkey.openai_agents.model("gpt-4o")` | `OpenAIChatCompletionsModel` |
| Anthropic SDK | `donkey.anthropic.client()` | `anthropic.AsyncAnthropic` |
| CrewAI | `donkey.crewai.llm("gpt-4o")` | `crewai.LLM` |

Every adapter offers the same governed connection three ways — a factory on a
shared `Donkey` (`donkey.langgraph.chat_model(...)`), a module-level factory
(`from donkey_kit.integrations.langgraph import chat_model`), or
`connection_kwargs()` when you want to build the native object yourself.

## Governance

  
    **Purpose:** turn every gateway rejection into a typed exception —
    `PIIDetected`, `TokenBudgetExceeded`, `PromptInjectionBlocked`,
    `ContentSafetyBlocked`, `AuthError`, `GatewayUnavailable` and more.
    **Goal:** branch on the governance outcome, not on a parsed error body.
    **Advantage:** a PII block is never mistaken for an auth failure, and a
    policy `429` is never retried.
  
  
    **Purpose:** expose the gateway's token window as a `Budget` object.
    **Goal:** stop *before* the limit, not after it. **Advantage:**
    `pace(reserve=)` and `wait_for_reset()` let an overnight batch slow down
    instead of dying at 2am.
  
  
    **Purpose:** on-behalf-of token exchange. **Goal:** per-user policy
    reaches the gateway. **Advantage:** requests never silently fall back to
    the service identity.
  
  
    **Purpose:** one vocabulary for "pause and ask a human". **Advantage:**
    mapped onto each framework's native interrupt, so approval flows look the
    same everywhere.
  
  
    **Purpose:** read the policy set in force. **Advantage:** skip calls that
    are certain to be refused. Advisory only — the gateway always wins.
  

## Observability

  
    **Purpose:** one span per governed call, using the GenAI semantic
    conventions plus a stable `donkey.*` namespace — policy decision, policy
    type, budget, routing and token usage. **Advantage:** refused calls still
    produce a span, streaming produces exactly one, and prompt content stays
    out by default. Zero-config OTLP export.
  
  
    **Purpose:** `donkey.run(id=…, team=…, project=…)` binds one correlation
    ID and validated cost tags to every call in a task. **Advantage:** your
    log line, your span and the gateway's audit record join on the same ID.
  
  
    **Purpose:** `donkey.last_call` records which gateway served the call,
    how it was routed and what it used. **Advantage:** detect a model
    substitution — or make it raise — instead of discovering it in a bill.
  

## Developer tooling

  
    **Purpose:** `donkey mock` replays real gateway responses and refusal
    shapes on `127.0.0.1`. **Advantage:** build and demo against governance
    without an Anypoint account or credentials.
  
  
    **Purpose:** `donkey.simulate()` injects a refusal in-process, and a
    pytest plugin grades *your* agent against every refusal shape.
    **Advantage:** the PII branch is tested before production, not in it.
  
  
    **Purpose:** `donkey init`, `doctor`, `mock` and `test`, plus
    `@donkey.governed` and `@donkey.tool`. **Advantage:** `doctor` tells wrong
    credentials from wrong URL from model-not-allowed; one decorator gives a
    function a run scope, span and typed refusals.
  
  
    **Purpose:** the docs are published as `llms.txt` and per-page markdown.
    **Advantage:** Cursor, Claude Code and other assistants write correct DDK
    code from the source.
  

## Registry & catalog

  
    **Purpose:** discover governed MCP tools from the catalog and bind them as
    native framework tools. **Advantage:** allow/deny filtering, pinning and a
    lockfile — only governed tools reach your agent.
  
  
    **Purpose:** `serve`, `expose` and `dev` make your agent callable by other
    agents, on the official `a2a-sdk`. **Advantage:** inbound tasks are
    governed with the same correlation, spans and refusals.
  
  
    **Purpose:** derive a manifest and agent card from your code and register
    them in the Agent Fabric registry. **Advantage:** the catalog stays in sync
    from CI, not by hand.
  

## What DDK leaves to the platform

DDK makes the platform's capabilities reachable and typed; it does not
reproduce them. Policy enforcement, semantic caching, provisioning, agent
scanners, kill switch, trusted agent identity, approval UIs and evaluation all
stay with Agent Fabric and Omni Gateway. See the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md#what-ddk-will-not-build)
for the full list.
