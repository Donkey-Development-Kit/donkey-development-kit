# V1 Agent Card Rewrite

`v1-card-rewrite.selected.json` projects selected fields from two real HTTP GET
responses captured 2026-11-05. It is not a full card or simulator replay body.
Only status, content type and `supportedInterfaces` are retained, alongside the
configured `consumerUrl`. Upstream and gateway URLs are consistently replaced
with `agent.example.com` and `gateway.example.com`; bindings, versions, array
order and trailing-slash relationships preserve their observed values. Other
fields and headers are omitted, including security metadata and reporting URLs.

The capture used the DDK Sandbox on shared gateway 1.14.4, isolated `a2a_v1`
instance `21278723`, asset `ddk-capture1393v1-card-bdc9161c` 1.0.0. Tenant IDs
remain in private capture storage, not this fixture tree. Policy `9756361` used
definition `a-two-a-v1-agent-card` **1.0.1** and implementation
`a-two-a-v1-agent-card-flex` **1.0.2**, applied by the maintainer in the UI after
target assignment. Earlier definition **1.0.0** attachments returned HTTP 400
`PolicyValidationError`; target assignment alone is not established as their cause.

Default configuration initially rewrote URLs to `https://localhost/<base-path>/`.
The final capture followed an approved explicit public-gateway `consumerUrl`
edit, preserving `cardPath: /.well-known/agent-card.json`, `interfacePaths: {}`.
After 90 seconds, upstream and gateway `/.well-known/agent-card.json` GETs
returned 200 `application/json`. Both JSONRPC entries used the configured URL,
preserving versions `1.0` and `0.3.0` and all non-URL interface fields. No
`A2A-Version` request header, agent POST or automatic retry was used.

Other top-level card fields differed: `preferredTransport`, `protocolVersion`,
`security`, `securityRequirements`, `securitySchemes`, `skills`. No whole-card
preservation, other binding, extended-card, SSE, call-metric or delegation claim.
The shared LangGraph instance/policies/upstreams baseline compared equal.
Raw evidence remains in ignored provisioning storage. The isolated instance
and asset are intentionally **retained for inspection**, not cleaned up.
#1393 stays open. Evidence:
https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/1393#issuecomment-6670434829.
Official reference:
https://docs.mulesoft.com/gateway/latest/policies-included-a2a-v1-agent-card.
