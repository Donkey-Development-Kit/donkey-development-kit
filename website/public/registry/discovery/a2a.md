# Discovery for A2A agents

Status: Live. Shipped, and verified against a real governed gateway.

Finding another agent works like finding an MCP server: the Exchange reference
goes in, the version's deployed gateway instance comes out. The reads behind it
are the same ones [MCP discovery](https://docs.donkey-kit.dev/registry/discovery/mcp.md#resolving-an-mcp-server)
documents. There is no filter-and-bind step for agents. You resolve one agent
by its reference and then call it.

## Resolving an A2A agent

`donkey.registry.resolve_agent()` is Live. It does
for an A2A agent asset what `resolve_mcp()` does for an MCP server, with the
same reads, and finds the version's deployed A2A instance:

```python
agent = await donkey.registry.resolve_agent("<group-id>/claims-triage-agent/1.0.0")
print(agent.endpoint_url)   # https://<gateway-host>/<instance-path>/
```

The agent's card is at `endpoint_url` + `.well-known/agent-card.json`. It raises
`RegistryError` in the same cases as `resolve_mcp()`, including a gateway URL
that is not `https://`, and like it sends no gateway credential. The immutable
handle is resolution-only. Use [`donkey.a2a.send()` or `stream()`](https://docs.donkey-kit.dev/a2a/call.md)
with the Exchange reference to call the agent; that provisional surface is
offline-verified, not yet live-verified for successful gateway send/SSE.
Framework-native `AgentHandle.as_tool()` remains planned.

## Related

- [Call other agents](https://docs.donkey-kit.dev/a2a/call.md) — `donkey.a2a.send()` and `stream()` resolve the reference for you.
- [Discovery for MCP tools](https://docs.donkey-kit.dev/registry/discovery/mcp.md) — `resolve_mcp()`, search and `explain()`.
- [A2A gateway policies](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md) — what the gateway enforces on the call.
