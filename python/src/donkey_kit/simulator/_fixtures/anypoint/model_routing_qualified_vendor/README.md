# Qualified-vendor model-based routing — LIVE capture (docs/verified-apis.md §2, #1405)

Captured **2026-11-03** from a `Format=OpenAI` model-based proxy on
`model-based-routing` def 1.0.5 / impl 1.0.9 in the DDK sandbox (API Manager
instance **`21271481`**, shared-omni-gateway). Its vendors are configured in
`groupId:vendor` form (`mulesoft:openai`, `mulesoft:gemini`) with Route A
(`mulesoft:openai`/`gpt-5-mini`) as the fallback. Consumer auth is the
`client_id`/`client_secret` pair of the shared test client.

These are byte copies of the bodies under
`tests/fixtures/anypoint/model_routing_qualified_vendor/`; the Cloudflare
`set-cookie` line was stripped from the header dumps as noise, as in the
sibling captures. Served by `donkey mock` on `POST …/chat/completions` (or
`/responses`) when the request's `model` is the sentinel (#1412):

- `donkey-sim/success-qualified-vendor` → `chat_completions.success.*`:
  `openai/gpt-5-mini` matched, `x-llm-proxy-llm-provider: mulesoft:openai`,
  `x-llm-proxy-routing-fallback: false`.
- `donkey-sim/fallback-qualified-vendor` → `chat_completions.fallback.*`:
  an unconfigured `mistral/` prefix, served by the policy fallback,
  `x-llm-proxy-routing-fallback: true`, provider `mulesoft:openai`.

`donkey.last_call.served_provider` keeps the qualified value and the span's
`gen_ai.system` records the bare vendor (`openai`).
