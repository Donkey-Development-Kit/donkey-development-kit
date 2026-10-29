# Captured fixtures — a published A2A agent's card, published and live (#1354)

Captured 2026-10-29 from the real sandbox business group (scrubbed to
`00000000-0000-4000-8000-4256cc8e6f68`), environment `Sandbox` (scrubbed to
`00000000-0000-4000-8000-5c4fd1a49fc3`), with the reads
`ExchangeRegistry.published_card()` and `resolve_agent()` make in
`src/donkey_kit/registry/`, and the card read `Discovery.agent_card()` makes in
`src/donkey_kit/tools/discovery.py`. The asset is the group's
`ddk-langgraph-agent` A2A agent at `0.1.0`, deployed on the shared managed
gateway (scrubbed to `00000000-0000-4000-8000-d4b0d8dad526`, host
`shared-omni-gateway.example.invalid`). The live-card traffic went through a
throwaway instance of the same version, `ddk-verify1354-agent`, whose upstream
was the hosted LangGraph agent (scrubbed to `agents.example.invalid`). It was
deleted after the capture.

## Files

Each control-plane file is the response body; each `traffic.*` file holds the
status, headers and body.

- `graphql.files.json` — the Exchange Graph `asset(groupId, assetId, version)`
  files read: an `a2a-card` and an `agent-metadata` file, each as `json` and
  `zip`, plus their `fat-`/`original-` variants. The pre-signed
  `externalLink`s are replaced by `https://exchange-files.example.invalid/presigned`;
  the real ones downloaded with no credential. This version was published
  before #1212, so its card is the legacy A2A 0.3 `a2a-card`; the SDK now
  publishes `a2a-v1-card`, and `published_card()` reads either.
- `a2a-card.stored.json` — the `a2a-card` `json` file's content, as stored.
- `agent-metadata.stored.json` — the `agent-metadata` `json` file's content.
- `apis.by-asset.json` — `GET /apimanager/api/v1/organizations/{org}/environments/{env}/apis`
  with `assetId`, `groupId` and `assetVersion`: one instance,
  `endpointType: "a2a"`, `deployment.expectedStatus: "deployed"`.
- `api.describe.json` — `GET …/apis/{id}` for that instance:
  `endpoint.proxyUri` (`http://0.0.0.0:8081/ddk-langgraph-agent`) and
  `deployment.targetId`. With Gateway Manager's `publicUrl` (the
  `mcp_resolve/gateway.json` read) these are the reads `resolve_mcp()` makes.
- `traffic.card.no-policy.200.json` — `GET <publicUrl>/<label>/.well-known/agent-card.json`
  with no policy applied: `200`, and the body is the upstream's card byte for
  byte. The gateway rewrites nothing in it, `url` included.
- `traffic.card.no-client-id.401.json` — the same read once Client ID
  Enforcement 1.3.3 was applied, with no credentials: `401`, `Client ID is not
  present`, `WWW-Authenticate: Client-ID-Enforcement`.
- `traffic.card.uncontracted.401.json` — the same read with a client id that
  has no contract with the instance: `401`, `Authentication Attempt Failed`.
- `traffic.card.contracted.200.json` — the same read with a contracted
  application's `client_id`/`client_secret` headers: `200`, the upstream card.
- `upstream.agent-card.json` — the upstream's own card, read directly.

The live card is not the published one, and most of the difference is
expected. Its `url`, `supportedInterfaces`, `version` and `protocolVersion`
say where and how this deployment serves it (`version` is the agent's code,
`0.2.0`, against the asset's `0.1.0`), and it carries the security fields the
upstream declares. `card_drift()` ignores those (docs/verified-apis.md §7).
What remains is real drift: the `chat` skill's description was lengthened
after `0.1.0` was published.
