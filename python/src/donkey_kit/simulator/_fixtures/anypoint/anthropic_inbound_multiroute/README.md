# Native Anthropic ingress on model-based-routing 1.0.5 — LIVE capture (docs/verified-apis.md §2, #1404)

Captured **2026-11-03** from a `Format=Anthropic` proxy on `model-based-routing`
def 1.0.5 / impl 1.0.9 in the DDK sandbox (API Manager instance **`21271486`**,
shared-omni-gateway). It has two routes (`anthropic`, `bedrockanthropic`) with
Route A as the fallback. Consumer auth is the `client_id`/`client_secret` pair of
the shared test client.

These are byte copies of the matching files under
`tests/fixtures/anypoint/anthropic_inbound_multiroute/`. Served by `donkey mock`
on `POST …/v1/messages` (#1562):

- `messages.success.*`: the happy path, `claude-haiku-4-5-20251001` on the
  `anthropic` route, fallback `false`.
- `messages.stream.*`: `"stream": true`, the complete SSE stream; usage comes
  from `message_start` and `message_delta`.
- `messages.fallback.*`: the `donkey-sim/anthropic-fallback` sentinel. An
  unconfigured `openai/` prefix, served by the policy fallback
  (`x-llm-proxy-routing-fallback: true`).
- `messages.invalid_json.*`: a body that is not JSON. A flat-string 400 with no
  routing headers.

The Bedrock 404, the `anthropic/`-prefixed and the unresolvable captures stay in
the test tree only.
