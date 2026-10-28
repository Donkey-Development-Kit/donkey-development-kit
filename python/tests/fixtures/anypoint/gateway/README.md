# Captured fixtures — a managed Flex Gateway, by name (#226)

Captured 2026-10-28 from the real sandbox org (scrubbed to
`00000000-0000-4000-8000-…`), environment Sandbox, with a username/password
session. Read only: nothing was created. `donkey expose` reads these to find
the gateway it deploys to; the gateway itself is the platform's (ADR 0015
rule 5).

Paths are relative to
`/gatewaymanager/api/v1/organizations/{org}/environments/{env}`.

## Files

- `gateways.by-name.json` — `GET …/gateways?name=shared-omni-gateway`:
  `{content: [{id, name, targetId, status, …}], pageSize, pageNumber,
  totalElements}`. The `name` filter is a case-sensitive substring match
  (`name=shared` answers this same row), so a caller matches the exact name
  itself.
- `gateway.200.json` — `GET …/gateways/{id}`: `status: "APPLIED"`,
  `desiredStatus: "STARTED"`, `runtimeVersion`, `portConfiguration.ingress.port`
  (8081, the listener port an instance's `proxyUri` uses; egress is 8082) and
  `configuration.ingress.publicUrl`. Its `id` is the `targetId` a deployment
  names (`../mcp_instance/deployment.201.json`).
