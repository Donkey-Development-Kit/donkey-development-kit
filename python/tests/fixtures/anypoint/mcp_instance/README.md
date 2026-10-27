# Captured fixtures — MCP instance create, policies and deploy (#1278)

Captured 2026-10-27 from the real sandbox org (scrubbed to
`00000000-0000-4000-8000-…`), environment Sandbox, with a username/password
session. A throwaway `mcp` asset, `ddk-verify1278-mcp:0.1.0`, was published to
Exchange (the #218 publish path, see `../exchange_publish/`), given an MCP
instance on a managed shared-space Flex target (listener port 8081), two
inbound policies, deployed, then deleted: the instance with `DELETE
…/apis/{id}` (`204`), the asset with an Exchange hard delete (`204`). Numeric
ids are the real, now-deleted ones.

Paths are relative to `/apimanager/api/v1/organizations/{org}/environments/{env}`
unless they start with `/`.

## Files

- `create.request.json` / `create.201.json` — `POST …/apis`. Body:
  `spec {groupId, assetId, version}`, `endpoint {type: "mcp", uri, proxyUri,
  isCloudHub: null, deploymentType: "HY"}`, `instanceLabel`, `technology:
  "flexGateway"`: the A2A body of `../a2a_instance/` with `type: "mcp"`. The
  response echoes the endpoint with `validation: "NOT_APPLICABLE"` and an
  `autodiscoveryInstanceName`. Unlike the A2A instance, `metadata` is `null`
  (no `assetType`). It is not yet deployed: no `deployment`.
- `apis-list.by-label.json` — the rows of `GET …/apis?limit=500`
  (`assets[].apis[]`) whose `instanceLabel` is the new one, read right after
  the create: one row, the new instance. This is how a create is made
  idempotent.
- `create.409.json` — the same `POST …/apis` again: `409` `ConflictError`, "This
  API (instance label for that product version in that environment) already
  exists in your organization." No second instance is made.
- `policy-mcp-support.request.json` / `policy-mcp-support.201.json` — `POST
  …/apis/{id}/policies` with `mcp-support` 1.1.1 (`injectMcpNameHeaders:
  false`), the body `provision_proxy.py` sends for LLM proxies.
- `policy-client-id-enforcement.request.json` /
  `policy-client-id-enforcement.201.json` — the same call with
  `client-id-enforcement` 1.3.3, reading `client_id` / `client_secret`
  headers. Both policies and their configuration are the ones the existing
  `product-catalog-mcp` instance has (`../policy_list.product-catalog-mcp.json`).
- `policies.200.json` — `GET …/apis/{id}/policies`: both, `type: "system"`,
  `order` 1 and 2 in the order they were posted.
- `deployment.request.json` / `deployment.201.json` — `POST
  /proxies/xapi/v1/organizations/{org}/environments/{env}/apis/{id}/deployments`,
  the A2A body. Sent `gatewayVersion: "1.0.0"`; the response carries the
  target's (`1.14.2`).
- `deployment-status.applying.json`, `deployment-status.applied.json` — `GET
  /proxies/xapi/v1/…/apis/{id}/deployments/{deploymentId}/status`, polled every
  5 s: `applying`/`unregistered`, then `applied`/`active` about 40 seconds
  later.
- `api_describe.200.json` — `GET …/apis/{id}` once deployed.
- `contracts.200.json` — `GET …/apis/{id}/contracts` before the delete: none.

Not captured: traffic through the deployed instance (its upstream was not
reachable, and no call was made through the gateway), and the same run with a
connected app's token (#23).
