# Captured fixtures — Exchange publish and read-back (#218)

Captured 2026-10-26 from the real sandbox org (scrubbed to
`00000000-0000-4000-8000-306f0858eed3`) by publishing a throwaway `mcp` asset,
`ddk-verify-218-fixture:1.0.0`, which was hard-deleted right after. Every
pre-signed `externalLink` is replaced with
`https://exchange-files.example.invalid/presigned`: the real ones carry AWS
signing parameters.

## Files

- `publish.201.json` — `POST /exchange/api/v2/organizations/{org}/assets/{g}/{a}/{v}`,
  multipart, `x-sync-publication: true`, with `properties.ddkDigest` and a
  `files.mcp-metadata.json` file field.
- `publish.409.json` — the same POST again: `ASSET_PRE_CONDITIONS_FAILED`. The
  published version is left as it was.
- `graphql.latest.json` — `POST /graph/api/v2/graphql`, `asset(groupId, assetId)`
  with no version: the latest version, its name and description, its files
  and its other versions (recaptured for #1281, see below). The generated POM
  is the file with `packaging` `pom` and a null classifier.
- `pom.xml` — that POM, downloaded from its `externalLink` without
  credentials. `properties.ddkDigest` is stored here, as `<ddkDigest>`.
- `instance.201.json` — `POST /exchange/api/v2/assets/{g}/{a}/versionGroups/{vg}/instances/external`.
- `graphql.version.json` — the GraphQL `asset` query for `1.0.0` after the
  instance was added.
- `graphql.absent.json` — the query for an asset the org does not have: a
  `200` whose error carries `extensions.status` `404`.

## The asset page (#1269)

Captured 2026-10-27 with the demos repo's connected app (a `client_credentials`
token whose app holds Exchange roles on the business group) against the published
`ddk-demo-sku-lookup:1.0.0`, renamed `ddk-verify-1269-fixture` here, with every
ID scrubbed. The calls are the ones `anypoint-cli-v4 exchange asset page upload`
makes (`anypoint-cli-exchange-plugin` 1.0.12, `assetPageUpdate`).

- `portal-draft.200.json` — `GET /exchange/api/v2/assets/{g}/{a}/{v}/portal/draft`,
  which opens the portal draft.
- `page.201.json` — `POST …/portal/draft/pages` with `{"pagePath": "home"}`
  when the version has no page content yet.
- `page.409.json` — the same POST when the page exists.

`PUT …/portal/draft/pages/home` with `Content-Type: text/markdown` and
`PATCH /exchange/api/v1/assets/{g}/{a}/{v}` (publishes the draft) answer `204`
with no body. `GET …/portal/pages/home` then returns the page rendered as HTML.

## The asset's name and description (#1281)

Captured 2026-10-27 with the demos repo's connected app by publishing a
throwaway `mcp` asset, `ddk-verify-1281-fixture:1.0.0`, named
"DDK verify 1281 fixture", which was hard-deleted right after.

- `graphql.latest.json` — the publisher's latest-version query, right after
  the publish.
- `graphql.renamed.json` — the same query after
  `PATCH /exchange/api/v2/assets/{g}/{a}` with a new `name` and
  `description`, which answered `204` with no body.

Exchange takes an asset's name and description from its first version only:
a later version sent with others shows the asset's existing ones. The PATCH
above changes them for every version (checked on throwaway assets with two
versions).

## Agent assets (#1260)

Captured 2026-10-27 by running `publish_plan` itself, with a user-session
bearer, on a one-agent repo declaring `protocol = "a2a_v1"` and an endpoint in
`[donkey.agents.ddk-verify-1260-fixture]`. The throwaway asset,
`ddk-verify-1260-fixture:1.0.0`, was hard-deleted right after with
`DELETE /exchange/api/v2/assets/{g}/{a}/{v}` and `x-delete-type: hard-delete`
(`204`). The digest is scrubbed to `sha256:5…5`, as in `pom.xml`.

- `agent.publish.201.json`: the multipart POST with `type` `agent`,
  `properties.{ddkDigest,protocol,platform}` and the `files.agent-metadata.json`
  and `files.a2a-v1-card.json` file fields.
- `agent.graphql.latest.json`: the publisher's latest-version query on the
  rerun. Exchange keeps both files under their classifiers, as `json`, `zip`,
  `fat-*` and `original-*`.
- `agent.pom.xml`: its POM. `<type>`, `<ddkDigest>`, `<protocol>` and
  `<platform>` are all kept.
- `agent.agent-metadata.stored.json` and `agent.a2a-v1-card.stored.json`:
  both files as downloaded back, byte-for-byte what was sent (key order
  normalised here).

The rerun answered `unchanged` after reading the POM back. A changed endpoint
under the same version was refused after the same two reads, with nothing
sent. The external-instance POST, the page calls and the `409` behaved as they
do for `mcp`.

## What these verify (docs/verified-apis.md §7, §12.3)

`tests/unit/test_exchange_publish.py` replays them through a stateful fake
Exchange to drive `donkey_kit.registry.exchange_publish`.
