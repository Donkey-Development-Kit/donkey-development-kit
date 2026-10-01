# OpenAI-format ingress streaming to a Gemini upstream — LIVE capture (docs/verified-apis.md §2, #830)

Captured **2026-10-01** from two real OpenAI-format LLM proxies that route to a
Gemini upstream (`gemini/gemini-2.5-flash`) through model-based routing, on the
`shared-omni-gateway` Flex Gateway, env **Sandbox** (svc id
`14d3b31e-4e3b-4d90-b77a-63c9d6b7ea6a`, from the `x-envoy-decorator-operation`
header):

- `ddk-model-wallet` (API Manager instance **`21189395`**, JWT ingress, the proxy
  #830 was reported on): `request.stream.http`, `request.stream-tool-call.http`
  and their `responses.stream.*` / `responses.stream-tool-call.*`.
- `ddk-request-compression` (instance **`21194432`**, `client_id`/`client_secret`
  ingress, single Gemini route): `request.stream.request-compression.http` +
  `responses.stream.request-compression.*`.

`ddk-multi-route-fallback` (its Gemini route) answered the same way in the same
probe; it was not kept as a file. The response bytes are verbatim (`iter_raw()`).
The JWT and the consumer pair are **redacted** from the `request.*.http` files.

## What is verified

The gateway answers `"stream": true` on `/chat/completions` with a stream that is
**not** OpenAI's chunk format:

- **HTTP 200, `content-type: text/event-stream`, chunked.** It does stream: a
  longer answer arrives as several `data:` events, one per upstream chunk.
- **Every event is a `chat.completion`, not a `chat.completion.chunk`.** The
  increment rides in `choices[0].message` (`role`, `content`, `tool_calls`,
  `refusal: ""`). There is no `choices[0].delta`.
- **`finish_reason: "stop"` on every event**, including non-final ones. A tool
  call also reports `"stop"`, not `"tool_calls"`.
- **No `data: [DONE]` sentinel.** The body ends after the last event.
- **`usage` is on every event and is cumulative** (`completion_tokens` grows; the
  last event holds the call's total). `stream_options.include_usage` changes
  nothing: there is no separate usage-only chunk.
- **Tool calls arrive whole** in one event's `message.tool_calls` (with an `id`
  and complete `arguments`), not as `tool_calls` deltas.
- `created` is `0`. The routing headers are the normal model-based set
  (`x-llm-proxy-llm-provider: gemini`, `x-llm-proxy-llm-model: gemini-2.5-flash`).

An OpenAI-routed request on the same host (`ddk-openai-model-routing`,
`gpt-5-mini`) streams conformant `chat.completion.chunk` deltas, so this is the
OpenAI→Gemini translation, not the ingress. The native Gemini ingress streams
correctly too (`../gemini_inbound/`).

## What it breaks

The openai SDK builds `ChatCompletionChunk`s from these events without
validating them, so `chunk.choices[0].delta` is `None` and the text is lost
(openai 2.54.0 and 3.22.1). `client.chat.completions.stream(...)` fails with an
`AssertionError`. Strands (strands-agents 1.57.1) streams by default and raises
`AttributeError: 'NoneType' object has no attribute 'content'` on every turn;
`stream=False` works. The SDK's transport is unaffected: the SSE usage scanner
keeps the latest counts, so `donkey.last_call` gets the terminal totals.

`tests/unit/test_openai_gemini_stream_contract.py` pins these shapes. The
simulator does not serve these files.
