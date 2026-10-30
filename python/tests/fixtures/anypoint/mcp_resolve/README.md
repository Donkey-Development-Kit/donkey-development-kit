# Captured fixtures — MCP endpoint resolution (#1297)

Captured 2026-10-28 from the real sandbox business group (scrubbed to
`00000000-0000-4000-8000-4256cc8e6f68`), environment `Sandbox` (scrubbed to
`00000000-0000-4000-8000-5c4fd1a49fc3`), with the reads
`ExchangeRegistry.resolve_mcp()` makes in
`src/donkey_kit/registry/exchange.py`. The asset is the group's
`ddk-demo-sku-lookup` MCP server at `1.1.0`. Its instances were throwaway ones,
created on the shared managed gateway (scrubbed to
`00000000-0000-4000-8000-d4b0d8dad526`, host
`shared-omni-gateway.example.invalid`) and deleted after the capture.

## Files

Each control-plane file is the response body; each `traffic.*` file holds the status, headers and body.

- `environments.json` — `GET /accounts/api/organizations/{org}/environments`:
  the group's `Design` and `Sandbox` environments. Each environment's
  `clientId` is scrubbed to a 32-hex placeholder starting
  `00000000000040008000` (#1443).
- `apis.by-asset.json` — `GET /apimanager/api/v1/organizations/{org}/environments/{env}/apis`
  with `assetId`, `groupId` and `assetVersion`: one deployed MCP instance,
  label `ddk-verify1297-b`. A row has `endpointType`, `instanceLabel` and
  `deployment.expectedStatus`, but no `endpoint`; `endpointUri` is `null`.
- `apis.by-asset.undeployed-and-deployed.json` — the same read with two
  instances of the version: one never deployed (`deployment: null`, `status:
  unregistered`) and one deployed.
- `apis.by-asset.none.json` — the same read after both instances were deleted.
- `api.describe.json` — `GET …/apis/{id}` for the deployed instance:
  `endpoint.proxyUri` (`http://0.0.0.0:8081/ddk-verify1297-b`) and
  `deployment.targetId`.
- `gateway.json` — `GET /gatewaymanager/api/v1/organizations/{org}/environments/{env}/gateways/{id}`:
  the gateway's `configuration.ingress.publicUrl`.
- `traffic.slash.401.json` — an MCP `initialize` with no credentials to
  `<publicUrl>/ddk-verify1297-b/`: HTTP `401`, `Client ID is not present`, with
  `WWW-Authenticate: Client-ID-Enforcement`. The instance is routed.
- `traffic.no-slash.404.json` — the same call without the trailing slash: an
  empty `404`, every time.
- `traffic.unknown-path.404.json` — the same call to a path no instance has: the
  same empty `404`.

Probed but not captured: a `deployment` sent inline when creating the instance
is ignored, so the instances were deployed with the separate deployments call.
