# Framework binding

Roadmap

This capability is on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md); the API shown here is the planned design.

Once [discovery](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/discovery.md) hands you a `ToolSet`, binding turns
governed MCP servers into a framework's own tool objects — nothing wrapped,
nothing re-implemented.

## MCP session management

MCP servers created by MCP Bridge are gateway endpoints speaking streamable
HTTP, protected by gateway policies. The SDK's session layer handles four
things for you:

- **Auth.** Client-credentials OAuth is the machine-to-machine case. Strands'
  `MCPClient` already builds streamable HTTP with a `client_credentials` grant
  internally; every other framework needs headers supplied explicitly.
  `McpServerHandle.auth_headers()` returns a ready-to-use header dict, refreshed
  automatically on a `401`.
- **Connection lifecycle.** MCP clients are stateful, and several frameworks
  connect lazily. `donkey.tools.discover()` never opens a connection — it
  returns handles, and the connection opens on first tool use.
- **Multi-server aggregation.** `ToolSet` wraps N `McpServerHandle`s. When two
  servers expose a tool with the same name, the collision is resolved by
  prefixing the server's short name — for example `hr__get_employee` — and the
  mapping is available on `ToolSet.name_map`, so you can see exactly why the
  model called that name.
- **Filtering.** Enterprise MCP servers can expose dozens of tools. Handing 60
  tool descriptors to a model degrades it and inflates token cost, so filter
  before you bind:

```python
tools = await donkey.tools.discover(domain="hr")
filtered = tools.filter(allow=["get_employee", "search_employees"])
# or:
filtered = tools.filter(deny=["delete_*"])
# or a predicate over the tool descriptor:
filtered = tools.filter(predicate=lambda t: t.name.startswith("get_"))
```

  The SDK logs the descriptor token count for a `ToolSet` at debug level, so
  you can see the cost of skipping `filter()` before a model does.

## Per-framework binding

`ToolSet` exposes one method per installed integration, each returning the
framework's **native** tool type:

```python
ts = await donkey.tools.discover(domain="hr")
ts.langgraph()          # -> list[BaseTool]
ts.adk()                # -> list[McpToolset]
ts.strands()            # -> list[MCPClient]
ts.llamaindex()         # -> list[FunctionTool]
# etc.
```

Note the shape difference: ADK and Strands take a toolset/provider object,
while LangGraph and LlamaIndex take a flat tool list. Each method matches its
framework's own idiom rather than forcing a uniform return type, and its
docstring calls out the difference.

| Framework | Binding |
|---|---|
| LangGraph | `langchain_mcp_adapters.client.MultiServerMCPClient({...}).get_tools()` — the SDK builds the connection dict from your handles, transport `"streamable_http"`, headers injected. |
| Google ADK | `McpToolset(connection_params=StreamableHTTPConnectionParams(url=..., headers=...), tool_filter=[...])`, passed straight into `LlmAgent(tools=[...])`. |
| MS Agent Framework | The framework's MCP client/tool class for streamable HTTP. |
| OpenAI Agents SDK | `agents.mcp.MCPServerStreamableHttp(params={"url": ..., "headers": ...})`, passed into `Agent(mcp_servers=[...])`. |
| Anthropic SDK | The `anthropic` SDK has no native client-side MCP binding; the SDK binds via the MCP Python SDK's streamable-HTTP client and passes the resulting tool schemas to `messages.create(tools=...)`. |
| CrewAI | The framework's MCP adapter for streamable-HTTP servers, yielding native `crewai` tool objects for a `Crew`/`Agent`. |
| LlamaIndex | `llama_index.tools.mcp.BasicMCPClient` + `McpToolSpec(...).to_tool_list_async()`. |
| Strands | `MCPClient(lambda: streamablehttp_client(url, headers=...))` — implements `ToolProvider`, so it can be passed directly into `Agent(tools=[...])` with automatic lifecycle management. |

A binding failure or a `401` surfaces as a typed exception from the
[error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md).

## Related

- [Discovery, search & filter](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/discovery.md) — produce the `ToolSet`.
- [A2A agent tools](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/a2a.md) — bind a remote agent the same way.
- [Frameworks](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md) — governed model access per framework.
