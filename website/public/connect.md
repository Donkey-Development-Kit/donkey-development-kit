# Connect

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Connecting is the first job: getting your agent's calls to a model, a tool or
another agent through Omni Gateway, with credentials, correlation and
attribution already set. The same shared transport carries all three, so
everything under [Governance](https://docs.donkey-kit.dev/governance/gateway-policies.md) and
[Observability](https://docs.donkey-kit.dev/telemetry.md) applies to each without extra wiring.

  
    Your framework's own model object, pointed at a governed LLM proxy. Eight
    frameworks, no wrapper.
  
  
    Discover governed MCP servers and bind their tools as native framework tool
    objects.
  
  
    Delegate to another governed agent with `donkey.a2a.send()` or `stream()`.
  

## What each surface needs

| | Models | MCP tools | A2A agents |
| --- | --- | --- | --- |
| **You write** | `donkey.<framework>.chat_model(...)` or the framework's equivalent | `await donkey.tools.discover()`, then a per-framework bind method | `await donkey.a2a.send(...)` or `stream()` |
| **You get** | The framework's native model object | The framework's native tool objects | The official `a2a-sdk` request and response types |
| **How it is found** | By model id, see [Discovery for models](https://docs.donkey-kit.dev/registry/discovery/models.md) | By governance and tags, see [Discovery for MCP tools](https://docs.donkey-kit.dev/registry/discovery/mcp.md) | By Exchange reference, see [Discovery for A2A agents](https://docs.donkey-kit.dev/registry/discovery/a2a.md) |
| **Start here** | [Models](https://docs.donkey-kit.dev/frameworks.md) | [MCP tools](https://docs.donkey-kit.dev/tool-access.md) | [A2A agents](https://docs.donkey-kit.dev/a2a.md) |

Which frameworks are supported on which surface, and how deep each one goes, is
on [Supported frameworks](https://docs.donkey-kit.dev/supported-frameworks.md). Each framework's own page
(for example [LangGraph](https://docs.donkey-kit.dev/frameworks/langgraph.md)) shows the model code, and the
tool-binding code is on [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md).

## After you connect

- [Registry & catalog](https://docs.donkey-kit.dev/registry.md): find, pin and publish the assets you connect to.
- [Gateway policies](https://docs.donkey-kit.dev/governance/gateway-policies.md): what the gateway does to each call.
- [Typed refusals](https://docs.donkey-kit.dev/errors.md): what your code catches when it says no.
