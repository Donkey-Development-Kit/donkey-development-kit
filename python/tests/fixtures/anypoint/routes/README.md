# Per-upstream route fixtures (#894)

Live captures of the OpenAI-format ingress, one per **upstream × route × shape**,
taken by hand on 2026-10-08 with `scripts/capture_fixture.py` against the DDK
Sandbox proxies on the shared gateway. The contract test is
`tests/unit/test_route_upstream_contract.py`, and the ledger rows are in
`docs/verified-apis.md` §2 ("Per-upstream route matrix").

| Upstream | Proxy (instance) | Model |
|---|---|---|
| `openai` | `ddk-openai-model-routing` (21177134) | `openai/gpt-5-mini` |
| `azureopenai` | `ddk-azure-openai-model-routing` (21188199) | `azureopenai/gpt-5-mini` |
| `bedrockanthropic` | `ddk-bedrock-anthropic-model-routing` (21192625) | `bedrockanthropic/us.anthropic.claude-sonnet-5` |
| `gemini` | `ddk-azure-openai-model-routing` (21188199), model-based route | `gemini/gemini-2.5-flash` |

Every capture's `x-llm-proxy-llm-provider` header names the intended upstream.

Each file is named `<upstream>.<route>.<shape>`, where:

- `route` is `chat_completions` (`POST /chat/completions`) or `responses` (`POST /responses`);
- `shape` is `success` (a minimal non-streaming prompt), `stream` (the same request with
  `"stream": true`, body kept as raw SSE text in `.body.txt`), or `reject`
  (`"temperature": 99`, an out-of-range parameter the upstream itself rejects).

## Findings

- **Only `/chat/completions` works on every upstream.** Azure OpenAI returns
  `404 {"error":{"code":"404","message": "Resource not found"}}` on `/responses`
  for all three shapes.
- **`/responses` is transcoded** for Bedrock and Gemini. The result is a valid Response object,
  but its top-level `status` is `null`.
- **Streaming is uneven:**
  - OpenAI streams both routes in full.
  - Azure streams chat, with a leading `prompt_filter_results` chunk (`object: ""`, no
    choices) and an extra `latency_checkpoint` key on the usage chunk.
  - Bedrock's `/responses` stream has no `response.in_progress`, `output_text.done` or
    `content_part.done` events.
  - No Bedrock `output_item.done` carries an `item`, and `response.completed` has no
    `output`. The `openai` client's `responses.stream()` helper fails on them with
    `AttributeError` (replayed offline with `openai` 3.26.1); `responses.create(stream=True)`
    iterates them.
- **Message-typed `/responses` input works on Bedrock** (#1409, bedrock-anthropic impl
  1.0.4, 2026-11-03). Role-only `developer`/`user` messages with string content, a prior
  assistant output message and list content all return 200. Streamed, the reply opens
  with an `output_item.done` for index 0 that was never added, then streams the text at
  index 1 (`*.message_input.*`).
  - Gemini **fake-streams** both routes: one `data:` event holding the whole
    non-chunk object, with no `[DONE]` and no `event:` lines.
- **Reject envelopes differ by upstream.**
  - OpenAI and Azure send the native OpenAI `code`/`type`/`param`.
  - Bedrock sends `type: "upstream_error"`, `code: "400"`.
  - Gemini wraps Google's JSON error in `message`, with an empty `type`.
  - `classify()` maps all of them to `UpstreamRequestError`.

## Fixture provenance

- `openai.chat_completions.success`: Captured 2026-10-08 by hand against `ddk-openai-model-routing` (DDK/Sandbox), model `openai/gpt-5-mini`, POST /chat/completions, success (#894).
- `openai.chat_completions.stream`: Captured 2026-10-08 by hand against `ddk-openai-model-routing` (DDK/Sandbox), model `openai/gpt-5-mini`, POST /chat/completions, stream (#894).
- `openai.chat_completions.reject`: Captured 2026-10-08 by hand against `ddk-openai-model-routing` (DDK/Sandbox), model `openai/gpt-5-mini`, POST /chat/completions, reject (#894).
- `openai.responses.success`: Captured 2026-10-08 by hand against `ddk-openai-model-routing` (DDK/Sandbox), model `openai/gpt-5-mini`, POST /responses, success (#894).
- `openai.responses.stream`: Captured 2026-10-08 by hand against `ddk-openai-model-routing` (DDK/Sandbox), model `openai/gpt-5-mini`, POST /responses, stream (#894).
- `openai.responses.reject`: Captured 2026-10-08 by hand against `ddk-openai-model-routing` (DDK/Sandbox), model `openai/gpt-5-mini`, POST /responses, reject (#894).
- `azureopenai.chat_completions.success`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `azureopenai/gpt-5-mini`, POST /chat/completions, success (#894).
- `azureopenai.chat_completions.stream`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `azureopenai/gpt-5-mini`, POST /chat/completions, stream (#894).
- `azureopenai.chat_completions.reject`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `azureopenai/gpt-5-mini`, POST /chat/completions, reject (#894).
- `azureopenai.responses.success`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `azureopenai/gpt-5-mini`, POST /responses, success (#894).
- `azureopenai.responses.stream`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `azureopenai/gpt-5-mini`, POST /responses, stream (#894).
- `azureopenai.responses.reject`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `azureopenai/gpt-5-mini`, POST /responses, reject (#894).
- `bedrockanthropic.chat_completions.success`: Captured 2026-10-08 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /chat/completions, success (#894).
- `bedrockanthropic.chat_completions.stream`: Captured 2026-10-08 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /chat/completions, stream (#894).
- `bedrockanthropic.chat_completions.reject`: Captured 2026-10-08 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /chat/completions, reject (#894).
- `bedrockanthropic.responses.success`: Captured 2026-10-08 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /responses, success (#894).
- `bedrockanthropic.responses.stream`: Captured 2026-10-08 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /responses, stream (#894).
- `bedrockanthropic.responses.reject`: Captured 2026-10-08 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /responses, reject (#894).
- `gemini.chat_completions.success`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `gemini/gemini-2.5-flash`, POST /chat/completions, success (#894).
- `gemini.chat_completions.stream`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `gemini/gemini-2.5-flash`, POST /chat/completions, stream (#894).
- `gemini.chat_completions.reject`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `gemini/gemini-2.5-flash`, POST /chat/completions, reject (#894).
- `gemini.responses.success`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `gemini/gemini-2.5-flash`, POST /responses, success (#894).
- `gemini.responses.stream`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `gemini/gemini-2.5-flash`, POST /responses, stream (#894).
- `gemini.responses.reject`: Captured 2026-10-08 by hand against `ddk-azure-openai-model-routing` (DDK/Sandbox), model `gemini/gemini-2.5-flash`, POST /responses, reject (#894).
- `bedrockanthropic.responses.message_input.success`: Captured 2026-11-03 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox, bedrock-anthropic transcoder impl 1.0.4), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /responses with developer/user EasyInputMessage, a prior assistant output message and list content, success (#1409).
- `bedrockanthropic.responses.message_input.stream`: Captured 2026-11-03 by hand against `ddk-bedrock-anthropic-model-routing` (DDK/Sandbox, bedrock-anthropic transcoder impl 1.0.4), model `bedrockanthropic/us.anthropic.claude-sonnet-5`, POST /responses with developer/user EasyInputMessage, a prior assistant output message and list content, stream (#1409).
