# Native Gemini ingress on model-based-routing 1.0.5 — LIVE capture (docs/verified-apis.md §2, #1404)

Captured **2026-11-03** from a `Format=Gemini` proxy on `model-based-routing`
def 1.0.5 / impl 1.0.9 in the DDK sandbox (API Manager instance **`21271488`**,
shared-omni-gateway). It has two routes (`gemini`, `openai`) with Route A as the
fallback. Consumer auth is the `client_id`/`client_secret` pair of the shared
test client.

These are byte copies of the matching files under
`tests/fixtures/anypoint/gemini_inbound_multiroute/`. Served by `donkey mock` on
`POST …/models/{model}:generateContent` and `:streamGenerateContent` (#1562):

- `generate_content.success.*`: the happy path, served by `gemini` with
  fallback `false`. Live, every path model was served this way, so the
  simulator serves it for any model.
- `stream_generate_content.success.*`: the complete SSE stream; each event
  carries cumulative `usageMetadata`.
- `generate_content.invalid_json.*`: a body that is not JSON. A flat-string 400
  with no routing headers.

The cross-vendor and unresolvable captures stay in the test tree only.
