# Captured fixtures — Exchange asset search (#53)

Captured 2026-10-27 from the real sandbox business group (scrubbed to
`00000000-0000-4000-8000-4256cc8e6f68`) with the exact query
`ExchangeRegistry.search()` sends (`_SEARCH_QUERY` in
`src/donkey_kit/registry/exchange.py`): `POST /graph/api/v1/graphql`,
`assets(query: $query, latestVersionsOnly: true)`, the search MuleSoft documents
in [Searching the Graph API](https://docs.mulesoft.com/exchange/to-search-with-graph-api).
`$query` is a `SearchAsset` whose `organizationIds` is the business group in
every capture. The key of every tag Exchange derives is replaced with
`exchange-derived`.

## Files

Each file is the response body; every capture is a `200` but the last.

- `search.types-mcp.json` — `types: ["mcp"]`, `limit: 50`: the group's one MCP
  server.
- `search.term.json` — `searchTerm: "sku"`: Exchange matches words and word
  prefixes of the name, asset ID and description, ignoring case.
- `search.description-term.json` — `searchTerm: "keycloak", types: ["agent"],
  limit: 3`: a word only in the agents' descriptions.
- `search.page1.json`, `search.page2.json` — `types: ["mcp", "agent"]`,
  `limit: 5`, `offset: 0` then `5`: nine assets, so the second page is short.
- `search.api-types.json` — `types: ["rest-api", "http-api", "soap-api"]`: the
  group has none.
- `search.tags.json` — `tags: [{value: "crewai"}], types: ["agent"]`: the one
  agent with that tag. Its `tags` hold the keyless tags set at publish and the
  keyed ones Exchange derives.
- `search.tags-two.json` — `tags` `crewai` and `langgraph`: nothing, so Exchange
  requires every tag.
- `search.tags-derived.json` — `tags: [{value: "v0.3"}]`: every agent, because
  `v0.3` is a tag Exchange derives (it has a key) on each of them.
- `search.unknown-field.400.json` — a `labels` field in `$query`: HTTP `400`,
  `GRAPHQL_VALIDATION_FAILED`.

Probed but not captured: `searchTerm` `"ku-loo"` (inside words) matches nothing,
and `"*sku*"` matches the same as `"sku"`, so a glob is not server-side. Without
`latestVersionsOnly` an asset comes back once per version (790 rows for 371
assets across the root organisation). A `limit` of 1000 is accepted.
