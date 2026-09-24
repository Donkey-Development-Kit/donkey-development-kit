# Tool access

Roadmap

This capability is on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md); the API shown here is the planned design.

Governed tool access lets an agent discover the MCP tools your organisation has
published and governed, filter them down to what it actually needs, and bind
them into any of the eight supported frameworks as that framework's **native
tool objects** — the same "no wrapper" approach as [model access](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md).

## Two lines from catalog to agent

```python
tools = await donkey.tools.discover(domain="hr", tags=["approved"])
agent = create_react_agent(donkey.langgraph.chat_model("gpt-4o"), tools.langgraph())
```

Everything else in this section — search filters, session management,
per-framework binding, pinning, and A2A tool handles — makes those two lines
hold up in production.

## Discover and filter

`donkey.tools.discover(...)` is also the search and filter entry point. One call
narrows the catalog by name/description glob, governance, domain, tags, and
asset type, so an agent binds only the tools it needs:

```python
tools = await donkey.tools.discover(
    search="*accounts*",     # glob over asset name + description
    governed_only=True,      # default criteria, or a GovernanceCriteria
    domain="hr",
    tags=["approved"],
    asset_types=["mcp"],
    environment="Production",
    limit=50,
)
```

"Governed" is a computed, environment-scoped predicate rather than a flag in
Exchange — see [Discovery, search & filter](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/discovery.md).

## In this section

  
    Narrow the catalog by name, governance, domain, tags, and asset type.
  
  
    Turn a `ToolSet` into each framework's own native tool objects.
  
  
    Pin resolved versions and digests so a run is reproducible.
  
  
    Treat a governed agent-to-agent endpoint as another bindable tool.
