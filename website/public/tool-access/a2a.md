# A2A agent tools

Status: Roadmap. Planned design; not shipped yet.

This capability is on the [Roadmap](https://docs.donkey-kit.dev/roadmap.md); the API shown here is the planned design.

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

There is nothing to install for `as_tool()` yet. The `donkey-kit[a2a]` extra
carrying `a2a-sdk` exists today for the server side,
[`donkey.serve()`](https://docs.donkey-kit.dev/a2a.md); `as_tool()` will use the same extra when it ships.

## Where this fits

- `AgentHandle` comes from `ExchangeRegistry.resolve_agent()`, which is
  available today: it resolves the agent's gateway URL the way `resolve_mcp()`
  resolves an MCP server's. Only `as_tool()` is on the Roadmap. See
  [Resolving an A2A agent](https://docs.donkey-kit.dev/tool-access/discovery.md#resolving-an-a2a-agent).
- The resolved URL follows the
  [`https://` rule](https://docs.donkey-kit.dev/reference/configuration.md#endpoints-must-use-https) of the
  URLs you configure, because the gateway credential is sent to it. A gateway
  public URL that uses plain `http://` to a non-loopback host (without
  `DONKEY_ALLOW_HTTP=1`), or that carries a user name or password, raises
  `RegistryError`, and nothing is sent to the agent.
- `as_tool()` returns a framework-native callable, following the same
  conventions as [Framework binding](https://docs.donkey-kit.dev/tool-access/binding.md).
- To make *your* agent callable by others over A2A, see [A2A agents](https://docs.donkey-kit.dev/a2a.md).

## Gateway Refusals

Gateway policies can reject a remote A2A call before it reaches the agent.
For A2A v0.3, PII detection returns HTTP 403 with a JSON-RPC error whose
`data` string can contain the detected value. Schema validation returns HTTP
400 with a JSON-RPC error, and token rate limiting can return HTTP 429 with
an empty body and `x-token-*` headers. Do not log raw PII refusal bodies.

For A2A v1, PII detection returns HTTP 403 with a JSON-RPC `ErrorInfo`
detail (`FORBIDDEN`, domain `a2a-protocol.org`). Both the error message and
its detail can contain the detected value. Schema validation uses the same
detail envelope: bad parameters and malformed JSON return HTTP 400, while
an unknown method returns HTTP 404. Token rate limiting can return a bare
HTTP 429 with the same `x-token-*` headers. A rejected streaming request can
return an ordinary HTTP error before any SSE stream starts.

Azure Content Guardrails can return HTTP 403 with `FORBIDDEN` and a
`prompt_shield` detail. Fail-closed Azure and Bedrock guardrail service failures
return HTTP 503 with `UNAVAILABLE`. The `x-flex-azure-guardrails-*` or
`x-flex-bedrock-guardrails-*` headers identify the action, phase and reason.
A `service_unavailable` refusal is not a content-moderation verdict. These
captures cover request-phase refusals, not response moderation or successful SSE.

## Authentication Challenges

The v0.3 In-Task Authorization Code policy can return HTTP 200 with a
JSON-RPC **result**, not an error: a task whose status is `auth-required`.
Its status message contains an `authChallenge` data part with OAuth metadata,
including authorization/token endpoints, scopes, audience and redirect URI.
A `message/stream` challenge can also be ordinary JSON rather than SSE.
HTTP success alone therefore does not mean the delegated task completed.

The captured missing-token responses generated new task and context IDs,
despite IDs supplied in the request params, and returned the numeric JSON-RPC
request ID as a string. These observations do not verify token forwarding,
caching, authentication failures or a completed OAuth flow.

## Quality Evaluation

Quality Evaluation can annotate a successful agent response with
`x-quality-evaluation`, `x-quality-score`, `x-quality-explanation` and
`x-quality-status` headers. A captured v0.3 `message/send` reply returned
HTTP 200 with `x-quality-evaluation: yes`, `x-quality-score: 3` and
`x-quality-status: completed`; its explanation described how the agent fulfilled
the request. That single response does not establish a score range, evaluator
failure behavior, v1 evaluation or streaming support. DDK does not yet record
these annotations on its telemetry spans.

These gateway response shapes do not make outbound A2A calls or their typed
refusals available in DDK yet. `as_tool()` remains on the Roadmap, and
[`donkey dev`](https://docs.donkey-kit.dev/a2a.md) does not yet replay these A2A-specific policy responses.
