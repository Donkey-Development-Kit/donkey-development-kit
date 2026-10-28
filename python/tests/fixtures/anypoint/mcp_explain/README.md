# Captured fixtures — governed-state reads behind `explain()` (#1298)

Captured 2026-10-28 from the real sandbox business group (scrubbed to
`00000000-0000-4000-8000-4256cc8e6f68`), environment `Sandbox` (scrubbed to
`00000000-0000-4000-8000-5c4fd1a49fc3`), with the reads
`ExchangeRegistry.explain()` makes in `src/donkey_kit/registry/exchange.py`.
The environment list is `../mcp_resolve/environments.json`.

The instances were throwaway `mcp` instances of the group's
`ddk-demo-sku-lookup` MCP server at `1.1.0`, on the shared managed gateway
`shared-omni-gateway` (scrubbed to `00000000-0000-4000-8000-d4b0d8dad526`), one
at a time, each deleted after its reads. The governance report is for a
throwaway asset, `ddk-verify1298-mcp` `0.1.0` (tag `ddk-verify1298`), under a
throwaway governance profile whose filter was `tag:ddk-verify1298,scope:mcp`,
with notifications off and MuleSoft's Agent Network Best Practices `2.1.0`
ruleset. The profile and the asset were deleted after the capture; the group
has no other profile.

Paths are relative to `/apimanager/api/v1/organizations/{org}/environments/{env}`
unless they start with `/`. Each file is the response body.

## Files

- `apis.by-asset.governed.json` — `GET …/apis?assetId=&groupId=&assetVersion=`:
  instance `ddk-verify1298-g`, deployed.
- `api.describe.governed.json` — `GET …/apis/{id}` for it:
  `deployment.targetName` is the gateway's name.
- `policies.governed.json` — `GET …/apis/{id}/policies` for it:
  `mcp-support` 1.1.1 and `client-id-enforcement` 1.3.3, by `template.assetId`.
- `apis.by-asset.undeployed.json` — instance `ddk-verify1298-u` before its
  deployment: `deployment: null`, `status: unregistered`.
- `apis.by-asset.ungoverned.json` — the same instance once deployed.
- `policies.none.json` — its policies: `{"policies": []}`, before and after the
  deployment.
- `apis.by-asset.none.json` — the read after both instances were deleted.
- `asset.version.json` — `GET /exchange/api/v2/assets/{group}/{asset}/{version}`
  for `ddk-demo-sku-lookup` `1.1.0`: `status: published`, no `labels`.
- `asset.version.tagged.json` — the same read for `ddk-verify1298-mcp` `0.1.0`,
  published with a `tags` field: `labels: ["ddk-verify1298"]`.
  Both are trimmed to the asset's identity and the fields `explain()` reads;
  the rest of each body names the publisher.
- `governance.reports.conformant.json` —
  `GET /governance/xapi/api/v1/stats/organization/targets/{group}/{asset}/{version}?organization={group}`
  for `ddk-verify1298-mcp`: one report, `summaryStatus: Conformant`, keyed by
  the minor version (`…::ddk-verify1298-mcp/v0.1`). It came about a minute
  after the publish.
- `governance.reports.root-org.json` — the same read without `organization`, at
  the same time: `reports: []`. It looks in the root organization.
- `governance.reports.none.json` — the read with `organization` for
  `ddk-demo-sku-lookup`, which no profile governs: `reports: []`. An asset that
  does not exist comes back the same way.

Probed but not captured: a profile created after the asset was published
never evaluated it (no report in 10 minutes), so the capture created the
profile first. A profile needs a `scope:` in its filter (`400 Scope filter
must be provided`). `GET /stats/profiles/{id}` and `…/targets` were `404` and
`POST /conformance/status` was `500` for the same asset. `NonConformant`,
`Pending` and `Failed` are the other `summaryStatus` values in the API
Governance Experience API spec (`api-governance-xapi` 0.1.2); they were not
observed.
