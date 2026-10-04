# Captured fixtures — real Anypoint sandbox via `anypoint-cli-v4`

Captured 2026-08-28 from the real sandbox org
`00000000-0000-4000-8000-40e9a964ded9`, environment **Sandbox**
(`00000000-0000-4000-8000-9be3001e93bf`), using `anypoint-cli-v4` 1.6.26. Unlike
the `a2d/` fixtures, these are the **direct Anypoint control-plane** data
contracts the SDK targets.

## Files

- `api_list.sandbox.json` — `api-mgr:api:list --environment Sandbox -o json`,
  first 3 of 10 instances. This is the **raw API Manager REST body** (camelCase
  fields) the CLI passes through: `groupId/assetId/assetVersion`, `endpointUri`,
  `technology` (`flexGateway`), `deployment`, `routing`, `status`, `deprecated`,
  `isPublic`, `tags`, `stage`, `semanticCacheConfigId`, etc. The MCP servers are
  governed API Manager instances behind the Agent Network ingress gateway.
- `api_describe.product-catalog-mcp.json` — `api-mgr:api:describe 21121315`,
  raw REST body for one MCP instance (adds `endpoint`, deployment target, proxy
  + implementation URIs).
- `policy_list.product-catalog-mcp.json` — `api-mgr:policy:list 21121315 -o json`.
  NOTE: this one is the CLI's **presentation shape** (keys `"ID"`, `"Template
  ID"`, `"Asset ID"`, `"Configuration"` as a rendered string), not the raw REST
  policy body. It still captures the governed-state contract: which policy
  assets (`mcp-support`, `client-id-enforcement`, `header-injection`) are
  Enabled on the instance.

## What these verify (see docs/verified-apis.md §5/§6/§7)

- Governed-state join: API Manager instances are listable per
  environment and carry deployment + policy state — no reverse-engineering
  needed. `test_governed_state_shapes.py` proves the domain models represent
  this real data.
- The direct-REST *fetch* endpoints behind the CLI are recorded in
  docs/verified-apis.md §12 (from the CLI-plugin analysis); nothing here wires
  `ExchangeRegistry` to a live call yet.
