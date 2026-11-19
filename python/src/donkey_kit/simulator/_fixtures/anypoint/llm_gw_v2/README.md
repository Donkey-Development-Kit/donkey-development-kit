# The llm-gw-v2 transcoding chain — LIVE capture (docs/verified-apis.md §2, #1409)

Captured **2026-11-14** from a short-lived probe proxy (API Manager instance
**`21300212`**, since torn down) on the DDK org's `shared-omni-gateway`. The proxy
ran the v2 outbound chain: `llm-v2-out-{anthropic,gemini}-to-openai-trans-policy`
1.0.1, `llm-v2-out-auth-apikey-policy` 1.0.0, `llm-proxy-core` 1.0.12 and
`model-based-routing` 1.0.10, with the routes `anthropic/claude-haiku-4-5-20251001`
and `gemini/gemini-3.8-flash`.

These are byte copies of files under `tests/fixtures/anypoint/v2_chain/` (that
README has the full findings), renamed by shape. Served by `donkey mock` on
`POST …/responses` (or `/chat/completions`) when the request's `model` is the
sentinel (#1412):

| Sentinel | Files | Source capture |
| --- | --- | --- |
| `donkey-sim/v2-anthropic` | `anthropic.responses.{headers.txt,body.json}` | `anthropic.responses.tool_long` |
| `donkey-sim/v2-anthropic-stream` | `anthropic.responses.stream.*` | `anthropic.responses.tool_long.stream` |
| `donkey-sim/v2-gemini` | `gemini.responses.{headers.txt,body.json}` | `gemini.responses.tool_long` |
| `donkey-sim/v2-gemini-stream` | `gemini.responses.stream.*` | `gemini.responses.tool_long.stream` |
| `donkey-sim/v2-gemini-cached` | `gemini.responses.cached.body.json` | `gemini.responses.cache_2` (body only) |
| `donkey-sim/v2-anthropic-chat-cached` | `anthropic.chat.cached.body.json` | `anthropic.chat.cache_2` (body only) |

The usage is the gateway's normalised OpenAI shape: `input_tokens` /
`prompt_tokens` include the cache read, `cached_tokens` is the cache read, there
is no cache-write count (so `donkey.last_call.cache_write_tokens` stays `None`), and
Gemini reports `reasoning_tokens`. The two cached bodies were captured without
headers, so they replay with no `x-llm-proxy-*` routing headers. The streams
end with a `response.completed` event carrying `usage`.
