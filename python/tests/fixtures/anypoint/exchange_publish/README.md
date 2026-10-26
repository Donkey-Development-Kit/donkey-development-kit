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
  with no version: the latest version, its version group, its files and
  instances. The generated POM is the file with `packaging` `pom` and a null
  classifier.
- `pom.xml` — that POM, downloaded from its `externalLink` without
  credentials. `properties.ddkDigest` is stored here, as `<ddkDigest>`.
- `instance.201.json` — `POST /exchange/api/v2/assets/{g}/{a}/versionGroups/{vg}/instances/external`.
- `graphql.version.json` — the GraphQL `asset` query for `1.0.0` after the
  instance was added.
- `graphql.absent.json` — the query for an asset the org does not have: a
  `200` whose error carries `extensions.status` `404`.

## What these verify (docs/verified-apis.md §7, §12.3)

`tests/unit/test_exchange_publish.py` replays them through a stateful fake
Exchange to drive `donkey_kit.registry.exchange_publish`.
