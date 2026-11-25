# Supported frameworks

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

DDK supports eight agent frameworks. Each one is wired into a surface, and the
depth varies by surface. This page is the map: which framework goes where, how
far DDK goes for it, and which page has the how-to. The how-to itself lives in
the surface sections, so each one is documented once.

| Surface | What a framework gets | Where the how-to lives |
| --- | --- | --- |
| [Models](https://docs.donkey-kit.dev/frameworks.md) | A governed model object from the framework's own API (`chat_model()`, `model()`, `llm()` and so on), with credentials, correlation, attribution and retry policy applied. | [Models](https://docs.donkey-kit.dev/frameworks.md), with one page per framework |
| [MCP tools](https://docs.donkey-kit.dev/tool-access.md) | Governed MCP tools bound as the framework's native tools. Some frameworks bind one tool at a time; others take a whole server. | [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md) |
| [A2A agents](https://docs.donkey-kit.dev/a2a.md) | Nothing framework-specific today. `donkey.a2a.send()` and `donkey.serve()` work the same for every framework. Binding an A2A agent as a framework tool is planned. | [Call other agents](https://docs.donkey-kit.dev/a2a/call.md), [Serve & expose](https://docs.donkey-kit.dev/a2a/serve.md) |

## Models

Every framework has a factory on the shared `Donkey` object, and the adapter
page for each one shows the exact call and what it returns.

| Framework | Factory | Depth to know about |
| --- | --- | --- |
| [LangGraph](https://docs.donkey-kit.dev/frameworks/langgraph.md) | `donkey.langgraph.chat_model()` | Full model path: typed refusals and `last_call` are observed. |
| [Google ADK](https://docs.donkey-kit.dev/frameworks/adk.md) | `donkey.adk.model()`, `donkey.adk.gemini()` | Full model path, plus a native Gemini route on a `Format=Gemini` proxy. |
| [Strands](https://docs.donkey-kit.dev/frameworks/strands.md) | `donkey.strands.model()` | Streaming is off on the governed connection (`stream=False`). |
| [MS Agent Framework](https://docs.donkey-kit.dev/frameworks/agent-framework.md) | `donkey.agent_framework.chat_client()` | Full model path. |
| [OpenAI Agents SDK](https://docs.donkey-kit.dev/frameworks/openai.md) | `donkey.openai_agents.model()` | Full model path. |
| [Anthropic SDK](https://docs.donkey-kit.dev/frameworks/anthropic.md) | `donkey.anthropic.client()` | Full model path. |
| [CrewAI](https://docs.donkey-kit.dev/frameworks/crewai.md) | `donkey.crewai.llm()` | CrewAI builds its own HTTP client, so the JWT and bearer auth modes are refused and `last_call` is not observed. |
| [LlamaIndex](https://docs.donkey-kit.dev/frameworks/llamaindex.md) | `donkey.llamaindex.llm()` | Full model path. |

The per-factory capability table (transport, sync, streaming, typed refusals,
observed `last_call`) is on the [Models overview](https://docs.donkey-kit.dev/frameworks.md#what-each-factory-gets-capabilities).

  "Full model path" means the framework's model object goes through DDK's shared
  transport, so refusals, `last_call` and budget reads reach your code. Where the
  table marks a capability off, read that adapter's page before relying on it.

## MCP tools

The binding shape decides how you filter and refuse tools:

- **Per tool.** [LangGraph](https://docs.donkey-kit.dev/tool-access/binding.md), [LlamaIndex](https://docs.donkey-kit.dev/tool-access/binding.md) and the
  [Anthropic SDK](https://docs.donkey-kit.dev/tool-access/binding.md) bind each tool separately. Typed refusals
  from a tool call reach your code through these bindings.
- **Per server.** [Google ADK](https://docs.donkey-kit.dev/tool-access/binding.md), [Strands](https://docs.donkey-kit.dev/tool-access/binding.md),
  [OpenAI Agents SDK](https://docs.donkey-kit.dev/tool-access/binding.md), [MS Agent Framework](https://docs.donkey-kit.dev/tool-access/binding.md) and
  [CrewAI](https://docs.donkey-kit.dev/tool-access/binding.md) bind a whole server. Some of these return tool errors as
  text, so the policy metadata is lost. The per-framework refusal behaviour is on the
  [MCP gateway policies](https://docs.donkey-kit.dev/tool-access/gateway-policies.md) page.

## A2A agents

A2A is not bound per framework. `donkey.a2a.send()` and `donkey.serve()` are the
same for every framework, so an agent built with any of the eight can call or
serve other agents. Framework-native `as_tool()` binding is still planned; see
[Call other agents](https://docs.donkey-kit.dev/a2a/call.md).

## Next steps

- [Models](https://docs.donkey-kit.dev/frameworks.md): the framework adapters, gateway policies and routing.
- [MCP tools](https://docs.donkey-kit.dev/tool-access.md): discovery, binding and the MCP gateway policies.
- [A2A agents](https://docs.donkey-kit.dev/a2a.md): serving and calling agents through the gateway.
- [Feature overview](https://docs.donkey-kit.dev/feature-overview.md): every capability, grouped by surface.
