# The llm-gw-v2 transcoding chain — LIVE capture (docs/verified-apis.md §2, #1409)

Captured **2026-11-14** from a short-lived probe proxy (`ddk-testv2-probe`, API
Manager instance **`21300212`**, since torn down) on the DDK org's
`shared-omni-gateway`, env **Sandbox**. The proxy ran the v2 outbound chain that
replaced the v1 transcoders:

| Policy | Implementation |
| --- | --- |
| `llm-v2-out-anthropic-to-openai-trans-policy` | 1.0.1 |
| `llm-v2-out-gemini-to-openai-trans-policy` | 1.0.1 |
| `llm-v2-out-auth-apikey-policy` | 1.0.0 |
| `llm-proxy-core` | 1.0.12 |
| `model-based-routing` | 1.0.10 |

Two model-based routes: `anthropic/claude-haiku-4-5-20251001` and
`gemini/gemini-3.8-flash`. Ingress was `client_id`/`client_secret`.

## Files

`<upstream>.<route>.<case>[.stream].{headers.txt,body.json|body.txt}`:

- `*.responses.tool_{long,invalid,collide}` — `/responses` with one function tool,
  non-streaming and streaming. `tool_long` is a 147-character name, `tool_invalid` is
  `mcp.server/get weather!`, and `tool_collide` declares both `get.weather` and
  `get_weather`.
- `*.{chat,responses}.cache_{1,2}.body.json` — two identical calls carrying a
  ~7k-token prefix, bodies only (the headers were not kept for these runs).

The bytes are verbatim apart from `scripts/scrub_fixtures.py` (UUIDs, provider
account headers). The consumer credentials were never written to disk.

## What is verified

- **Tool names round-trip unchanged** on both upstreams, in both modes: the
  147-character and the `mcp.server/get weather!` names come back exactly as
  sent, in `output[].name` and in the stream's `response.output_item.*` events.
- **The `get.weather` / `get_weather` pair is accepted** (200). Anthropic called
  `get.weather`. Gemini (`max_output_tokens: 300`, not echoed back) spent the
  budget on reasoning and returned an empty `output` with `status: "completed"`
  and no `incomplete_details`. The name-collision 400 is not reproduced by this chain.
- **`/responses` streams with the named-event lifecycle** on both upstreams:
  `response.created`, `response.in_progress`, `response.output_item.added` /
  `.done` (reasoning items included for Gemini),
  `response.function_call_arguments.delta` / `.done`, and a terminal
  `response.completed` that carries `usage`. This replaces the v1 Gemini shape
  pinned in `../openai_gemini_stream/`.
- **Usage**: `input_tokens` / `prompt_tokens` include the cache reads, and
  `cached_tokens` is the cache read (Anthropic chat 7309 on the second call, Gemini
  4709). There is no cache-write field. Gemini reports `reasoning_tokens`. The two
  Anthropic `/responses` cache captures put the prefix in `instructions`, which
  carries no `cache_control`, so they show `cached_tokens: 0`. A run with
  `cache_control` on a system `input_text` cached 7312 tokens on the second call;
  that run was not kept as a file.
- **The routing headers are still returned** on this chain:
  `x-llm-proxy-routing-type: ModelBased`, `x-llm-proxy-routing-fallback`,
  `x-llm-proxy-llm-provider` and `x-llm-proxy-llm-model`.

Pinned by `tests/unit/test_v2_chain_contract.py`.

## Client replay compatibility (#1764)

Offline replay on 2026-11-15: `openai` **1.66.5** (Python 3.10.21) and
**3.51.0** (Python 3.12) parse both `tool_long` streams with
`responses.stream()` and recover the final function call and usage.
**1.66.0–1.66.3** misclassify the sparse `response.created` event and fail with
`AttributeError: 'dict' object has no attribute 'to_dict'`. The upstream
[discriminated-union fix](https://github.com/openai/openai-python/commit/eb3d7ae36d2686645e15840ab369255157247dd9)
first ships on PyPI in **1.66.5** (1.66.4 is not published), the new `[llm]`
floor. The captures remain unchanged; this is client replay evidence, not a
new live capture.
