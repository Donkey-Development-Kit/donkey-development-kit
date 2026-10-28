# Tool access

Status: Live. Shipped, and verified against a real governed gateway.

Discovery and binding run end to end through the governed gateway, with
every supported framework; see [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md).

Governed tool access lets an agent discover the MCP tools your organisation has
published and governed, filter them down to what it actually needs, and bind
them into any of the eight supported frameworks as that framework's **native
tool objects** — the same "no wrapper" approach as [model access](https://docs.donkey-kit.dev/frameworks.md).

## Two lines from catalog to agent

```python
tools = await donkey.tools.discover(tags=["approved"], governed=True)
agent = create_react_agent(donkey.langgraph.chat_model("gpt-4o"), await tools.langgraph())
```

Everything else in this section — search filters, session management,
per-framework binding, pinning, and A2A tool handles — makes those two lines
hold up in production.

## Discover and filter

`donkey.tools.discover(...)` is also the filter entry point. One call narrows
the catalog by governance and tags, so an agent binds only the tools it
needs:

```python
tools = await donkey.tools.discover(
    tags=["approved"],
    governed=True,           # default criteria, or a GovernanceCriteria
)
```

Name search and asset type filters live one layer down, on
`donkey.registry.search()`, which is live.

"Governed" is a computed, environment-scoped predicate rather than a flag in
Exchange — see [Discovery, search & filter](https://docs.donkey-kit.dev/tool-access/discovery.md).

## In this section

  
    Narrow the catalog by governance and tags, or search it by name and asset type.
  
  
    Turn a `ToolSet` into each framework's own native tool objects.
  
  
    Pin resolved versions and digests so a run is reproducible.
  
  
    Treat a governed agent-to-agent endpoint as another bindable tool.
