# Discovery

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Discovery reads the Exchange catalog and the gateway instances in front of it.
What you get back depends on the surface, because the three surfaces are found
in different ways.

| Surface | You ask for | You get | Page |
| --- | --- | --- | --- |
| **Models** | A model id you already know | A `ModelHandle` from `donkey.llm.resolve()`. The proxy has no catalog endpoint to list. | [Models](https://docs.donkey-kit.dev/registry/discovery/models.md) |
| **MCP tools** | Governed tools, by governance, tags or name | A `ToolSet` from `donkey.tools.discover()`, or registry handles from `donkey.registry.search()` | [MCP tools](https://docs.donkey-kit.dev/registry/discovery/mcp.md) |
| **A2A agents** | One agent by its Exchange reference | An `AgentHandle` with the deployed endpoint, from `donkey.registry.resolve_agent()` | [A2A agents](https://docs.donkey-kit.dev/registry/discovery/a2a.md) |

  
    What identifies a model, and how to read an `llm` asset's declared metadata.
  
  
    Filter, search and `explain()` why a tool was included or excluded.
  
  
    Resolve a published agent to its gateway endpoint.
  

After discovery, [Connect](https://docs.donkey-kit.dev/connect.md) covers binding the result into your
framework or calling it.
