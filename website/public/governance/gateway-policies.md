# Gateway policies

Status: Offline-verified. Shipped and verified offline; no live gateway round-trip yet.

Policies run at the gateway, not in your agent. DDK does not evaluate them. It
makes the outcome visible in code: a typed exception, a field on the response, a
span attribute. Each surface has its own page, because the policies, the
captured refusals and what your code sees differ.

  **Three things are called "policy" in these docs.** *Gateway policies* are
  what the gateway enforces on each surface, covered by the pages in this
  section. *[Custom policies](https://docs.donkey-kit.dev/custom-policies.md)* teach `classify()` the refusal
  shape your own gateway policy returns. The *[policy handshake](https://docs.donkey-kit.dev/policies.md)* is a
  planned, advisory read of the policies in force; the gateway always wins.

## One page per surface

- [Models](https://docs.donkey-kit.dev/governance/gateway-policies/models.md): policies on an LLM proxy
  instance, the typed exception or field each gives your code, and how to
  rehearse it offline.
- [MCP tools](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md): policies on an MCP server
  instance, including what they do to the tool catalog.
- [A2A agents](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md): policies on an A2A agent
  instance, per protocol version.

## Policy families across surfaces

This matrix lines up the policy families the gateway enforces on each surface. A
link goes to the section that documents that policy on the surface's page; **—**
means that surface's policies page has no section for the family. The status
badges live on the linked pages, so this table makes no status claims of its own.

| Policy family | Models | MCP tools | A2A agents |
|---|---|---|---|
| Who may call | [Client ID enforcement](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#client-id-enforcement) | [Global access](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md#mcp-global-access), [attribute-based access](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md#mcp-attribute-based-access-control) | — |
| Token and request limits | [Token rate limiting](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#token-rate-limiting), [request rate limiting](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#request-rate-limiting) | — | [Token rate limit](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md#captured-outcomes) |
| Personal data | [PII detection](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#pii-detection) | [MCP PII detector](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md#mcp-pii-detector) | [PII detector](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md#captured-outcomes) |
| Prompt injection and content safety | [Injection protection](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#injection-protection-and-regex-prompt-guard), [content safety](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#content-safety-and-guardrails) | — | [Azure content guardrails](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md#captured-outcomes) |
| Schema and tool poisoning | — | [Schema validation and tool poisoning](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md#mcp-schema-validation-and-tool-poisoning) | [Schema validation](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md#captured-outcomes) |
| Approval and step-up | [OAuth2 OBO and CIBA](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#oauth2-obo-step-up-ciba) | [Approval on `invoke_tool`](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md#mcp-tools-progressive-disclosure) | [Challenges and replay](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md#challenges-and-replay) |
| Kill switch | [Agent kill switch](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#agent-kill-switch) | — | — |
| Routing and caching | [Model-based routing](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#model-based-routing-and-fallback), [semantic caching](https://docs.donkey-kit.dev/governance/gateway-policies/models.md#semantic-caching) | — | — |
| Tool discovery and payload size | — | [Progressive disclosure](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md#mcp-tools-progressive-disclosure), [payload optimization](https://docs.donkey-kit.dev/governance/gateway-policies/mcp.md#mcp-payload-optimization) | — |
| Protocol version | — | — | [Protocol versions](https://docs.donkey-kit.dev/governance/gateway-policies/a2a.md#protocol-versions) |

## Related

- [Typed refusals](https://docs.donkey-kit.dev/errors.md): the exception each refusal becomes.
- [Refusal handlers](https://docs.donkey-kit.dev/refusal-handlers.md): react to a refusal once, for every call.
- [Human-in-the-loop](https://docs.donkey-kit.dev/hitl.md): the approval and step-up policies from your code.
