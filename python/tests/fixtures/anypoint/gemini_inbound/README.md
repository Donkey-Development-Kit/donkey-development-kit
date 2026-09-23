# Gemini-native ingress — LIVE capture (docs/verified-apis.md §2, #540)

Captured **2026-09-23** from a real deployed Agent Network LLM proxy provisioned
with the model-proxy **Format = Gemini** ("native Gemini ingress") in the DDK
sandbox, env **Sandbox** (svc id `14d3b31e-4e3b-4d90-b77a-63c9d6b7ea6a`, from the
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

## There is still no Gemini adapter — by design

Verifying the route does **not** mean the SDK ships a `donkey.gemini` adapter.
Per #540 / #244 a native adapter (a `google-genai` client bound at
`connection_kwargs()` level, `BG §1.8`) is **demand-driven** — "do not guess it".
Gemini also remains reachable as an *upstream provider* behind an OpenAI-format
ingress via model-based routing (the §2 supported-providers row), which needs no
new code. This capture records that the native ingress **exists and works**; a
future adapter issue is filed only if demand warrants.
