# Captured fixtures — A2A instance create, policy and deploy (#254)

Captured 2026-10-27 from the real sandbox org (scrubbed to
`00000000-0000-4000-8000-…`), environment Sandbox, with a username/password
session. The same sequence was then repeated end to end with a connected
app's `client_credentials` token, for a second throwaway asset; its scopes are
in `docs/verified-apis.md` §1, and these files are from the first run. A throwaway
`agent` asset, `ddk-verify254-agent:0.1.0`, was published to Exchange, given an
A2A instance on a managed shared-space Flex target (listener port 8081),
deployed, then deleted: the instance with `DELETE …/apis/{id}` (`204`), the
asset with an Exchange hard delete. Numeric ids are the real, now-deleted
ones. The Credential Injection key was a dummy and is `<redacted>` here.

Paths are relative to `/apimanager/api/v1/organizations/{org}/environments/{env}`
unless they start with `/`.

## Files

- `create.request.json` / `create.201.json` — `POST …/apis`. Body:
  `spec {groupId, assetId, version}`, `endpoint {type: "a2a", uri, proxyUri,
  isCloudHub: null, deploymentType: "HY"}`, `instanceLabel`, `technology:
  "flexGateway"`. The response echoes the endpoint with `validation:
  "NOT_APPLICABLE"`, adds `metadata.assetType: "agent"` (from the asset) and
  an `autodiscoveryInstanceName`. It is not yet deployed: no `deployment`.
- `policy-inbound.request.json` / `policy-inbound.400.json` — `POST
  …/apis/{id}/policies` with `credential-injection-api-key` 1.0.1: `400
  PolicyValidationError`, "This policy can not be applied as inbound". The
  policy is outbound only.
- `upstreams.200.json` — `GET …/apis/{id}/upstreams`: the one upstream the
  create made from `endpoint.uri`.
- `outbound-policy.request.json` / `outbound-policy.201.json` — `POST
  /apimanager/xapi/v1/organizations/{org}/environments/{env}/apis/{id}/policies/outbound-policies`
  with `{configurationData, groupId, assetId, assetVersion, upstreamIds,
  apiVersionId}` (the body `anypoint-cli-agent-fabric-plugin` 1.0.4 sends in
  `applyOutboundPolicies`). The response is a list, one policy per upstream.
- `outbound-policies.get.405.json` — `GET` on the same path: `405`, POST only.
- `policies.200.json` — `GET …/apis/{id}/policies` after the outbound POST:
  the outbound policy is listed here too, with `type: "system"` and
  `upstreamId`, and its `configuration` omits `apiKey`.
- `deployment.request.json` / `deployment.201.json` — `POST
  /proxies/xapi/v1/organizations/{org}/environments/{env}/apis/{id}/deployments`
  with `{type: "HY", gatewayVersion, targetId, targetName, environmentId,
  environmentName}`. Sent `gatewayVersion: "1.0.0"`; the response carries the
  target's real one (`1.14.2`), `expectedStatus: "deployed"` and the
  deployment `id`.
- `deployment-status.applying.json`, `deployment-status.applied.json` — `GET
  /proxies/xapi/v1/…/apis/{id}/deployments/{deploymentId}/status`, polled:
  `applying`/`unregistered`, then `applied`/`active` about 10 seconds later.
- `api_describe.200.json` — `GET …/apis/{id}` once deployed.
- `contracts.200.json` — `GET …/apis/{id}/contracts` before the delete: none.
