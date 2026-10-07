# OpenAI public shape — NOT a gateway capture (#895)

The files here are written to OpenAI's **published** Chat Completions streaming
format. They were **not** captured from an Agent Fabric LLM proxy. They exist so
the local gateway simulator can answer `POST …/chat/completions` with
`"stream": true` before a streamed OpenAI-route response is captured.

Why there is no capture to use:

- docs/verified-apis.md §2 (Chat Completions row) is verified for
  **non-streaming** calls only. The simulator serves that 200 from the real
  capture in `../anypoint/azure_openai_routing/`.
- An OpenAI route was seen to stream conformant `chat.completion.chunk` deltas
  and `data: [DONE]` (§2, Gemini streaming row), but that stream was not kept
  as a file.
- The one streamed capture, `tests/fixtures/anypoint/openai_gemini_stream/`, is
  a Gemini upstream's **non-conformant** stream (whole `chat.completion`
  events, no `delta`, no `[DONE]`). It breaks the openai SDK, so it is not a
  happy-path default.

## Files

- `chat-completions.stream.headers.txt` — status line and `content-type` only.
  No `x-llm-proxy-*` header is included, because none was observed on this
  shape. The simulator adds its own `x-llm-proxy-ratelimit` budget window and
  `x-donkey-simulator: true`, as on every happy path.
- `chat-completions.stream.sse` — a role chunk, one content chunk, a
  `finish_reason: "stop"` chunk, then `data: [DONE]`. No usage chunk.

Replace both with real captures when #894 records the §2 streaming row, and
delete this directory.
