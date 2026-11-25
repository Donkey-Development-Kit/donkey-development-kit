# MCP tools

Status: Live. Shipped, and verified against a real governed gateway.

This section is DDK's MCP support. It lets an agent discover the MCP servers
and tools your organisation has published to Anypoint Exchange and governed,
filter them down to what it actually needs, and bind
them into any of the eight supported frameworks as that framework's **native
tool objects** — the same "no wrapper" approach as [model access](https://docs.donkey-kit.dev/frameworks.md).
A lockfile pins what each server listed, so a catalog change made at the
gateway fails loudly instead of silently changing your agent's tools. When a
gateway policy refuses a tool call, DDK raises the refusals it can identify as
typed exceptions; [Gateway policies](https://docs.donkey-kit.dev/tool-access/gateway-policies.md) lists what
each policy does and how far that typing reaches per framework.

Discovery and binding run end to end through the governed gateway, with
every supported framework; see [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md).

## Two lines from catalog to agent

```python
tools = await donkey.tools.discover(tags=["approved"])
agent = create_react_agent(donkey.langgraph.chat_model("gpt-4o"), await tools.langgraph())
```

Everything else in this section — search filters, session management,
per-framework binding and lockfile pinning — makes those two lines
hold up in production.

Calling another agent over A2A is not an MCP tool call; it has its own
section: see [Call other A2A agents](https://docs.donkey-kit.dev/a2a/call.md).

## Discover and filter

`donkey.tools.discover(...)` is also the filter entry point. One call narrows
the catalog by governance and tags, so an agent binds only the tools it
needs:

```python
tools = await donkey.tools.discover(
    tags=["approved"],
    governed=True,           # the default: default criteria; or an explicit GovernanceCriteria
)
```

Name search and asset type filters live one layer down, on
`donkey.registry.search()`, which is live.

"Governed" is a computed, environment-scoped predicate rather than a flag in
Exchange — see [Discovery, search & filter](https://docs.donkey-kit.dev/tool-access/discovery.md).

## In this section

  
    Narrow the catalog by governance and tags, or search it by name and asset type.
  
  
    Turn a `ToolSet` into each framework's own native tool objects.
  
  
    Pin each server's listed tools in `donkey.lock` and fail on drift.
  
  
    What MCP gateway policies do to catalogs and calls, and what your code sees.
  

## MCP across the docs

Other sections cover MCP where it meets their topic:

- **Typed refusals:** [JSON-RPC refusal matching](https://docs.donkey-kit.dev/errors.md#json-rpc-refusal-matching)
  and [MCP PII metadata](https://docs.donkey-kit.dev/errors.md#mcp-pii-metadata).
- **Local simulator:** [MCP tool fault injection](https://docs.donkey-kit.dev/simulator.md#mcp-tool-fault-injection)
  and [packaged MCP captures](https://docs.donkey-kit.dev/simulator.md#packaged-mcp-captures).
- **Human-in-the-loop:** [discovered tools](https://docs.donkey-kit.dev/hitl.md#discovered-tools-local-round-trip),
  [waiting while listing MCP tools](https://docs.donkey-kit.dev/hitl.md#waiting-while-listing-mcp-tools) and
  [step-up on a tool call](https://docs.donkey-kit.dev/hitl.md#step-up-on-a-tool-call).
- **Identity:** [acting for a user](https://docs.donkey-kit.dev/tool-access/binding.md#acting-for-a-user) with a
  bound `ToolSet`, and [Identity](https://docs.donkey-kit.dev/identity.md) for `donkey.as_user()`.
- **Scan & publish:** [publishing MCP servers](https://docs.donkey-kit.dev/publishing.md#publishing-mcp-servers)
  and [declaring how an MCP server is reached](https://docs.donkey-kit.dev/publishing.md#declaring-how-an-mcp-server-is-reached).
- **CLI:** [`donkey scan`](https://docs.donkey-kit.dev/cli.md#donkey-scan), [`donkey publish`](https://docs.donkey-kit.dev/cli.md#donkey-publish)
  and [`donkey mock`](https://docs.donkey-kit.dev/cli.md#donkey-mock).
- **Reference:** [connected-app scopes](https://docs.donkey-kit.dev/reference/scopes.md#per-operation) and the
  [unsupported boundary](https://docs.donkey-kit.dev/reference/unsupported-boundary.md#current-boundary).

## Next steps

1. [Discover](https://docs.donkey-kit.dev/tool-access/discovery.md) the governed tools your agent needs.
2. [Bind](https://docs.donkey-kit.dev/tool-access/binding.md) them into your framework.
3. Read what the [gateway policies](https://docs.donkey-kit.dev/tool-access/gateway-policies.md) on your MCP
   instance do, and pin the result with the [lockfile](https://docs.donkey-kit.dev/tool-access/lockfile.md).
