# A2A agent tools

Roadmap

This capability is on the [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md); the API shown here is the planned design.

Agent Broker is an A2A server, and A2A-compliant agents in the registry can be
consumed directly from Python. `AgentHandle.as_tool()` wraps a remote A2A agent
as a callable tool in your framework — the same "resolve, then bind" shape as
MCP tools, for a remote agent instead of a remote tool server.

```python
handle = await donkey.registry.resolve_agent("com.acme/claims-triage-agent/1.0.0")
tool = handle.as_tool()          # callable in your framework's native tool shape

agent = create_react_agent(donkey.langgraph.chat_model("gpt-4o"), [tool])
```

This lets a Python agent **delegate** to an Agent Broker agent without you
learning the A2A protocol. Under the hood, `as_tool()` uses the official
`a2a-sdk` for protocol handling.

## Install

A2A support ships behind its own extra, so installs that only need MCP tools
carry no extra dependency:

```bash
pip install "donkey-kit[a2a]"
```

## Where this fits

- `AgentHandle` comes from `ExchangeRegistry.resolve_agent()` — the same
  registry surface that resolves MCP servers into `McpServerHandle`s. See
  [Discovery, search & filter](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/discovery.md).
- `as_tool()` returns a framework-native callable, following the same
  conventions as [Framework binding](https://donkey-development-kit.github.io/donkey-development-kit/tool-access/binding.md).
- To make *your* agent callable by others over A2A, see [A2A agents](https://donkey-development-kit.github.io/donkey-development-kit/a2a.md).
