# Registry & catalog

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Anypoint Exchange is the catalog behind the gateway: it lists the LLM proxies,
MCP servers and A2A agents your organisation has published, and API Manager
says which of them are fronted by a gateway instance. DDK's registry features
read that catalog, pin what they found, and write your own agents and tools back
to it. The same four jobs apply to each surface.

| Job | Models | MCP tools | A2A agents |
| --- | --- | --- | --- |
| **Discover** | [Models](https://docs.donkey-kit.dev/registry/discovery/models.md) | [MCP tools](https://docs.donkey-kit.dev/registry/discovery/mcp.md) | [A2A agents](https://docs.donkey-kit.dev/registry/discovery/a2a.md) |
| **Pin** | n/a | [Pinning & lockfile](https://docs.donkey-kit.dev/registry/lockfile.md) | n/a |
| **Publish** | n/a | [Scan & publish](https://docs.donkey-kit.dev/registry/publish.md#publishing-mcp-servers) | [Scan & publish](https://docs.donkey-kit.dev/registry/publish.md#publishing-agents) |
| **Serve** | n/a | n/a | [Serve & expose](https://docs.donkey-kit.dev/registry/serve.md) |

**n/a** means the capability does not apply to that surface today, not that it
is missing from the docs. A model has no catalog endpoint to list, and no
lockfile. An MCP server is not served by DDK, it is published and then called.

Each linked page carries its own status badge. This page makes no status claims
of its own and takes the lowest status among them.

## The four jobs

  
    Find governed assets: `donkey.tools.discover()` and `donkey.registry.search()`
    for MCP tools, `resolve_agent()` for A2A, and the model reads that exist.
  
  
    Pin each MCP server's listed tools in `donkey.lock`, so catalog drift is a
    typed `ToolLockDrift` and a reviewable diff.
  
  
    `donkey scan`, `publish` and `verify` register what your code declares, from
    CI, and check the live side against it.
  
  
    `donkey.serve()` and `donkey expose` make your agent callable by other
    agents through the gateway.
  

## Where the registry shows up elsewhere

- Calling a discovered asset is on [Connect](https://docs.donkey-kit.dev/connect.md), not here. Discovery
  returns a handle or a `ToolSet`, and Connect turns it into a call.
- [Gateway policies](https://docs.donkey-kit.dev/governance/gateway-policies.md) says what the gateway then
  does to that call.
- The control-plane calls a registry read makes, and the scopes they need, are on
  [Control plane](https://docs.donkey-kit.dev/reference/configuration/control-plane.md) and
  [Connected-app scopes](https://docs.donkey-kit.dev/reference/scopes.md).
