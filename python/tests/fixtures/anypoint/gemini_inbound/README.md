# Gemini-native ingress — LIVE capture (docs/verified-apis.md §2, #540)

Captured **2026-09-23** from a real deployed Agent Network LLM proxy provisioned
with the model-proxy **Format = Gemini** ("native Gemini ingress") in the DDK
sandbox, env **Sandbox** (svc id `00000000-0000-4000-8000-5c4fd1a49fc3`, from the
`x-envoy-decorator-operation` header). Proxy asset `ddk-gemini-inbound`, API
Manager instance **`21193369`**, deployed to the `shared-omni-gateway` Flex
Gateway, single-route passthrough to a Gemini upstream
(`https://generativelanguage.googleapis.com/v1beta/`, model `gemini-2.5-flash`).

This is the **first live confirmation of a non-OpenAI ingress Format** on the LLM
proxy. It resolves issue **#540** ("verify whether the LLM proxy exposes a
Gemini-native ingress route"), previously UNVERIFIED / doc-read only. It is the
sibling of the OpenAI ingress captured in `../llm_proxy/` and the model-wallet
JWT ingress in `../model_wallet/`. See also #304 (the Anthropic-native route,
still doc-only) — the three selectable Formats (OpenAI / Gemini / Anthropic) are
documented at `docs.mulesoft.com/general/model-proxy`.

## What is verified (all witnessed live, not from the provisioning YAML's own claim)

- **The native route exists at `POST /<base-path>/models/<model>:generateContent`.**
  The Gemini API request shape (`{"contents":[{"role":"user","parts":[{"text":…}]}]}`)
  returns **HTTP 200** with a genuine native Gemini body — `candidates[].content.parts[].text`,
  `role: "model"`, `finishReason`, `usageMetadata` (incl. `thoughtsTokenCount`),
  `modelVersion`, `responseId`. See `request.success.http` +
  `responses.success.{body.json,headers.txt}`.
- **It is a passthrough, not a transcode.** The response header
  `x-llm-proxy-model-based-routing-success: Request passed through without model-based routing.`
  is present on the 200 (and on the 400 below) — the gateway forwards the native
  request verbatim rather than OpenAI↔Gemini transcoding. `routingType` stays
  `model-based`; the passthrough is driven by the per-upstream
  `routing[].upstreams[].llmConfigs.format: gemini` field (see the provisioning
  repo's `proxies/ddk-gemini-inbound.yaml`).
- **The ingress is genuinely native Gemini, not OpenAI.** An OpenAI-shaped
  request to `/<base-path>/chat/completions`
  (`{"model":…,"messages":[…]}`) is **rejected with HTTP 400** and a native-Gemini
  error envelope — a JSON *array* `[{"error":{"code":400,"status":"INVALID_ARGUMENT",
  "message":"Missing or invalid Authorization header."}}]`. The forwarded
  OpenAI body does not satisfy the Gemini upstream, confirming the ingress does
  not accept the OpenAI wire format. See `reject.openai-shape.*`. (We record the
  observed 400 shape; we do not assert *why* the upstream rejects it beyond "the
  OpenAI-shape request is not accepted".)
- **Client ID Enforcement is on.** A request with no `client_id` header →
  **HTTP 401** `{"error":"Client ID is not present"}` with
  `www-authenticate: Client-ID-Enforcement` — identical to the OpenAI proxy's CIE
  behavior in `../llm_proxy/reject.client-id-missing.*`. The consumer auth pair is
  the same `client_id` / `client_secret` header pair verified in §2.

## How this was captured

A throwaway consumer application + contract were minted against instance
`21193369` via the `ddk-request-llm-proxy-access` flow (Exchange app + API
Manager contract; contract auto-approved, ~20s Flex Gateway propagation before
the pair authenticated). The two data-plane requests above were then issued
directly. Consumer `client_id`/`client_secret` are **redacted** from
`request.success.http` and were never persisted; the probe applications were
removed after capture.

## Streaming and refusal captures (2026-09-29, #691)

Captured for the ADK native Gemini adapter (`donkey.adk.gemini(...)`), same
instance `21193369`, with a consumer pair contracted on the proxy. The pair is
**redacted** from both `request.*.http` files.

- **Streaming is routed.** `POST /<base-path>/models/<model>:streamGenerateContent?alt=sse`
  → **HTTP 200**, `content-type: text/event-stream`. Each `data:` event is a
  native Gemini chunk; `usageMetadata` is **cumulative**, so the last event
  carries the call's total. See `request.stream.http` +
  `responses.stream.{body.txt,headers.txt}` (sent with `thinkingBudget: 0`).
- **Unknown model is Google's 404, passed through.** `models/gemini-no-such-model:generateContent`
  → **HTTP 404** with Google's `NOT_FOUND` envelope; `classify()` maps it to
  `UpstreamRequestError`. See `request.unknown-model.http` +
  `reject.unknown-model.*`.

`usageMetadata.totalTokenCount` includes `thoughtsTokenCount`, and no
`x-llm-proxy-llm-model` / `-llm-provider` header is emitted, so
`donkey.last_call.served_model` stays `None` on this route. The body `model`
field is ignored by the gateway (the URL path picks the model), so the SDK reads
the requested model from the path. `tests/unit/test_gemini_inbound_contract.py`
pins these shapes. The simulator does not serve these files.

## The SDK adapter

Google ADK's native `google.adk.models.Gemini` is bound to this ingress by
`donkey.adk.gemini(...)` (#691, `BG §1.8`), with the shared http client injected
through `HttpOptions.httpx_async_client`. There is no standalone `google-genai`
adapter; the manual equivalent is `donkey.adk.gemini_connection_kwargs()`.
Gemini also remains reachable as an *upstream provider* behind an OpenAI-format
ingress via model-based routing (the §2 supported-providers row).
