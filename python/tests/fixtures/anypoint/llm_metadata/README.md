# Captured fixtures — an `llm` asset's `llm-metadata` file (#1411)

Captured 2026-11-04 from the real sandbox org (the DDK business group) with the
provisioning repo's Anypoint login. Every pre-signed `externalLink` is replaced
with `https://exchange-files.example.invalid/presigned`: the real ones carry AWS
signing parameters.

## Files

- `custom.llm-metadata.stored.json` — the `llm-metadata` file of
  `ddk-custom-llm-provider:1.0.0`, a custom LLM provider asset published for
  this capture (`POST /exchange/api/v2/organizations/{org}/assets/{g}/{a}/{v}`,
  multipart, `type=llm`, `x-sync-publication: true`, one
  `files.llm-metadata.json` field). It sets every field of the agent-fabric
  `llm_metadata.json` schema (draft-07) and was validated against it before
  publishing. Exchange returns it byte for byte as sent. No proxy routes to the
  asset: routing to a custom provider needs the `llm-gw-v2` chain (#1595). The
  `policyRef` names a real v2 adaptor, `mulesoft:llm-v2-out-azure-openai-adapt-policy:1.0.0`.
- `custom.graphql.files.json` — `POST /graph/api/v2/graphql`,
  `asset(groupId, assetId, version) { type version files { classifier packaging externalLink } }`
  for that version. Exchange derives five files from the one it was sent:
  `llm-metadata` as `json` and `zip`, `fat-llm-metadata` (`zip`),
  `original-llm-metadata` (`json`) and the generated POM.
- `proxy.llm-metadata.stored.json` and `proxy.graphql.files.json` — the same
  reads for `ddk-openai-model-routing`, the latest version of one of the DDK
  LLM proxies, published by `-provisioning`'s `ddkprov`. Its file holds only
  `platform`, which is all provisioning sends.
