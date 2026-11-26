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
| [A2A agents](https://docs.donkey-kit.dev/a2a.md) | Nothing framework-specific today. `donkey.a2a.send()` and `donkey.serve()` work the same for every framework. Binding an A2A agent as a framework tool is planned. | [Call other agents](https://docs.donkey-kit.dev/a2a/call.md), [Serve & expose](https://docs.donkey-kit.dev/registry/serve.md) |

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

The binding shape decides how you filter and refuse tools. Each framework gets
a `ToolSet` method that returns its own native MCP objects.

| Framework | Method on `ToolSet` | Binds | Depth to know about |
| --- | --- | --- | --- |
| [LangGraph](https://docs.donkey-kit.dev/frameworks/langgraph.md) | `await ts.langgraph()` | Each tool | Typed refusals reach your code. Rename on collision and the approval gate work. |
| [LlamaIndex](https://docs.donkey-kit.dev/frameworks/llamaindex.md) | `await ts.llamaindex()` | Each tool | Typed refusals reach your code. Rename on collision and the approval gate work. |
| [Anthropic SDK](https://docs.donkey-kit.dev/frameworks/anthropic.md) | `async with ts.anthropic()` | Each tool | Typed refusals reach your code. Rename on collision and the approval gate work. |
| [Google ADK](https://docs.donkey-kit.dev/frameworks/adk.md) | `await ts.adk()` | A whole server | A name collision or an approval rule raises `ConfigError`. |
| [Strands](https://docs.donkey-kit.dev/frameworks/strands.md) | `await ts.strands()` | A whole server | A name collision or an approval rule raises `ConfigError`. Tool errors come back as text. |
| [OpenAI Agents SDK](https://docs.donkey-kit.dev/frameworks/openai.md) | `await ts.openai_agents()` | A whole server | A name collision or an approval rule raises `ConfigError`. |
| [MS Agent Framework](https://docs.donkey-kit.dev/frameworks/agent-framework.md) | `await ts.agent_framework()` | A whole server | A name collision or an approval rule raises `ConfigError`. |
| [CrewAI](https://docs.donkey-kit.dev/frameworks/crewai.md) | `async with ts.crewai()` | A whole server | A name collision or an approval rule raises `ConfigError`. |

The classes and install extras are in [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md#per-framework-binding).
The per-framework refusal behaviour is on the
[MCP gateway policies](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md) page, and the same list
sits on the [MCP tools overview](https://docs.donkey-kit.dev/tool-access.md#supported-frameworks).

## A2A agents

A2A is not bound per framework. The same calls work for every framework, so an
agent built with any of the eight can call or serve other agents.

| Framework | Call another agent | Serve your agent | `as_tool()` binding |
| --- | --- | --- | --- |
| [LangGraph](https://docs.donkey-kit.dev/frameworks/langgraph.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |
| [Google ADK](https://docs.donkey-kit.dev/frameworks/adk.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |
| [Strands](https://docs.donkey-kit.dev/frameworks/strands.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |
| [MS Agent Framework](https://docs.donkey-kit.dev/frameworks/agent-framework.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |
| [OpenAI Agents SDK](https://docs.donkey-kit.dev/frameworks/openai.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |
| [Anthropic SDK](https://docs.donkey-kit.dev/frameworks/anthropic.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |
| [CrewAI](https://docs.donkey-kit.dev/frameworks/crewai.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |
| [LlamaIndex](https://docs.donkey-kit.dev/frameworks/llamaindex.md) | `donkey.a2a.send()` / `stream()` | `donkey.serve()` | Planned |

Framework-native `as_tool()` binding is planned, not shipped. See
[Call other agents](https://docs.donkey-kit.dev/a2a/call.md) and the [A2A overview](https://docs.donkey-kit.dev/a2a.md#supported-frameworks).

## Next steps

- [Models](https://docs.donkey-kit.dev/frameworks.md): the framework adapters, gateway policies and routing.
- [MCP tools](https://docs.donkey-kit.dev/tool-access.md): discovery, binding and the MCP gateway policies.
- [A2A agents](https://docs.donkey-kit.dev/a2a.md): serving and calling agents through the gateway.
- [Feature overview](https://docs.donkey-kit.dev/feature-overview.md): every capability, grouped by surface.
