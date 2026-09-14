# Feature overview

What the SDK gives you **today**, wired to the live-verified proxy contract.
For what is designed but not yet shipped, see the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md) — nothing
on this page is speculative.

Everything the SDK adds attaches at stage 2 of that diagram, on a single shared
transport. That is the **skeleton**: one place where every request enters and
every response leaves, which is why budget, spans, correlation IDs and
simulation can all be added later without you rewiring a call site.

## Native framework objects, not wrappers

One call per framework returns the framework's **own** object, already pointed at
the proxy with the verified `client_id`/`client_secret` headers, attribution, and
retry policy injected:

| Framework | Call | Returns |
|---|---|---|
| LangGraph | `donkey.langgraph.chat_model("gpt-4o")` | `langchain_openai.ChatOpenAI` |
| Google ADK | `donkey.adk.model("gpt-4o")` | `google.adk … LiteLlm` |
| Strands | `donkey.strands.model("gpt-4o")` | `strands … OpenAIModel` |
| MS Agent Framework | `donkey.agent_framework.chat_client("gpt-4o")` | Agent Framework chat client |
| LlamaIndex | `donkey.llamaindex.llm("gpt-4o")` | `OpenAILike` (`is_chat_model=True`) |
| OpenAI Agents SDK | `donkey.openai_agents.model("gpt-4o")` | `agents.OpenAIChatCompletionsModel` |
| Anthropic SDK | `donkey.anthropic.client()` | `anthropic.AsyncAnthropic` (client, not model-bound) |
| CrewAI | `donkey.crewai.llm("gpt-4o")` | `crewai.LLM` (LiteLLM-backed) |

See [Model access](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md) for each framework's page. Support **depth**
differs even though the shape does not: LangGraph is the one deep,
conformance-gated adapter, and the other seven are supported at the
`connection_kwargs()` level.

## Three ways to construct — pick your ergonomics

Every adapter offers the same governed connection three ways, from one source of
truth:

```python
# 1. Factory method — shared client + explicit lifecycle
async with Donkey.from_env() as donkey:
    model = donkey.langgraph.chat_model("gpt-4o", temperature=0.2)

# 2. Module-level factory — shortest; cached default from the environment
from donkey_kit.integrations.langgraph import chat_model
model = chat_model("gpt-4o", temperature=0.2)

# 3. Governed-kwargs accessor — you build the native object yourself
from langchain_openai import ChatOpenAI
model = ChatOpenAI(model="gpt-4o", **donkey.langgraph.connection_kwargs())
```

Prefer the factory when you want shared-client reuse and lifecycle
(`async with`); reach for the accessor when you want full control of the
constructor.

## Framework-free client

`donkey.llm.client()` returns a native `AsyncOpenAI` aimed at the proxy — use the
OpenAI SDK exactly as you normally would (Chat Completions or the Responses API,
streaming included); the SDK adds the governance headers and retry policy.

Pass `sync=True` for the blocking `OpenAI` instead, governed on identical terms —
same base URL, same verified `client_id` / `client_secret` headers, same
correlation ID and retry policy:

```python
with Donkey.from_env() as donkey:
    client = donkey.llm.client(sync=True)      # openai.OpenAI
    reply = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Say hi in three words."}],
    )
    print(reply.choices[0].message.content)
```

The two forms are declared as typed overloads, so your editor narrows the result
to `OpenAI` or `AsyncOpenAI` and keeps completing on it. Note the boundary: the
blocking transport takes no `AuthProvider`, because that protocol is async-only.
That costs nothing here — the proxy authenticates on the header pair, not a
fetched token — but it does mean the control-plane surfaces (`registry`,
`tools`) remain async.

## A typed governed error taxonomy

`classify()` maps the proxy's eight rejection shapes to typed exceptions —
`PIIDetected` (403), `TokenBudgetExceeded` (429), `PromptInjectionBlocked`
(`x-injection-protection` **or** the regex prompt-guard's `matched_patterns`),
`ContentSafetyBlocked` (Azure Content Safety / Bedrock Guardrails vendor reject
header; parses `categories`), a generic `PolicyViolation` fall-through for
undiscriminated content-moderation, `UpstreamRequestError` (4xx) and
`UpstreamModelError` (5xx), plus `AuthError` (401) for the separate consumer-auth
case — so you branch on governance outcomes instead of parsing bodies. Four are
live-verified; the injection, regex-prompt-guard, content-safety and
content-moderation bodies are pinned from the policy pages, pending sandbox
capture (#253). See [Error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md).

Those exception types are the client-side mirror of the policies the gateway
enforces. The **LLM** lane below is the one this SDK targets today; the same
control plane applies equivalent policy sets to APIs, MCP tools, and A2A
agents. Those two columns are not decoration — **MCP** is what
[tool access](https://donkey-development-kit.github.io/donkey-development-kit/tool-access.md) reaches in Phase 2, and **Agents** is what
[A2A](https://donkey-development-kit.github.io/donkey-development-kit/a2a.md) reaches in the same phase.

## Model handles without a `/models` endpoint

The governed proxy has **no** catalog endpoint (`GET /models` → `404`, verified).
`donkey.llm.resolve("gpt-4o")` gives you a heuristic capability handle
(function-calling / vision / json-output) from a known id, and
`list_models(live=True)` raises a clear `ConfigError` explaining the absence
rather than fabricating a path.

  **Honesty note (§0.3).** The proxy *contract* the adapters target is
  live-verified, but the exact framework *constructor signatures* are still being
  confirmed against installed versions. Where a
  name can't be resolved, the adapter raises `blocked on verification` rather
  than guess. The [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md) page tracks
  current status.
