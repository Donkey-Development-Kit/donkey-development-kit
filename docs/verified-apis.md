# Verified APIs — output of the verification discipline

> **Status (2026-08-28): partially verified.** The **management/control-plane**
> contract (OAuth token path, Exchange/API-Manager/gateway REST endpoints,
> provisioning topology, project format, control-plane headers) is now
> `VERIFIED (plugin)` — read from the shipping official client
> (`mulesoft-anypoint-cli-agent-fabric-plugin` v1.0.11 + `anypoint-cli-command`
> 1.6.8); see **§12**. Several sandbox facts are `VERIFIED (CLI)` from live
> `anypoint-cli-v4` runs (§1, §5, §6, §7). The **LLM-proxy data plane** (§2), the
> **attribution model** (§3), and the **policy rejection shapes** (§4) are now
> `VERIFIED (LIVE)` — a real governed proxy (`openai-sdk`) was deployed to
> `agent-network-ingress-gw` and called end-to-end on 2026-08-28 (fixtures in
> `src/donkey_kit/simulator/_fixtures/anypoint/llm_proxy/`). Streaming + `/models` on the proxy
> (§2) and the token-rate-limit (`429`, empty body, header-only budget) + PII
> (`403`, `type:pii_detected`) rejection contracts (§4) are now also
> `VERIFIED (LIVE)` — both policies were applied to `openai-sdk` for capture and
> removed. The regex-prompt-guard (`403` +
> `matched_patterns`) and Azure content-safety (`403` + `…-action: reject`)
> rejection shapes were also `VERIFIED (LIVE)` on 2026-09-22 against the deployed
> `ddk-injection-guard` / `ddk-azure-content-safety` proxies (#253), and the
> Bedrock Guardrails content-safety shape (same `…-action: reject` family) on
> 2026-09-24 against `ddk-bedrock-guardrails` (#568). The Injection Protection
> body (`x-injection-protection: blocked`) was itself `VERIFIED (LIVE)` on
> 2026-09-27 against `ddk-injection-protection` (instance 21200898, #669) — a
> real 79-byte body, replacing the honest empty placeholder. The PII and
> token-rate-limit shapes were re-confirmed on 2026-10-07 against
> `ddk-pii-masking` / `ddk-token-rate-limit` (#253). One streaming exception is
> recorded `VERIFIED (LIVE)` but **non-conformant**: chat-completions streaming
> over a Gemini upstream (§2, #830, 2026-10-01) sends whole `chat.completion`
> events with no `delta` and no `[DONE]`. This is an upstream gap. Still
> `UNVERIFIED`: any other unrecognised content-moderation shape (§4, the
> fall-through; federated-guardrail verdicts are tracked in #305), and the **framework
> constructor/binding names** (§§8–10). §11
> records A2D shapes
> (`VERIFIED-SHAPE-ONLY`), used only to validate SDK value types; no blocked
> code path was wired to them.
>
> **Status legend:** `VERIFIED (LIVE)` = observed from a real request against the
> deployed sandbox gateway. `VERIFIED (CLI)` = observed from a live
> `anypoint-cli-v4` run against the real sandbox. `VERIFIED (plugin)` = exact
> signature read from the official compiled client (authoritative, but a live
> request was not additionally replayed). `VERIFIED (build)` = read from an
> artifact produced by `agent-network project build`. `VERIFIED-SHAPE-ONLY` =
> data shape confirmed from an Anypoint-adjacent source (A2D), not the direct
> contract. `UNVERIFIED` = not yet confirmed; its code guard stays in place.
>
> This is the verification ledger. For any row still `UNVERIFIED`, the
> implementing engineer must, against a **real Anypoint sandbox**, confirm the
> signature, then change the row's status, fill in the **Verified value**,
> **Date**, and **Source** columns. A code guard
> (`NotImplementedError("blocked on verification: …")`) or `UNVERIFIED_*`
> placeholder is removed only after its row is confirmed **and** the maintainer
> signs off on scope — for `VERIFIED (plugin)` rows that means one live smoke
> request first (see §12.8).
>
> Working instruction #2: *never invent an endpoint, header name, or class name.*
> A fabricated endpoint that 404s in a customer sandbox destroys trust in the
> whole package (verification discipline).

Placeholder constants that gate behaviour live in
`src/donkey_kit/core/_verify.py`. Each is emitted with a runtime warning until
its status here flips to `VERIFIED`.

---

<!-- toc -->
**Contents**

- [1. Anypoint control plane](#1-anypoint-control-plane)
- [2. LLM Proxy (data plane) — LIVE-VERIFIED 2026-08-28](#2-llm-proxy-data-plane--live-verified-2026-08-28)
  - [Per-upstream route matrix (#894) — LIVE-VERIFIED 2026-10-08](#per-upstream-route-matrix-894--live-verified-2026-10-08)
- [3. Token attribution headers (highest-priority unknown, verification discipline)](#3-token-attribution-headers-highest-priority-unknown-verification-discipline)
- [4. Policy rejection response shapes (capture as fixtures, BG §1.5)](#4-policy-rejection-response-shapes-capture-as-fixtures-bg-15)
- [5. MCP Bridge / Agent Network provisioning — gates whether §5 is viable at all](#5-mcp-bridge--agent-network-provisioning--gates-whether-5-is-viable-at-all)
- [6. Governance / local-mode (the Verification milestone)](#6-governance--local-mode-the-verification-milestone)
- [7. Publication / Exchange (BG §2.5)](#7-publication--exchange-bg-25)
- [8. Framework APIs (BG §1.8) — re-verify every constructor](#8-framework-apis-bg-18--re-verify-every-constructor)
  - [8.1 Known upstream incompatibilities (floors, not ceilings)](#81-known-upstream-incompatibilities-floors-not-ceilings)
  - [8.2 Known upstream runtime limitations](#82-known-upstream-runtime-limitations)
  - [8.3 Extras floors (lowest verified versions)](#83-extras-floors-lowest-verified-versions)
- [9. MCP tool binding classes (BG §2.7) — verify each name](#9-mcp-tool-binding-classes-bg-27--verify-each-name)
- [10. Descriptor-derivation attributes (BG §2.5) — semi-public, put in nightly matrix](#10-descriptor-derivation-attributes-bg-25--semi-public-put-in-nightly-matrix)
- [11. A2D platform MCP tools — shapes captured 2026-08-28 (NOT the direct Anypoint REST API)](#11-a2d-platform-mcp-tools--shapes-captured-2026-08-28-not-the-direct-anypoint-rest-api)
  - [Open design questions surfaced by the probe (need a platform-team decision)](#open-design-questions-surfaced-by-the-probe-need-a-platform-team-decision)
- [12. Agent-donkey CLI plugin — direct REST contract (static analysis, 2026-08-28)](#12-agent-donkey-cli-plugin--direct-rest-contract-static-analysis-2026-08-28)
  - [12.1 Auth (transport dependency anypoint-cli-command/lib/)](#121-auth-transport-dependency-anypoint-cli-commandlib)
  - [12.2 Control-plane attribution / correlation headers (CLI request headers)](#122-control-plane-attribution--correlation-headers-cli-request-headers)
  - [12.3 Exchange publish / read (dist/utils/exchange.js, dist/utils/facets/asset-facet.js)](#123-exchange-publish--read-distutilsexchangejs-distutilsfacetsasset-facetjs)
  - [12.4 Agent Network gateway setup + Private Space (dist/utils/uris.js, gateway.js)](#124-agent-network-gateway-setup--private-space-distutilsurisjs-gatewayjs)
  - [12.5 API Manager governance + app deploy (dist/utils/facets/*)](#125-api-manager-governance--app-deploy-distutilsfacets)
  - [12.6 LLM proxy — how governance is actually wired](#126-llm-proxy--how-governance-is-actually-wired)
  - [12.7 Agent Network project format (dist/commands/agent-network/project/create.js, templates/)](#127-agent-network-project-format-distcommandsagent-networkprojectcreatejs-templates)
  - [12.8 Unblocking guidance (verification discipline)](#128-unblocking-guidance-verification-discipline)
<!-- tocstop -->

## 1. Anypoint control plane

CLI-verified rows were confirmed against the real sandbox org
`00000000-0000-4000-8000-40e9a964ded9` (user `admin-af`) on 2026-08-28 using
`anypoint-cli-v4` 1.6.26. The CLI confirms **data contracts and the auth model**;
the exact direct-REST paths the SDK calls come from the donkey-kit CLI plugin
analysis (see §11/§12) — do not remove a code guard until its own row is
VERIFIED with a concrete path.

| Item | Where used | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| Control-plane host (US) | `core/config.py` | VERIFIED (CLI) | `anypoint.mulesoft.com`; overridable via `ANYPOINT_HOST` | 2026-08-28 | `anypoint-cli-v4 conf host` |
| Auth env var names | `core/config.py` | VERIFIED (CLI) | `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET`, `ANYPOINT_ORG`, `ANYPOINT_ENV`, `ANYPOINT_BEARER` — match DonkeyConfig | 2026-08-28 | CLI flag defaults (`agent-network:*`) |
| Auth model | `core/auth.py` | VERIFIED (CLI) | connected-app `client_id`/`client_secret` OR direct `bearer` token both accepted | 2026-08-28 | CLI flags |
| Org / business-group as attribution unit | `core/config.py` | VERIFIED (CLI) | root BG `anypoint-cbp-1780648272` = org id (UUID); assets publish under org id as Maven groupId | 2026-08-28 | `account:business-group:list`, `api-mgr:api:describe` |
| Environments | `core/config.py` (environment targeting) | VERIFIED (CLI) | `{Name, Id (UUID), Sandbox: Y/N}`; sandbox has `Design` + `Sandbox` | 2026-08-28 | `account:environment:list` |
| OAuth2 token endpoint path | `core/auth.py` | VERIFIED (plugin) | `POST /accounts/api/v2/oauth2/token`, body `{client_id, client_secret, grant_type}` → `{access_token, expires_in}` | 2026-08-28 | §12.1 (`anypoint-cli-command/lib/login.js`) |
| Region host variants (US/EU/CA/JP Hyperforce) | `core/_verify.py` `UNVERIFIED_REGION_HOSTS` (warns once via `DonkeyConfig.control_plane_url`) | UNVERIFIED | US = `anypoint.mulesoft.com` confirmed; others pending | — | — |
| Connected-app scopes: Exchange read | `core/auth.py`, docs | UNVERIFIED | — | — | — |
| Connected-app scopes: API Manager write | provisioning | UNVERIFIED | — | — | — |
| Connected-app scopes: policy management | provisioning | UNVERIFIED | — | — | — |
| Which ops require an **admin** connected app w/ user context | `core/auth.py` | UNVERIFIED | — | — | — |

## 2. LLM Proxy (data plane) — **LIVE-VERIFIED 2026-08-28**

A real governed ingress LLM proxy (`openai-sdk`, instance `21133858`) was
deployed to `agent-network-ingress-gw` in the sandbox and called end-to-end
(HTTP 200 with a real OpenAI completion). Captures in
`src/donkey_kit/simulator/_fixtures/anypoint/llm_proxy/`. The proxy is an **OpenAI-compatible
passthrough**: the SDK points a framework's OpenAI client at the proxy base and
sends normal OpenAI request bodies; the upstream response is returned verbatim
plus Anypoint governance headers. `VERIFIED (LIVE)` = observed from a real
request against the deployed gateway.

| Item | Where used | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| Ingress base URL the SDK targets | `llm/client.py` | VERIFIED (LIVE) | `https://<ingress-gw-host>/<instance-path>/` — e.g. `https://agent-network-ingress-gw.example.invalid/openai-sdk/`. **No `/v1` at the ingress**; OpenAI path segment appended directly (`/responses`, and OpenAI-native routes) | 2026-08-28 | live probe |
| Ingress **Format** — Anthropic-native route (#304) | `integrations/anthropic.py`, `llm/client.py` | VERIFIED (LIVE) | A proxy provisioned `Format=Anthropic` exposes a **native Anthropic Messages ingress** at `POST /<base-path>/v1/messages`. The Anthropic request shape (header `anthropic-version: 2023-06-01`, body `{"model":…,"max_tokens":N,"messages":[{"role":"user","content":…}]}`) → **200** with a native Anthropic body (`type:"message"`, `role:"assistant"`, `content[].text`, `stop_reason:"end_turn"`, `usage.input_tokens`/`output_tokens`/`service_tier`) plus native `anthropic-ratelimit-*` / `request-id` / `anthropic-organization-id` response headers. It is a **passthrough, not a transcode** — gateway headers `x-llm-proxy-llm-provider: anthropic`, `x-llm-proxy-llm-model: <model>`, `x-llm-proxy-request-success` (`routingType` stays `model-based`; enabled by the per-upstream `routing[].upstreams[].llmConfigs.format: anthropic` field). An **OpenAI-shaped** request to `/<base-path>/chat/completions` → **404** (empty body) — the OpenAI route is simply not served on a native-Anthropic ingress (differs from Gemini's 400; both prove the ingress rejects the OpenAI wire format). Same `client_id`/`client_secret` CIE auth (no `client_id` → 401 "Client ID is not present", `www-authenticate: Client-ID-Enforcement`). Native ingress is **single-route only** (no multi-routing/fallback — that stays OpenAI-only); the upstream must speak native Anthropic (`provider: anthropic`/`api.anthropic.com` — **Bedrock is not usable** here). The `donkey.anthropic` adapter targets this route, but the SDK's own DDK proxies are `Format=OpenAI` — point `base_url` at a `Format=Anthropic` proxy to use the native surface. The roadmap "OAuth Client ID/Secret for configuring LLMs" is an **upstream/control-plane credential** concern and does **not** change this verified consumer `client_id`/`client_secret` request-header pair. | 2026-09-24 | live probe against `ddk-anthropic-inbound` (instance `21194086`, DDK/Sandbox) — captured in `python/tests/fixtures/anypoint/anthropic_inbound/` |
| Ingress **Format** — Gemini-native route (#540, #691) | `integrations/adk.py` (`gemini()`, #691); `core/transport/policy.py` (`_request_model`, model from the URL path) + `core/lastcall.py` (`usageMetadata`) | VERIFIED (LIVE) | A proxy provisioned `Format=Gemini` exposes a **native Gemini ingress** at `POST /<base-path>/models/<model>:generateContent`. The Gemini request shape (`{"contents":[{"role":"user","parts":[{"text":…}]}]}`) → **200** with a native Gemini body (`candidates[].content.parts[].text`, `role:"model"`, `usageMetadata` incl. `thoughtsTokenCount`, `modelVersion`, `responseId`). It is a **passthrough, not a transcode** — response header `x-llm-proxy-model-based-routing-success: "Request passed through without model-based routing."` (`routingType` stays `model-based`; enabled by the per-upstream `routing[].upstreams[].llmConfigs.format: gemini` field). An **OpenAI-shaped** request to `/<base-path>/chat/completions` → **400** with a native-Gemini error envelope (a JSON *array* `[{"error":{"code":400,"status":"INVALID_ARGUMENT",…}}]`), confirming the ingress does not accept the OpenAI wire format. Same `client_id`/`client_secret` CIE auth (no `client_id` → 401 "Client ID is not present"). Native ingress is **single-route only** (no multi-routing/fallback — that stays OpenAI-only). **Re-probed 2026-09-29 for the ADK native adapter (#691):** (a) `google-genai` always sends the API key as `x-goog-api-key`; a non-empty placeholder alongside `client_id`/`client_secret` does not clash → 200; the gateway echoes `x-correlation-id`. (b) `POST /<base-path>/models/<model>:streamGenerateContent?alt=sse` is routed → `text/event-stream`, each event a Gemini chunk with **cumulative** `usageMetadata` (last event is the total). (c) A `functionCall`/`functionResponse` round-trip works. (d) The gateway **ignores a body `model` field** — absent, `gemini-2.5-flash` and `gemini/gemini-2.5-flash` all → 200 with the same passthrough header; the URL path picks the model, so the SDK reads it from there for `last_call`/spans. No `x-llm-proxy-llm-model`/`-provider` header is emitted, so `served_model`/`served_provider` stay `None`. (e) Refusals: bad secret → **401** (`classify` → `AuthError`); unknown model → Google's **404** `NOT_FOUND` passed through (`classify` → `UpstreamRequestError`). `usageMetadata.totalTokenCount` **includes** `thoughtsTokenCount`. Gemini also remains reachable as an *upstream provider* behind an OpenAI ingress (supported-providers row below). | 2026-09-29 | live probes against `ddk-gemini-inbound` (instance `21193369`, DDK/Sandbox) on 2026-09-23 and 2026-09-29 — captured in `python/tests/fixtures/anypoint/gemini_inbound/` |
| Endpoint/API surface | `llm/client.py`; opt-in on `integrations/langgraph.py` (`use_responses_api=True`) and `integrations/agent_framework.py` (`api="responses"`: `OpenAIChatClient`, agent-framework's Responses client); both adapters default to Chat Completions since #1043 (row below) | VERIFIED (LIVE) | OpenAI **Responses API** works (`POST /openai-sdk/responses`, body `{model, input}`). Upstream registered as `https://api.openai.com/v1/`; `proxyUri http://0.0.0.0:8081/openai-sdk`. The LangGraph and agent-framework adapters target it only on opt-in; both default to `/chat/completions` (#1043). The local simulator serves it and `/chat/completions` (#895) | 2026-08-28 | `api:describe`, live probe |
| Endpoint/API surface — Chat Completions (#793) | `integrations/adk.py` (`model()`: LiteLLM `openai/…`), `integrations/openai_agents.py` (`OpenAIChatCompletionsModel`), `integrations/crewai.py` (`OpenAICompletion`, `api="completions"` default), `integrations/llamaindex.py` (`OpenAILike`, `is_chat_model=True`), `integrations/strands.py` (`OpenAIModel`), `integrations/agent_framework.py` (`chat_client()` default, `api="chat_completions"`: `OpenAIChatCompletionClient`); `integrations/langgraph.py` (default `use_responses_api=False`), both since #1043 | VERIFIED (LIVE) | An OpenAI-format proxy also serves the OpenAI **Chat Completions API**: `POST /<base-path>/chat/completions`, body `{model, messages}` → **200** with a verbatim `object: "chat.completion"` body (incl. `usage`) and the same `x-llm-proxy-*` governance headers. Observed on four proxies: model-wallet (JWT ingress), semantic caching, semantic routing, and Azure OpenAI model-based routing (`ddk-azure-openai-model-routing`, 2026-10-07, #896), whose upstream answers **`/responses` with 404** `Resource not found` after the proxy routed the model (`x-llm-proxy-model-based-routing-success` present, `x-llm-proxy-request-success` absent) and `/chat/completions` with 200. A base path no proxy serves answers an empty 404 with no `x-llm-proxy-*` header; `donkey doctor` reads both 404 shapes. Streaming is now captured per upstream (per-upstream route matrix below, #894); the simulator serves the Azure capture as its `/chat/completions` 200 and OpenAI's public chunk shape, labelled as not a capture, for a stream (#895). The `Format=Anthropic` (404) and `Format=Gemini` (400) ingresses reject this route (rows above) | 2026-10-07 | live probes — `tests/fixtures/anypoint/model_wallet/` (2026-09-21), `semantic_cache/` and `semantic_routing/` (2026-09-24), each `request.success.http` + its 200 body; `src/donkey_kit/simulator/_fixtures/anypoint/azure_openai_routing/` (2026-10-07, moved there in #895) |
| Auth: header name / model | `core/transport/headers.py`, `core/config.py` | VERIFIED (LIVE) | **`client_id` + `client_secret` request headers** (NOT bearer) — enforced by `client-id-enforcement` 1.3.3. A consumer **credential pair**, mapped to an Anypoint client application | 2026-08-28 | live probe + `policy:list` |
| Auth: model-wallet ingress (alt to `client_id`/`client_secret`, #372) | `core/transport/headers.py`, `core/config.py` — contract verified; SDK wiring landed in #509 (`llm_proxy_auth="jwt"` + `llm_proxy_wallet_client_id` + `Donkey(llm_auth=…)`) | VERIFIED (LIVE) | Wallet-backed model proxies auth via an **IdP-issued JWT + a client ID**, with **no `client_secret`**: the client ID travels as the **`X-Client-Id`** request header (exact casing confirmed; value = the wallet's system-generated `clientId`, `ddk-model-wallet` here — read it from the omni API response, don't assume) which selects the wallet (echoed back as response header **`x-model-wallet-selected`**), *and* as a `client_id` **JWT claim** read by the LLM Proxy Core Policy at `#[authentication.properties.claims.client_id]` after the **JWT Validation** policy validates + publishes claims. **The JWT rides as `Authorization: Bearer <JWT>`** — this was an *assumption* from the doc read (Bearer is nowhere quoted on the source page); now **confirmed** (policy `jwtOrigin: httpBearerAuthenticationHeader`; missing header → `400 {"error":"JWT Token is required."}`, `www-authenticate: Bearer`; invalid/expired token → `401 {"error":"Invalid token."}`). No `client_id`/`client_secret` request headers are sent and the call still succeeds → the default **DataWeave Headers Transformation** + **Client ID Enforcement** policies are confirmed **disabled**. Requires an org **IdP** (JWKS URL / signing key — "orgs without an IdP can't use model wallets"). A **parallel** ingress model, NOT a replacement of the LIVE `client_id`/`client_secret` pair above. | 2026-09-21 | live probe — `tests/fixtures/anypoint/model_wallet/` (`ddk-model-wallet`, instance `21186246`, Sandbox); shape corroborates `docs.mulesoft.com/general/exp-model-wallets-manage` |
| Auth: plain bearer token, no wallet (#836) | `core/config.py` (`llm_proxy_auth="bearer"`), `core/transport/headers.py`, `core/runtime.py` | PARTIAL | Sends the token from `Donkey(llm_auth=…)` as **`Authorization: Bearer <token>`**, the header and scheme already VERIFIED (LIVE) on the model-wallet row (`_verify.LLM_PROXY_WALLET_JWT_HEADER` / `_SCHEME`), with no `X-Client-Id` and no `client_id`/`client_secret`. The SDK side is unit-tested against a mock transport (`tests/unit/test_llm_bearer_mode.py`). Not yet captured live: a proxy with JWT Validation on and no model wallet (the shape the model-wallet row implies but did not probe). | 2026-10-01 | unit tests; header shape from the model-wallet live probe |
| Model routing | `llm/client.py`; consumed by `core/lastcall.py` + `core/transport/` (#309) | VERIFIED (LIVE) | `model-based-routing` 1.0.3 reads `model` from body → provider. Response headers `x-llm-proxy-routing-type: ModelBased`, `x-llm-proxy-routing-fallback: false`, `x-llm-proxy-llm-provider: openai`, `x-llm-proxy-llm-model: gpt-5.1`, `x-llm-proxy-model-based-routing-success: Request successfully matched. …`. As of #309 the SDK surfaces these at `donkey.last_call` (`served_provider`/`served_model`/`routing_type`/`fallback`, `substituted` when served≠requested — `x-llm-proxy-llm-model` is the BARE model and the `provider/` routing prefix travels in `x-llm-proxy-llm-provider`, so a requested prefix naming the served provider is ignored; live on OpenAI, Gemini, Azure OpenAI and Bedrock routes 2026-09-24, #586), emits them as `gen_ai.response.model` / `donkey.routing.type` / `donkey.routing.fallback` span attrs, and **does not retry** a `503` a fallback header already marked failed-over (#309, must not fight #183). With **more than one provider** configured, a bare model name (`gpt-5-mini`) the gateway cannot pin to a single provider is rejected `400` before any upstream call; use `provider/model` (`openai/gpt-5-mini`). `classify()` types it `ModelNotRoutable` (§4 item 6, live 2026-10-01, #825) | 2026-08-28 | live probe (`responses.success.headers.txt`) |
| Semantic routing (#589, #590) | `llm/client.py`; the four shared routing headers consumed by `core/lastcall.py` + `core/transport/` (#309); the semantic-only `x-llm-proxy-semantic-routing-success` header parsed by `core/lastcall.py` (`_parse_semantic_routing` / `semantic_routing`) into `LastCall.matched_topic` + `LastCall.routing_score`, and emitted on the span as `donkey.routing.matched_topic` / `donkey.routing.score` via `core/telemetry.py` (#590) | VERIFIED (LIVE) | A **semantic-routing** proxy emits the **same four routing headers as model-based** — `x-llm-proxy-routing-type` (value **`Semantic`** vs `ModelBased`), `-routing-fallback`, `-llm-provider`, `-llm-model` — so #309's `LastCall` parsing already works unchanged for the semantic case. Additionally, a **semantic-only** header carries the match detail: **`x-llm-proxy-semantic-routing-success`**, exact format `Request successfully matched '{topic}' topic (Provider: {provider}, Model: {model}). Score: {score}.` (observed: Finance → `openai`/`gpt-5-mini` score `0.62`; Code → `gemini`/`gemini-2.5-flash` score `0.62`). Note the format is `(Provider: …, Model: …)` — **not** a `provider/model` slug — and the score is a bare `0.xx`. Also present alongside: `x-llm-proxy-request-success` (`Request completed successfully. Provider: …, Model: ….`) and, on OpenAI-served routes, cost hints `x-llm-proxy-input-tokens-cost-per-1m` / `-output-tokens-cost-per-1m` (out of scope for #590 — see its Out of scope). **Fallback (`routing-fallback: true`) was NOT reproducible on this SSC** (§0.3 — not fabricated): with a 10-vector `text-embedding-3-small` store, cosine similarity floors ~`0.51`–`0.53` (above the `0.5` threshold, comparison strictly-greater-than) even for gibberish/off-topic prompts, so every request matched a primary topic with `routing-fallback: false`. The fallback-branch header shape therefore remains uncaptured. | 2026-09-24 | live probe against `ddk-semantic-advanced-routing` (instance `21194930`, DDK/Sandbox, private-space) — captured in `python/src/donkey_kit/simulator/_fixtures/anypoint/semantic_routing/` |
| Semantic caching (#588; consumed by #587) | Request steering will inject `x-cache-*` at the `core/transport/` `_inject_headers` seam; response signals will surface on `core/lastcall.py` + span attrs via `core/telemetry.py`; header names are plain constants in `core/_verify.py`, injected by `core/cachecontrol.py` (#587) | VERIFIED (LIVE) | A proxy with the **Semantic Caching policy** (`semantic-caching-policy-openai-azure-ai-search` 1.0.1) honors five **lowercase** `x-cache-*` request steering headers (spec's `X-Cache-*` casing accepted case-insensitively): `x-cache-skip: true` → `x-semantic-cache-status: bypass`; `x-cache-no-store: true` → `x-semantic-cache-status: no-store` + echo `x-semantic-cache-no-store: true`, entry **not written** (proven by a follow-up `miss` on the same prompt); `x-cache-ttl: 60` → echo `x-semantic-cache-ttl: 60` (default `604800`); `x-cache-threshold: 0.5` → echo `x-semantic-cache-threshold: 0.5000` (default `0.7500`); `x-cache-principal-id: <s>` → echo `x-semantic-cache-principal-id: <s>` and **partitions the similarity filter** (a set principal missed a semantically-near cached entry). Response signals: **`x-semantic-cache-status`** takes all four values `miss`/`hit`/`bypass`/`no-store` (all captured); **`x-semantic-cache-score`** (4-dp, e.g. `1.0000`) is present **only on `hit`**; `x-semantic-cache-ttl` / `-threshold` present on miss/hit/no-store but **absent on `bypass`**. **Cost on hit is proven by verbatim replay, NOT a `total_cost` body field** (#587's description assumed one — it does **not** exist): a `hit` returns the **byte-identical** stored completion (same `chatcmpl-…` id + `usage`) at ~3.0s vs ~9.3s, and the upstream `openai-*`/`x-ratelimit-*`/`x-request-id` headers present on miss/bypass/no-store are **absent on hit** → no provider round-trip. So #587 must derive "zero spend on hit" from `x-semantic-cache-status == hit`, not from a body field. The static per-1M rate hints `x-llm-proxy-input-tokens-cost-per-1m` / `-output-tokens-cost-per-1m` appear on every response and are **not** a per-request charge. Layers cleanly over the model-based routing headers (§ above) without altering them. | 2026-09-24 | live probe against `ddk-semantic-cache` (instance `21195392`, DDK/Sandbox, private-space) — captured in `python/tests/fixtures/anypoint/semantic_cache/` |
| Token accounting (cost attribution) | `llm/*`, telemetry | VERIFIED (LIVE) | response `usage: {input_tokens, output_tokens, total_tokens, input_tokens_details, output_tokens_details}` — the detail sub-objects carry `cached_tokens` / `cache_write_tokens` (under `input_tokens_details`) and `reasoning_tokens` (under `output_tokens_details`), surfaced per-call at `donkey.last_call` and on the span (#307, see §3); also upstream `x-ratelimit-*` headers passed through (Go-style **duration-string** resets, e.g. `0s`/`12ms` — distinct from the gateway's own `x-llm-proxy-ratelimit`/`x-token-*` window, see §4) | 2026-08-28 | `responses.success.body.json` |
| Request fields passed through | `llm/client.py` | VERIFIED (LIVE) | OpenAI body passed through verbatim; response returned verbatim (model `gpt-5.1`→`gpt-5.1-2025-11-13`, full Responses object) | 2026-08-28 | live probe |
| Streaming support | `llm/client.py` | VERIFIED (LIVE) | `"stream": true` → `200`, `content-type: text/event-stream`, chunked SSE (`event: response.created` / `response.in_progress` / …); same `x-llm-proxy-*` headers | 2026-08-28 | live probe (`responses.stream.*`) |
| Chat-completions streaming over a Gemini upstream (#830) | `llm/client.py` (`donkey.llm.client()` callers), `integrations/strands.py`; `core/transport/streaming.py` (`_SseUsageScanner`) is unaffected | VERIFIED (LIVE) — **non-conformant** (upstream gap) | An OpenAI-format proxy that routes `chat/completions` with `"stream": true` to a **Gemini** upstream answers `200`, `content-type: text/event-stream`, chunked, but each `data:` event is a whole **`chat.completion`** carrying the next piece of text in `choices[0].message`; there is **no `delta`**. `finish_reason` is `"stop"` on **every** event (a tool call too, not `"tool_calls"`), there is **no `data: [DONE]`**, and `usage` sits on every event and is **cumulative** (`stream_options.include_usage` adds no usage-only chunk). Tool calls arrive whole in `message.tool_calls`. The openai SDK builds `ChatCompletionChunk`s without validating them, so `choices[0].delta` is `None` (openai 2.54.0 and 3.22.1), and `chat.completions.stream(...)` raises `AssertionError`. Strands streams by default and fails on every turn (strands-agents 1.57.1); `stream=False` works. `donkey.strands.model()` therefore builds its `OpenAIModel` with `stream=False` (#852); pass `stream=True` only on a route that sends deltas. The SSE usage scanner keeps the latest counts, so `donkey.last_call` and the span still get the terminal totals. Same on all three Gemini routes probed (`ddk-model-wallet`, `ddk-request-compression`, `ddk-multi-route-fallback`); an OpenAI route on the same host (`ddk-openai-model-routing`) streams conformant `chat.completion.chunk` deltas and `[DONE]`. The native Gemini ingress (row above) streams correctly. | 2026-10-01 | live probe — `python/tests/fixtures/anypoint/openai_gemini_stream/`, pinned by `tests/unit/test_openai_gemini_stream_contract.py` |
| `/models` endpoint | `llm/catalog.py` | VERIFIED (LIVE) | **Does not exist** — `GET /openai-sdk/models` → `404`, `x-llm-proxy-model-based-routing-success: Request passed through without model-based routing`. The proxy only routes requests carrying `model` in the body; no catalog endpoint. `llm/catalog.py` must source models elsewhere | 2026-08-28 | live probe (`models.notfound.headers.txt`) |
| Supported providers | `llm/catalog.py` | VERIFIED (CLI) | openai, azureopenai, gemini, **bedrock**, **anthropic** (each a `*-llm-provider-policy-flex` in org `68ef9520…`) | 2026-08-28 | `exchange:asset:list llm` |

### Per-upstream route matrix (#894) — **LIVE-VERIFIED 2026-10-08**

Each upstream behind an OpenAI-format ingress was called on both routes. Each
route had three shapes: buffered success, `"stream": true`, and a `temperature: 99`
rejection. All captures were made by hand against the DDK Sandbox proxies on the
shared gateway:

- `ddk-openai-model-routing` (21177134)
- `ddk-azure-openai-model-routing` (21188199), which serves both Azure and, through its
  model-based route, `gemini/gemini-2.5-flash`
- `ddk-bedrock-anthropic-model-routing` (21192625)

Every capture's `x-llm-proxy-llm-provider` header names the intended upstream. The
fixtures are in `python/tests/fixtures/anypoint/routes/` (the README there lists the provenance for each one), pinned by `tests/unit/test_route_upstream_contract.py`.

| Item | Where used | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| `/chat/completions` × OpenAI | `integrations/*` Chat Completions adapters (row above) | VERIFIED (LIVE) | **200**, verbatim `chat.completion` + `usage`. Stream: chunked `chat.completion.chunk` deltas ending `data: [DONE]`. Reject: **400** `{"error":{message,type:"invalid_request_error",param:"temperature",code:"decimal_above_max_value"}}` | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``openai.chat_completions.*` |
| `/chat/completions` × Azure OpenAI | same | VERIFIED (LIVE) | **200**, `chat.completion` plus per-choice `content_filter_results`. Stream: deltas and `[DONE]`, led by a content-safety chunk (`object: ""`, `choices: []`, `prompt_filter_results`); the usage chunk adds a `latency_checkpoint` object. Reject: **400**, same native OpenAI envelope as OpenAI | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``azureopenai.chat_completions.*` |
| `/chat/completions` × Bedrock-Anthropic | same | VERIFIED (LIVE) | **200**, gateway-transcoded `chat.completion` (`created: 0`, `model: "claude-sonnet-5"`). Stream: deltas and `[DONE]`. Reject: **400** `{"error":{"message":"temperature: range: 0..1","type":"upstream_error","param":null,"code":"400"}}` | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``bedrockanthropic.chat_completions.*` |
| `/chat/completions` × Gemini | same | VERIFIED (LIVE) — stream **non-conformant** | **200**, gateway-transcoded `chat.completion`. Stream: whole `chat.completion` objects (`message`, no `delta`, no `[DONE]`), the #830 shape; a one-token reply is a single event. Reject: **400**, Google's JSON error string-wrapped in `message`, with `type: ""` and `code: "400"` | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``gemini.chat_completions.*` |
| `/responses` × OpenAI | `llm/client.py`, LangGraph (`use_responses_api=True`), `agent_framework.chat_client()` default | VERIFIED (LIVE) | **200**, verbatim Response (`status: "completed"`). Stream: the full named-event sequence (`response.created` → `in_progress` → `output_item.*` / `content_part.*` / `output_text.delta` + `.done` → `response.completed`). Reject: **400**, native OpenAI envelope | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``openai.responses.*` |
| `/responses` × Azure OpenAI | same | VERIFIED (LIVE) — **not served** | **404** `{"error":{"code":"404","message": "Resource not found"}}` on all three shapes, after the proxy routed the model. The body is never validated, so the `temperature: 99` request 404s too. This confirms the #826 / 2026-10-07 probe | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``azureopenai.responses.*` |
| `/responses` × Bedrock-Anthropic | same | VERIFIED (LIVE) — stream **partial** | **200**, gateway-transcoded Response (`object: "response"`, per-item `status: "completed"`, top-level `status: null`). Stream: named events `response.created`, `output_item.added`, `content_part.added`, `output_text.delta`, `output_item.done`, `response.completed`. There is **no** `response.in_progress`, `output_text.done` or `content_part.done`. Reject: **400**, Bedrock envelope as on chat | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``bedrockanthropic.responses.*` |
| `/responses` × Gemini | same | VERIFIED (LIVE) — stream **non-conformant** | **200**, gateway-transcoded Response (top-level `status: null`). Stream: **one** `data:` event holding the whole Response, with **no `event:` lines**, so a Responses-stream client sees no named events. Reject: **400**, Gemini envelope as on chat | 2026-10-08 | live capture — `python/tests/fixtures/anypoint/routes/``gemini.responses.*` |
| Reject → SDK error | `core/errors.py` (`classify()`) | VERIFIED (LIVE) | All seven 400 envelopes and the Azure 404 map to `UpstreamRequestError`, never a gateway `PolicyViolation`. `code` is the native OpenAI code or the stringified HTTP status. `error_type` is the provider `type`, or `None` for Gemini's empty `type` | 2026-10-08 | pinned by `tests/unit/test_route_upstream_contract.py` |

**Decision: MAF/LangGraph default route (#894).** `/chat/completions` is the only
route all four upstreams serve. `/responses` 404s on Azure OpenAI and only
streams in full on OpenAI. The Responses-first defaults therefore fail or degrade
on three of the four upstreams:

- `agent_framework.chat_client()` (`api="responses"`)
- the LangGraph adapter (`use_responses_api=True`)

The decision is to make **Chat Completions the default for both**, matching the
other five adapters (row above), and keep `/responses` as the opt-in
(`api="responses"`, `use_responses_api=True`). Implemented in #1043, together
with the §8 MAF note and the `frameworks/agent-framework.mdx` "Which API"
section.

**Provider SDK retry override (#953, verified from installed SDK source, not a
gateway contract).** `openai` (`openai._base_client.BaseClient._should_retry`;
checked at the `1.66.0` floor, `2.54.0` and `3.24.0`) and `anthropic`
(`anthropic._base_client.BaseClient._should_retry`; checked at the `0.116.0`
floor and `1.11.0`) both check the response header `x-should-retry: false`
before their default `>=500` retry rule. The governed transport stamps this SDK-facing header on a
final 4xx and on a 502/504 model POST when its retry decision is `unsafe`.
It does not claim the gateway emits the header. End-to-end mock-transport tests
with default provider retries are in `test_transport.py` and
`test_anthropic_client_stacks.py`; the shared sync/async policy is covered in
`test_transport_both_clients.py`.

**Note — single-header apikey/bearer convenience (no-op for DDK).** The default
proxy's `dataweave-headers-transformation` policy (the §6 policy-stack row) is a
gateway convenience for thin clients that carry only one auth field: it accepts a
**colon-joined credential in a single header** — `authorization: Bearer
<client_id>:<client_secret>` or `apikey: <client_id>:<client_secret>` — and splits
it back into the `client_id` / `client_secret` pair, but **only** when neither
header is already present. DDK always emits the two-header pair (LIVE-verified
above), so the request lands in the policy's pass-through branch and the
transformation is a **no-op** for DDK traffic — the `Authorization` bearer DDK
sends (an `llm_proxy_key` or the inert sentinel filling the OpenAI SDK's mandatory
`api_key` slot) is never read as a credential. DDK deliberately does **not** add
an apikey/bearer emit mode; the two-header pair is the single auth path in
client-id mode (`client_id` is also the §3 attribution unit).

## 3. Token attribution headers (**highest-priority unknown**, verification discipline)

Without these the core value proposition (per-agent cost attribution) does not work.

BREAKTHROUGH (2026-08-28): the mechanism is identified from a **built** LLM-proxy
`connection.json` (`agent-network project build` of a minimal `kind: llm`
connection). The governed LLM instance carries two inbound telemetry policies:

- `agent-connection-telemetry` `1.0.3` (org `68ef9520…`): config
  `sourceAgentId: #[attributes.headers['x-anypoint-api-instance-id']]` — i.e. the
  **caller's attribution identity is carried in the `x-anypoint-api-instance-id`
  request header**, read at the gateway as the source agent id.
- `tracing` `1.1.1`: labels `mulesoft.api.instance.id` (= this connection's id)
  and `mulesoft.api.type = llm`.

Outbound: `openai-transcoding-policy` `1.0.3` with `{apiKey, timeout}`. Note this
minimal egress LLM connection did **not** get `client-id-enforcement` (unlike the
MCP ingress instances) — so the consumer-auth story for a *directly-called* LLM
proxy still needs the live-deploy capture below to confirm.

RESOLVED (2026-08-28, LIVE): for a **directly-called ingress LLM proxy**, the
attribution unit is the **`client_id` credential** (the calling agent/app is a
registered Anypoint client application, enforced by `client-id-enforcement`) —
NOT a bespoke header the SDK invents. The gateway then emits identity/telemetry
on the **response**: `x-envoy-decorator-operation:
api-instance-<id>.<envId>.svc` (carries the API-instance id + environment id),
`x-correlation-id`, and `x-llm-proxy-*` routing headers. Token usage for cost
attribution comes from the response `usage` block (§2). The
`x-anypoint-api-instance-id` **request** header (below) is the separate
agent→agent egress-telemetry path, not needed for direct LLM proxy calls.

As of #362, `core/lastcall.py` **parses** two of these response headers on the
happy path and surfaces them at `donkey.last_call`: the upstream provider's
request id becomes `LastCall.request_id` (the same value `classify()` surfaces as
`DonkeyError.request_id` on a refusal), and `x-envoy-decorator-operation`
(`api-instance-<instanceId>.<environmentId>.svc`) is split into
`LastCall.api_instance_id` (`21133858`) and `LastCall.environment_id`
(`00000000-…`, a UUID — dashes but no dots, so a three-way split on `.` is
unambiguous). An absent or unrecognised shape leaves both `None` and never
raises (verification discipline). The response `x-correlation-id` is **not** parsed into the record
yet — whether it echoes the client-sent id is unconfirmed (#300).

As of #542, `request_id` is understood as the **upstream provider's own** id,
passed through by the gateway unchanged — **not** an id the gateway mints. The
gateway never adds one of its own, so the header **name differs by provider** and
there is no single header present on every route (live probe, 2026-09-23,
DDK/Sandbox): `openai-model-routing` returns `x-request-id` (OpenAI's own
`req_…` format); `azure-openai-model-routing` returns `x-request-id` (a UUID)
plus `apim-request-id`; `bedrock-anthropic-model-routing` returns **no
`x-request-id` at all** (0/5 calls) and only `x-amzn-requestid`. The native
`Format=Anthropic` ingress (`ddk-anthropic-inbound`, 2026-09-24 capture) passes
Anthropic's own id through as **`request-id`** (`req_…`), again with no
`x-request-id` (#827). So both
`LastCall.from_response` and `classify()` resolve `request_id` from an ordered
list — `x-request-id`, then `x-amzn-requestid`, then `apim-request-id`, then
`request-id` — via one
shared `core/lastcall.py:request_id` helper, failing open to `None`. This is the
id a provider's support team needs; the gateway-side join key is instead
`x-correlation-id` / `correlation_id`.

As of #307, `core/lastcall.py` also parses the per-call **usage token counts**
from the response *body* (the already-VERIFIED `usage` block, §2) and surfaces
them on the same record: `input_tokens` / `output_tokens` / `total_tokens`, plus
the cost-relevant `cached_tokens` / `cache_write_tokens` (from
`usage.input_tokens_details`) and `reasoning_tokens` (from
`usage.output_tokens_details`). The Responses-API (`input_tokens*`),
Chat-Completions (`prompt_tokens*` / `completion_tokens*`), Gemini
`usageMetadata` and Anthropic Messages shapes are read. Anthropic carries its
cache counts flat on `usage`, as `cache_read_input_tokens` (→ `cached_tokens`)
and `cache_creation_input_tokens` (→ `cache_write_tokens`), and sends no total;
on a stream its `input_tokens` and cache counts arrive only on `message_start`,
nested under `message.usage` (#827, `anthropic_inbound/responses.success.body.json`).
Every count is taken **as the provider reports it**: Anthropic's `input_tokens`
*excludes* both cache counts, while OpenAI's includes `cached_tokens`, so a
cross-provider cost rollup must add Anthropic's cache counts back in. An
absent detail field (or an absent `usage` object entirely) leaves the field
`None`, **never a fabricated `0`** — a real `0` is a distinct, kept observation.
On a streamed response the counts land once the terminal SSE `usage` event has
been consumed, not at first read, and are also emitted as span attributes
(`gen_ai.usage.input_tokens`/`.output_tokens`, and `donkey.usage.cached_tokens`
/ `.cache_write_tokens` / `.reasoning_tokens` — the semconv pins no key for the
detail counts at the pinned version, so they live in the stable `donkey.*`
namespace). This is a *consumption* of the live-verified §2 shape, so it warrants
no `UnverifiedValueWarning`.

| Item | Where used | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| Per-agent attribution unit (direct proxy) | `core/config.py`, transport | VERIFIED (LIVE) | the `client_id`/`client_secret` credential pair = the agent identity; issued per client application | 2026-08-28 | live probe + `policy:list` |
| Per-agent attribution unit (model-wallet ingress, #372) | `core/config.py`, transport — SDK wiring landed in #509 | VERIFIED (LIVE) | under a **model wallet**, the caller is identified by the JWT `client_id` claim (`ddk-model-wallet-client`) **and** the wallet's `X-Client-Id` request header (`ddk-model-wallet`), matched against the wallet's `predicates` (`group=ddk` AND `client_id=…`) — not the `client_id`/`client_secret` pair. Requires an org IdP. Budget counts against the matched wallet's `modelId` (`openai:gpt-5-mini`); see the §2 "model-wallet ingress" row for the full auth shape. | 2026-09-21 | live probe — `tests/fixtures/anypoint/model_wallet/` (`wallet.definition.json`, `jwt.claims.json`, `responses.success.headers.txt` → `x-model-wallet-selected`) |
| Gateway identity on response | `core/transport/observe.py` (`x-llm-proxy-llm-provider` → `gen_ai.system`, #192); `core/lastcall.py` **parses** `x-envoy-decorator-operation` and the upstream request id (#362) | VERIFIED (LIVE) | `x-envoy-decorator-operation: api-instance-21133858.00000000-…svc`; `x-correlation-id`; `x-llm-proxy-llm-provider/-llm-model/-routing-type`. **`x-correlation-id` echo semantics (#300):** the gateway returns a client-sent `X-Correlation-Id` **verbatim** (any format: `test-12345`, a dashless `uuid4().hex`, a lowercase header name), and **mints** a dashed UUID only when the request carries none — so a dashed UUID in a capture means no id was sent, not that the gateway replaced one. Holds on gateway-generated responses too (401 bad credentials, 400) and with a client `X-Request-Id` alongside; the response `x-request-id` is the upstream's own id (row below), never an echo. See the run-correlation-id request-header row. | 2026-10-08 | `responses.success.headers.txt`; live probes 2026-10-08 (#300) against `ddk-openai-model-routing` (21177134) and `ddk-bedrock-anthropic-model-routing` (21192625) on shared-omni-gateway, `ddk-azure-openai-model-routing` (21188199) on private-space-omni-gateway |
| Upstream request id on response (per provider, #542) | `core/lastcall.py:request_id` (shared by `LastCall` + `classify()`) | VERIFIED (LIVE) | provider's own id, passed through — header name varies: OpenAI `x-request-id` (`req_…`); Azure OpenAI `x-request-id` (UUID) + `apim-request-id`; Bedrock **only** `x-amzn-requestid` (no `x-request-id`); native `Format=Anthropic` ingress **only** `request-id` (`req_…`, #827). Resolved in that order, failing open to `None`. | 2026-09-24 | live probe (DDK/Sandbox): `openai-`/`azure-openai-`/`bedrock-anthropic-model-routing`; `anthropic_inbound/responses.success.headers.txt` |
| Agent→agent egress attribution header | `core/_verify.py` → transport | VERIFIED (build) | `x-anypoint-api-instance-id` → `agent-connection-telemetry` policy `sourceAgentId`; `tracing` labels `mulesoft.api.instance.id`, `mulesoft.api.type=llm` | 2026-08-28 | built `connection.json` (§12.6) |
| Business-group attribution header name | `core/_verify.py` → transport | UNVERIFIED | not surfaced as a request header in the direct-proxy path | — | — |
| Run correlation id **request** header (`X-Correlation-Id`, #195, #522) | `core/_verify.py` `CORRELATION_ID_HEADER` → transport | VERIFIED (LIVE) | the gateway **reads** the inbound `X-Correlation-Id` and echoes it **verbatim** on the response `x-correlation-id` — a probe sending `X-Correlation-Id: ddk522-corr` got `ddk522-corr` back on both the 200 and 400 paths. So this IS the client→gateway run/trace join key. Overridable via `correlation_header` / `DONKEY_CORRELATION_HEADER`. | 2026-10-08 | live probe against `ddk-multi-route-fallback` (instance 21179672, DDK/Sandbox) on 2026-09-22; re-confirmed 2026-10-08 (#300) on both gateways and three upstreams (OpenAI, Azure OpenAI, Bedrock-Anthropic), on 200/400/401 paths. No second, multi-hop correlation header is documented on docs.mulesoft.com (Omni Gateway, Agent Fabric, Model Proxy release notes) as of 2026-10-08 |
| Per-call id **request** header (`X-Donkey-Request-Id`, #195, #522) | `core/_verify.py` `CALL_ID_HEADER` → transport | VERIFIED (LIVE) | **negative** — the gateway consumes NO inbound per-call-id header (not echoed; absent from all 10 applied system policies). It is a **client-owned** id surfaced on `DonkeyError.call_id`, stable across a request's retries; the gateway need not read it. Name is the SDK's own convention, overridable via `call_id_header` / `DONKEY_CALL_ID_HEADER`. | 2026-09-22 | live probe + `view_api_instance_policies` (instance 21179672) |
| Cost tag: team **request** header (`X-Anypoint-Cost-Team`, #196, #522) | `core/_verify.py` `COST_TEAM_HEADER` → transport | VERIFIED (LIVE) | **negative** — the LLM Gateway has NO inbound cost-tag ingestion: the name is not echoed and appears in none of the 10 applied system policies. Cost/usage is collected by the `llm-proxy-core` policy from token usage and attributed by API instance + consuming client application, never by a client header. The authoritative carrier is the `donkey.cost.team` span attribute (SDK owns the span). The `X-Anypoint-Cost-Team` name is a forward-looking, overridable (`cost_team_header` / `DONKEY_COST_TEAM_HEADER`) convention; the SDK sends the four cost headers only when `send_cost_headers` / `DONKEY_SEND_COST_HEADERS` is enabled (default off). | 2026-09-22 | live probe + `view_api_instance_policies` (instance 21179672) + `ddk-create-llm-proxy-*` system-policy set |
| Cost tag: project **request** header (`X-Anypoint-Cost-Project`, #196, #522) | `core/_verify.py` `COST_PROJECT_HEADER` → transport | VERIFIED (LIVE) | **negative** — same finding as the team row: no gateway ingestion; carrier is the `donkey.cost.project` span attribute. Overridable via `cost_project_header` / `DONKEY_COST_PROJECT_HEADER`. | 2026-09-22 | live probe + `view_api_instance_policies` (instance 21179672) |
| Cost tag: env **request** header (`X-Anypoint-Cost-Env`, #196, #522) | `core/_verify.py` `COST_ENV_HEADER` → transport | VERIFIED (LIVE) | **negative** — same finding: no gateway ingestion; carrier is the `donkey.cost.env` span attribute. Overridable via `cost_env_header` / `DONKEY_COST_ENV_HEADER`. | 2026-09-22 | live probe + `view_api_instance_policies` (instance 21179672) |
| Cost tag: enduser id **request** header (`X-Anypoint-Cost-Enduser-Id`, #196, #522) | `core/_verify.py` `COST_ENDUSER_HEADER` → transport | VERIFIED (LIVE) | **negative** — same finding: no gateway ingestion; carrier is the `donkey.cost.enduser.id` span attribute. Overridable via `cost_enduser_header` / `DONKEY_COST_ENDUSER_HEADER`. | 2026-09-22 | live probe + `view_api_instance_policies` (instance 21179672) |

The two #195 rows are **request** headers the SDK *sends* (the client→gateway
join keys behind `donkey.run()` and `DonkeyError.correlation_id`/`.call_id`). As
of #522 both are verified against the live gateway: the gateway **reads** the
inbound `X-Correlation-Id` and echoes it verbatim, so the request and response
`x-correlation-id` are the same value and a client-side log line joins to the
gateway's own record; `X-Donkey-Request-Id` is confirmed to be a **client-owned**
per-call id the gateway does not consume (it lives on `DonkeyError.call_id`).
Both names are still overridable via config for a gateway that expects different
ones, but neither is a guess and neither emits a warning.

The four #196 cost-tag rows are verified **negative** as of #522: the LLM Gateway
has no inbound cost-tag ingestion, so the `X-Anypoint-Cost-*` request-header names
are a NON-CONTRACT — the deployed proxy's applied system policies do not read
them, and cost/usage is metered by the `llm-proxy-core` policy from token usage,
attributed by API instance + consuming client application. The fixed dimensions
(`team` / `project` / `env` / `enduser.id`, set once via `Donkey.from_env(team=…)`
/ `[donkey.cost]` / `DONKEY_COST_*`, overridable per run via `donkey.run(team=…)`)
are emitted as `donkey.cost.*` **span** attributes, the authoritative carrier, and
as `X-Anypoint-Cost-*` request headers only when `send_cost_headers` /
`DONKEY_SEND_COST_HEADERS` is enabled (default off; nothing on the gateway reads
them). Because the
SDK controls the span end to end, cost attribution works in tracing regardless of
the header question; a customer whose own gateway *does* read a cost header can
point the SDK at it via the `cost_*_header` overrides.

## 4. Policy rejection response shapes (capture as fixtures, BG §1.5)

**Eight rejection shapes** are now LIVE-VERIFIED: four from the `openai-sdk` proxy
(client-id-enforcement, upstream passthrough, PII, token-rate-limit; 2026-08-28),
plus **Regex Prompt Guard** and **Azure Content Safety** — both `403` policy
blocks confirmed 2026-09-22 against the deployed provisioning proxies
`ddk-injection-guard` (instance 21179713) and `ddk-azure-content-safety`
(instance 21180957), rows added to the policy table below (#253) — plus
**Injection Protection**, confirmed 2026-09-27 against `ddk-injection-protection`
(instance 21200898, #669) — plus the **Agent Kill Switch**, confirmed 2026-09-29
against `ddk-agent-kill-switch` (instance 21206201, #694).
Fixtures in `src/donkey_kit/simulator/_fixtures/anypoint/llm_proxy/reject.*` and
`src/donkey_kit/simulator/_fixtures/rejections/reject.*`. The critical lesson:
**neither the status code nor the mere shape of the `error` value is a
sufficient discriminator** — the same nested-object envelope is emitted by both
the upstream provider AND a gateway policy (PII), and a policy block can be a
403. The authoritative discriminator is the error **`type`** plus specific
headers.

1. **client-id-enforcement (auth)** — `401`, flat `{"error":"Client ID is not
   present"}`, header `www-authenticate: Client-ID-Enforcement`.
2. **Upstream provider error, passed through** — the provider's native envelope
   verbatim. Example (bad model, valid creds): `400`,
   `{"error":{"message":"…does not exist.","type":"invalid_request_error","param":"model","code":"model_not_found"}}`.
   Nested `error` object **with** `x-llm-proxy-*` routing headers present.
   **Gemini** (native ingress, `ddk-gemini-inbound`) wraps the same object in a
   **JSON list** — `[{"error":{"code":400,"message":"…","status":"INVALID_ARGUMENT"}}]` —
   with a numeric `code` and a string `status` (no OpenAI `type`/`param`), and a
   `x-llm-proxy-model-based-routing-success: Request passed through…` header
   confirming the *upstream*, not the gateway, rejected it. `classify()` unwraps
   the first list element, maps `status`→`error_type` and `code`→`code`, and
   types it as `UpstreamRequestError` (VERIFIED LIVE 2026-09-23, #548) — not the
   `PolicyViolation` fall-through it previously landed in.
3. **PII detection** — `403` **but NOT auth**: nested object
   `{"error":{"message":"Request contains PII data: […]","type":"pii_detected"}}`,
   **no** `code`/`param`, and crucially **no** `www-authenticate` header. The
   `message` embeds a JSON list of `{"pii_type","value","start","end"}` entries.
   Re-confirmed 2026-10-07 on `ddk-pii-masking` (instance 21188402) from a full
   raw header dump, which settles the `www-authenticate` absence (#253).
4. **Token rate limit** — `429` with an **empty body** (`content-length: 0`).
   Budget state is header-only: `x-token-limit`, `x-token-remaining`,
   `x-token-reset` (**milliseconds** to reset). There is **NO** `retry-after`.
   Re-confirmed 2026-10-07 on `ddk-token-rate-limit` (instance 21188394, #253).
5. **Agent Kill Switch** — `403` **but NOT auth and NOT upstream**: nested object
   `{"error":{"code":"agent_killed","message":"This agent has been blocked by an active kill switch."}}`,
   **no** `type`, **no** `www-authenticate`, and **no** kill-reason field (#314).
   Returned when the calling agent (the JWT's `act.sub`) is quarantined in
   Governance > Security or listed in the policy's *Killed Agent IDs*; the
   upstream is never called.
6. **Bare model name on a multi-provider proxy** — `400` **but NOT a policy
   refusal and NOT upstream**: flat-string
   `{"error":"Failed to parse model from request: Model 'gpt-5-mini' is not in the known unique model map and multiple providers are configured. Use 'provider/model' format."}`,
   `content-type: application/json`, **no** `x-llm-proxy-*` headers. Model-based
   routing returns it when a bare model name is not known to exactly one of the
   configured providers; the upstream is never called. `classify()` keys on the
   gateway's sentence and returns `ModelNotRoutable` (`.model` parsed from it,
   gateway text kept in the message). VERIFIED (LIVE) 2026-10-01 on
   `ddk-multi-route-fallback` and `ddk-azure-openai-model-routing`, byte-identical
   on both (#825); fixture `reject.model-not-routable.{headers.txt,body.json}`.
7. **Request rate limit** — `429` from the stock `rate-limiting` policy (it
   counts requests, not tokens): body `{"error":"Too Many Requests"}`
   (`content-type: application/json; charset=UTF-8`), the unsuffixed
   `x-ratelimit-limit` / `x-ratelimit-remaining: 0` / `x-ratelimit-reset`
   (**milliseconds** to reset), **no** `retry-after`, **no** `x-token-*`; the
   upstream is never called. VERIFIED (LIVE) 2026-10-07 on
   `ddk-request-rate-limit` (instance 21188400, #974); fixture
   `request_rate_limit/reject.request-rate-limit.{headers.txt,body.json}`.

`core/errors.classify()` implements this (tests: `test_llm_proxy_contract.py`,
`test_rejection_contract.py`, `test_errors.py`): error `type == "pii_detected"` →
`PIIDetected` (checked *before* the 401/403→auth rule; parses `entities` from the
message); a nested error `code == "agent_killed"` → `AgentKilled`
(`policy="agent-kill-switch"`; keyed on the body code, checked *before* the auth
and generic-4xx rules so it is not mis-typed as `UpstreamRequestError`; VERIFIED
LIVE 2026-09-29, #694); a top-level `matched_patterns` list → `PromptInjectionBlocked`
(`policy="regex-prompt-guard"`); a vendor `x-llm-proxy-<vendor>-…-action: reject`
header (Azure Content Safety / Amazon Bedrock Guardrails) → `ContentSafetyBlocked`
(parses `categories` from the sibling `…-reason` header) — both of these also
checked *before* the 401/403→auth rule so a `403` moderation block is not
mis-typed as auth; header `x-injection-protection: blocked` (VERIFIED LIVE
2026-09-27 against `ddk-injection-protection`, instance 21200898, #669 — see
the [Injection Protection policy](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection)) →
`PromptInjectionBlocked` (the header, not the status, is the discriminator, so a
bare `400` is unaffected); a `429` carrying `x-ratelimit-limit` and
`x-ratelimit-remaining` but no `x-token-limit` → `RequestRateLimitExceeded`
(item 7, #974; `retry_after` from `x-ratelimit-reset`, ms→s); any other `429` →
`TokenBudgetExceeded` with `retry_after` derived from `x-token-reset` (ms→s); non-auth 4xx with a nested `error` object —
in either an OpenAI-style object envelope or a Gemini-style **list** envelope
(`[{"error":{…}}]`, #548) — → `UpstreamRequestError` (carries provider
`code`/`type`/`param`, with Gemini's `status` standing in for `type`); a non-auth
4xx whose flat-string `error` says the model "is not in the known unique model
map" → `ModelNotRoutable` (item 6, #825); `5xx` →
`UpstreamModelError`. Auth is then the verified client-id-enforcement shape: a
`401`, **or** a `403` carrying a `www-authenticate` header → `AuthError`. A `403`
**without** `www-authenticate` that matched none of the discriminators above is
therefore *not* coerced to auth — it falls through to a generic `PolicyViolation`
whose message names the observed status and any `x-llm-proxy-*` policy headers and
states the **shape is unconfirmed** (#184), rather than being mis-typed. The full
taxonomy is indexed in `src/donkey_kit/simulator/_fixtures/rejections/README.md`.

The committed rejection fixtures record only the **semantic header subset**
each discriminator needs (plus `content-type` when the body is JSON) — not
the full raw header set a live gateway response returns. A live capture
against `ddk-injection-protection` (#670) returned ~10 response headers for
the Injection Protection shape; the fixture records 2. The rest are
transport/CDN framing headers the simulator's `replay_headers()` allow-list
(`_KEEP_EXACT` / `_KEEP_PREFIX` in `simulator/fixtures.py`) drops on replay by
design, plus, in that capture, a proxy-topology header
(`x-llm-proxy-model-based-routing-success`) specific to the capturing proxy's
own routing configuration rather than to the policy — see
`src/donkey_kit/simulator/_fixtures/rejections/README.md` for the full rationale.

The Regex Prompt Guard and content-safety/guardrail shapes below are now
**VERIFIED (LIVE), 2026-09-22 (#253)** — confirmed against the deployed
provisioning proxies (`ddk-injection-guard`, `ddk-azure-content-safety`), not
only typed from the policy pages. They have **no `_verify.py` constant** —
`classify()` reads them straight from the response — so the verification record
is these rows, not an `Unverified(...)` flip:

| Shape | HTTP | Discriminator | Maps to | Source |
|---|---|---|---|---|
| Injection Protection | `400` | header `x-injection-protection: blocked` (not the status) | `PromptInjectionBlocked` (`policy="prompt-injection-protection"`) | [Injection Protection policy](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection) (v1.12.0); VERIFIED (LIVE) 2026-09-27 on `ddk-injection-protection` (instance 21200898, #669) — real 79-byte body |
| Regex Prompt Guard | `403` | top-level `matched_patterns` list (flat-string `error`) | `PromptInjectionBlocked` (`policy="regex-prompt-guard"`) | [Regex Prompt Guard policy](https://docs.mulesoft.com/gateway/latest/policies-included-regex-prompt-guard) (v1.11.4); VERIFIED (LIVE) 2026-09-22 on `ddk-injection-guard` (instance 21179713) |
| Content safety / guardrails | `403` | `x-llm-proxy-azure-content-safety-action` / `x-llm-proxy-bedrock-guardrail-action` == `reject`; reasons in the sibling `…-reason` header | `ContentSafetyBlocked` (parses `categories`) | [Azure Content Safety policy](https://docs.mulesoft.com/gateway/latest/policies-included-azure-content-safety) (v1.13.0); [Amazon Bedrock Guardrails policy](https://docs.mulesoft.com/gateway/latest/policies-included-bedrock-guardrails) (v1.13.0); VERIFIED (LIVE) 2026-09-22 on `ddk-azure-content-safety` (instance 21180957) + 2026-09-24 on `ddk-bedrock-guardrails` (#568) |
| Unrecognised refusal (fall-through) | any non-429 `4xx` | matches **none** of the discriminators above; no nested `error` envelope; no `www-authenticate` (e.g. an unrecognised `403`) | generic `PolicyViolation` (`policy="unknown"`), message says **shape unconfirmed** and names the observed status + `x-llm-proxy-*` headers | UNVERIFIED — no known contract by design (#184); federated-guardrail verdicts tracked in #305 |

The Injection Protection (row 3), Regex Prompt Guard (row 7), and Content
Safety (row 8) shapes are now LIVE-captured — Injection Protection on
2026-09-27 against `ddk-injection-protection` (instance 21200898, #669), Regex
Prompt Guard and Azure Content Safety on 2026-09-22 (#253), and Bedrock
Guardrails on 2026-09-24 (#568), the sibling vendor of the same row-8
`…-action: reject` family. Still **uncaptured**: any **unrecognised
content-moderation / federated-guardrail** shape (row 4 — the fall-through).
Rather than guess a type for it, `classify()` surfaces any such unrecognised
refusal **honestly** — a `PolicyViolation` whose message states the shape is
unconfirmed and whose remediation asks the operator to file the observed
status/headers/body so the shape can be typed (#184). This is the last row
above; it is deliberately **not** an `AuthError`, even for a `403`, once the
verified `www-authenticate` auth shape is excluded. The fall-through is a
standing catch-all, not a pending contract: no capture can verify "any
unrecognised shape", so it closes nothing and blocks nothing. A specific
vendor verdict that turns up is captured and typed on its own issue; the
federated guardrails (Akamai, CrowdStrike, Google Armor) are tracked in #305.

**Budget window emission — two forms, keyed on outcome not status class
(LIVE-VERIFIED).** The gateway signals its token-rate-limit window in two
distinct shapes depending on the response outcome, **not** on the status class:

| Response | Budget signal | Reset unit | Status |
|---|---|---|---|
| `200` success, policy applied | `x-llm-proxy-ratelimit`, one prose sentence | milliseconds | VERIFIED (LIVE) |
| `403` refusal (e.g. PII) | `x-llm-proxy-ratelimit`, one prose sentence | milliseconds | VERIFIED (LIVE) |
| `429` limit exceeded | `x-token-limit` / `x-token-remaining` / `x-token-reset` (item 4 above) | milliseconds | VERIFIED (LIVE) |

On a `200` and on a `403` (with a `llm-token-rate-limit` policy applied) the
window arrives as a **single prose header**, not the numeric trio:

```
x-llm-proxy-ratelimit: Token rate limit: 10000 tokens remaining of 10000 limit. Reset in 56711ms.
```

The reset is in **milliseconds** (`…ms`). The committed `403` PII capture
(`reject.pii-detected.headers.txt`) carries the same header in the same sentence
shape, and the `openai-sdk` API lists `x-llm-proxy-ratelimit` in its CORS
`exposedHeaders`; the existing `responses.success.headers.txt` capture lacks it
only because it predates the policy being applied.

**Distinct from the gateway window: the upstream provider's quota passthrough.**
The `x-ratelimit-limit-tokens` / `x-ratelimit-remaining-tokens` /
`x-ratelimit-reset-tokens` headers on a `200` (and their `-requests` siblings)
are the **upstream provider's** own quota, passed straight through — not the
gateway's budget. Their reset values are Go-style **duration strings** (`0s`,
`12ms`), **not** integer milliseconds, so they must not be parsed with the
`x-token-*` / `ms` rule. The SDK does not read them. They are also not the
gateway's request window below, which uses the **unsuffixed** names
`x-ratelimit-limit` / `-remaining` / `-reset`.

**Request window emission (LIVE-VERIFIED 2026-10-07, #974).** The stock
`rate-limiting` policy counts requests per window. With `exposeHeaders: true`
it sends one numeric trio on **every** response, success and refusal alike:

| Response | Request-window signal | Reset unit | Status |
|---|---|---|---|
| `200` success (streaming too) | `x-ratelimit-limit` / `x-ratelimit-remaining` / `x-ratelimit-reset` | milliseconds | VERIFIED (LIVE) |
| `429` limit exceeded | the same trio, `remaining: 0` (item 7 above) | milliseconds | VERIFIED (LIVE) |

`Budget.observe()` fills `donkey.budget.requests` (a `RequestWindow`) from the
trio, and `Budget.pace()` refuses when either window has reached the reserve.
Probed on `ddk-request-rate-limit` (instance 21188400, 3 requests per
`client_id` per 60000 ms, model-based to `azureopenai`/`gpt-5-mini`): three
`200`s with `remaining` `2`/`1`/`0` and a reset counting down with the wall
clock, then `429`s, then `remaining: 2` after the reset. The window is fixed
and wall-clock aligned: the first request does not start it, and two observed
windows ended on the same second-of-minute. Fixtures in
`src/donkey_kit/simulator/_fixtures/anypoint/request_rate_limit/` (the served
`429`) and `tests/fixtures/anypoint/request_rate_limit/` (the `200`s, the
repeat `429`, the post-reset `200`). The probe was a hand-written `httpx`
script, not `scripts/capture_fixture.py`.

Still **UNVERIFIED**:

- **`exposeHeaders: false`.** The policy then sends no `x-ratelimit-*` header,
  so the `429` has no header to key on. That shape is not captured, and
  `classify()` maps it to `TokenBudgetExceeded`, as for any unmarked `429`.
  The body `{"error":"Too Many Requests"}` was seen only with the headers on,
  so it is not used as a discriminator.
- **Semantic-cache hits.** Whether a cache hit counts against the request
  window is not probed. `Budget.observe()` skips a hit for both windows.
- **Other window units and clustering.** Only one 60 s window on one gateway
  replica was probed.

**Simulator budget window (corrected, #353).** Resolved (#354): a live probe
confirms the production proxy **does** carry its budget window on a `200` success
once a `llm-token-rate-limit` policy is applied — as the prose
`x-llm-proxy-ratelimit` header above, **not** the numeric `x-token-*` trio (that
trio appears only on the `429`). The local gateway simulator (`donkey mock`,
BG §1.4) now renders exactly this prose sentence — `Token rate limit: {remaining}
tokens remaining of {limit} limit. Reset in {ms}ms.` — from a monotonically
decreasing counter on its happy-path `200` (and streaming `200`), so the simulator
matches the observed live contract rather than the earlier assumption. The former
synthesised numeric `x-token-*` overlay is removed (#353); it diverged from the
gateway on the exact header names a consumer parses, and `Budget.observe()` (with
the prose parser from #352) now populates identically from a simulated or a live
`200`. Nothing in `core/`/`llm/` depends on the simulator emitting it; only
`simulator/app.py` (`SimulatorConfig`) renders it.

**Model-wallet budget emission (LIVE-VERIFIED 2026-09-30, #301).** A model
wallet's budget is a **separate mechanism** from the token window above. The
platform auto-attaches two policies to every matching model proxy —
`mw-find-key-policy` (the wallet objects, verbatim) and
`mw-token-rate-limit-policy` — and neither is the `llm-token-rate-limit` policy
that emits the `x-token-*` trio or the prose `x-llm-proxy-ratelimit`. Probed by
driving `ddk-model-wallet` (2000 tokens/day on `openai:gpt-5-mini`) on instance
21189395 to refusal: 45 consecutive `200`s, then `429`s. Fixtures in
`tests/fixtures/anypoint/model_wallet/` (`probe.exhaustion-run.json`,
`responses.exhaustion-run.{first,last}.headers.txt`,
`reject.wallet-exhausted.*`, `mw-policies.json`); pinned by
`test_model_wallet_contract.py`.

| Item | Where used | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| Wallet state in-band on a `200` | `core/budget.py` (not consumed — nothing to consume) | VERIFIED (LIVE) | **negative** — the only wallet header is `x-model-wallet-selected: <wallet clientId>`. No limit / remaining / spent value, no currency, and neither the `x-token-*` trio nor `x-llm-proxy-ratelimit`. The header set was identical on all 45 `200`s. So like the token window (upstream gap #248), wallet state is not queryable in-band either; a wallet's first observable signal is its refusal. | 2026-09-30 | `probe.exhaustion-run.json`, `responses.exhaustion-run.{first,last}.headers.txt` |
| Spend denomination | — (#315) | VERIFIED (LIVE) for tokens; doc-read for spend | Chosen **per budget**: `resource: tokens` or `spend` in the omni `model-wallet` API (UI "Metric": Tokens / Spend (USD)); the docs name USD as the only currency. The gateway materialises a tokens budget as `tokenRateLimits[] {limit, timeWindow: day\|week\|month, provider, model}` in `mw-find-key-policy`. **Nothing on the wire reports spend** for either metric. The only money signal is the per-model **rate** (`x-llm-proxy-input-tokens-cost-per-1m` / `-output-tokens-cost-per-1m`), which is a price, not a running total. This agrees with the #315 constraint: the SDK must not compute cost locally. A `spend` budget's refusal shape was **not** exercised live. | 2026-09-30 | `mw-policies.json` + [model wallets doc](https://docs.mulesoft.com/general/exp-model-wallets-manage#add-a-budget-to-a-wallet) |
| Claim dimensions a wallet keys on | — (#315) | VERIFIED (LIVE) | No fixed dimension set. `predicates[]` are `{type: "jwtClaim", claim: <any claim key>, values: [...]}`, AND-ed, and `jwtClaim` is the only `type` observed. "Employee", "user", "team" and "cost centre" are whatever claims the IdP emits (DDK: `group`, `client_id`). When several wallets match, the highest `priority` wins. | 2026-09-30 | `mw-policies.json`, `wallet.definition.json` |
| Pre-refusal threshold warning | — (#315) | VERIFIED (LIVE) | **negative** — no warning on the wire. The header keys on the last `200` before the refusal match the first `200` exactly, and neither `mw-` policy config has a threshold / alert setting. The roadmap's "Budget Alerts for Model Wallets" is not an in-band signal today. Budgets are approximate: this run was refused only after 3403 response `total_tokens` against the 2000 limit. | 2026-09-30 | `probe.exhaustion-run.json`, `mw-policies.json` |
| Wallet-exhaustion refusal | `core/errors.classify()` (generic `429` branch → `TokenBudgetExceeded`); a typed sibling is #315 | VERIFIED (LIVE) | `429`, body `{"error":"token rate limit exceeded"}` served as **`content-type: text/plain`**, **`retry-after: 86400`** (seconds; identical on two refusals 1 s apart, i.e. equal to the `day` window length — whether it counts down to the actual reset is unconfirmed), `x-model-wallet-selected`, and the routing headers. There are **no** `x-token-*` headers. **Distinguishable** from the token-window `429` (item 4 above: empty body, the `x-token-*` trio, no `retry-after`) by `x-model-wallet-selected` plus the missing `x-token-*` headers. Today `classify()` returns `TokenBudgetExceeded(retry_after=86400.0)`, and `Budget.observe()` makes no change. The upstream is not called. If a fallback route exists, the proxy reroutes instead of refusing (doc). | 2026-09-30 | `reject.wallet-exhausted.{headers.txt,body.json}` (correlation id `00000000-0000-4000-8000-fec05f757155`) |

| Policy | Exchange asset (verified) | Status | Rejection shape | Date | Source |
|---|---|---|---|---|---|
| client-id-enforcement | `client-id-enforcement` `1.3.3` | VERIFIED (LIVE) | `401` + `www-authenticate: Client-ID-Enforcement`, `{"error":"Client ID is not present"}` | 2026-08-28 | live probe |
| model-based-routing / upstream | `model-based-routing` `1.0.3` | VERIFIED (LIVE) | passthrough of provider error (OpenAI `400 model_not_found` object) | 2026-08-28 | live probe |
| model-based-routing, bare model name, more than one provider | applied to `ddk-multi-route-fallback`, `ddk-azure-openai-model-routing` | VERIFIED (LIVE) | gateway's own `400`, flat-string `{"error":"Failed to parse model from request: Model '…' is not in the known unique model map and multiple providers are configured. Use 'provider/model' format."}`, no `x-llm-proxy-*` headers, upstream not called → `ModelNotRoutable` (item 6 above) | 2026-10-01 | live probe (#825) |
| LLM proxy core | `llm-proxy-core` `1.0.5` | applied VERIFIED (LIVE) | on `openai-sdk`; rejection body not yet triggered | 2026-08-28 | `policy:list` |
| Request rate limiting | `rate-limiting` `1.5.1` (impl `rate-limiting-flex` `1.2.2`), `exposeHeaders: true` | VERIFIED (LIVE) | `429`, body `{"error":"Too Many Requests"}`, the unsuffixed `x-ratelimit-limit`/`-remaining`/`-reset`(ms) trio, no `retry-after`, no `x-token-*`; the same trio on every `200` → `RequestRateLimitExceeded` (item 7 above). See "Request window emission" above. | 2026-10-07 | live probe, `ddk-request-rate-limit` instance 21188400 (#974) |
| Token rate limiting | interface `llm-token-rate-limit` `1.0.2` (impl `-policy-flex` `1.0.4`) | VERIFIED (LIVE) | Two emission forms: `429` limit-exceeded → **empty body**, numeric trio `x-token-limit`/`x-token-remaining`/`x-token-reset`(ms), no `retry-after`; `200`/`403` under the same policy → the window as prose in a single `x-llm-proxy-ratelimit` header (ms reset). See "Budget window emission" above. | 2026-08-28; re-confirmed 2026-10-07 | applied + live probe; re-probe on `ddk-token-rate-limit` (instance 21188394, #253) |
| PII detection | interface `llm-pii-detection-policy` `1.0.0` (impl `-flex` `1.0.2`) | VERIFIED (LIVE) | `403`, nested `{"error":{message,type:"pii_detected"}}`, no `www-authenticate` | 2026-08-28; re-confirmed 2026-10-07 | applied + live probe; raw header dump on `ddk-pii-masking` (instance 21188402, #253) |
| Regex Prompt Guard | `regex-prompt-guard-policy` `1.0.0` | VERIFIED (LIVE) | `403`, flat-string `error` + top-level `matched_patterns` list; body matched the committed fixture byte-for-byte | 2026-09-22 | live probe, `ddk-injection-guard` instance 21179713 |
| Azure Content Safety | `azure-content-safety-policy` `1.0.0` | VERIFIED (LIVE) | `403`, `x-llm-proxy-azure-content-safety-action: reject` + `-phase` + `-reason` headers; body `{"error":…,"categories":[…]}` (categories prompt-dependent, e.g. `severity_hate,severity_violence`, `prompt_shield`) | 2026-09-22 | live probe, `ddk-azure-content-safety` instance 21180957 (private-space gateway) |
| Agent Kill Switch | applied to `ddk-agent-kill-switch` | VERIFIED (LIVE) | `403`, nested `{"error":{code:"agent_killed",message}}` — no `type`, no `www-authenticate`, no reason field (#314). Discriminator: nested `error.code == "agent_killed"` → `AgentKilled` | 2026-09-29 | live probe, `ddk-agent-kill-switch` instance 21206201 (shared-omni-gateway), quarantined agent `act.sub=21206128`, correlation id `00000000-0000-4000-8000-ede8f2783ba7` (#694) |
| Model wallet budget | `mw-find-key-policy` `1.0.3` + `mw-token-rate-limit-policy` `1.0.0`, auto-attached, not applied by hand | VERIFIED (LIVE) | `429`, flat-string `{"error":"token rate limit exceeded"}` (`text/plain`), `retry-after: 86400`, `x-model-wallet-selected`; no `x-token-*`. See "Model-wallet budget emission" above. | 2026-09-30 | live probe, `ddk-model-wallet` instance 21189395 (#301) |

**Apply note (verified):** these LLM policies apply against the schema-bearing
**interface** asset id/version (`llm-token-rate-limit` `1.0.2`,
`llm-pii-detection-policy` `1.0.0`) — NOT the `-policy-flex` impl asset, which
errors `Policy Template is missing required files: [schema]`. Config property
names (from `api-mgr:policy:describe <interface> --policyVersion <v> -o json`,
`configuration[]`): token-rate-limit = `maximumTokens` (int≥1),
`timePeriodInMilliseconds` (int≥1000), `keySelector` (DW expr, e.g.
`#[attributes.headers['client_id']]`); PII = `entities` (enum: `Email`,
`US SSN`, `Credit Card`, `Phone Number`), `customPatterns` (`{name,pattern}[]`),
`action` (enum: `Reject`, `Log`, `Log and mask`; default `Log` — only `Reject`
blocks). Both were applied to `21133858` for capture, then removed.

## 5. MCP Bridge / Agent Network provisioning — **gates whether §5 is viable at all**

FINDING (CLI, 2026-08-28): provisioning is delivered as a **Maven-project + CLI**
flow via the `mulesoft-anypoint-cli-agent-fabric-plugin` (v1.0.11), branded
**"Agent Network"**, NOT a clean REST CRUD. The deploy target is a **Private
Space** running **Flex Gateway** with a paired **ingress + egress** gateway.
This reshaped §5: a declarative `donkey.yaml` plan/apply would have had to wrap
this CLI/Maven toolchain or emit its project layout, not invent a REST
provisioning API. That control plane is refused, and its scaffolding was
deleted in #730 (ADR 0008 in `docs/adr/`); the findings below stay as the record. Exact REST calls behind the CLI are now recorded in §12
(static analysis of plugin v1.0.11 + `anypoint-cli-command` 1.6.8).

| Item | Status | Finding | Date | Source |
|---|---|---|---|---|
| Provisioning surface exists? | VERIFIED (CLI) | Yes — `agent-network setup gateways` + `agent-network project create/build/deploy/publish` | 2026-08-28 | `anypoint-cli-v4 agent-network --help` |
| Deploy topology | VERIFIED (CLI) | ingress-gw (`agent-network-ingress-gw`) + egress-gw + target Private Space (`agent-network-space`) | 2026-08-28 | `agent-network:setup:gateways --help`, `:project:deploy --help` |
| Gateway technology | VERIFIED (CLI) | Flex Gateway (API Type `flexGateway`, gateway v1.13.2) | 2026-08-28 | `api-mgr:api:describe 21121315` |
| Project is Maven-based (JVM needed) | VERIFIED (CLI) | `project build` uses `mvnw`; GAV = group-id/asset-id/asset-version (default 1.0.0) | 2026-08-28 | `agent-network:project:create --help` |
| Runtime property injection | VERIFIED (CLI) | `deploy --property name:value` (example given: `apiKey:sk-xxx`) | 2026-08-28 | `agent-network:project:deploy --help` |
| Exact REST endpoints behind the CLI | VERIFIED (plugin) | gatewaymanager / runtimefabric / apimanager / amc / proxies / exchange paths + bodies | 2026-08-28 | §12.3–§12.5 |
| `mulesoft/anypoint` Terraform provider coverage | UNVERIFIED | not needed if CLI/Maven path adopted | — | — |

## 6. Governance / local-mode (the Verification milestone)

| Item | Gates | Status | Finding | Source |
|---|---|---|---|---|
| Can Local Mode run the LLM Proxy? | the Verification milestone | UNVERIFIED | A stock Flex Gateway 1.14.0 Local Mode image rejects `llm-proxy-core`, `model-based-routing`, and `openai-transcoding-policy` as missing extensions; bounded to that image version, not a full-capability verification (#651, #661) | 2026-09-27 |
| Can Local Mode run MCP Bridge? | the Verification milestone | UNVERIFIED | Same stock image rejects `mcp-support` as a missing extension; bounded to that image version (#651, #661) | 2026-09-27 |
| Does Local Mode need a control-plane licence/registration artifact? | OSS/CI viability | UNVERIFIED | Yes — the gateway refuses API configuration until registered to a control plane, so OSS contributors/CI without an Anypoint org cannot run it (#651, #661) | 2026-09-27 |
| Which policies are Connected-Mode-only? (portability table) | the Verification milestone | UNVERIFIED | — | — |
| Is "deployed to gateway" readable per API instance? | `require_deployed` | VERIFIED (CLI) | Yes — `api-mgr:api:list` (per env) + `:api:describe <id>` returns Endpoint URI, gateway, deployment target | 2026-08-28 |
| Are applied policies readable per API instance? | governed-state join | VERIFIED (CLI) | Yes — `api-mgr:policy:list <id>` returns `{ID, Template ID, Asset ID, Asset Version, Label, Status, Configuration}` | 2026-08-28 |
| Are governance ruleset results exposed via API? | `require_governance_pass` | PARTIAL | `governance:api` evaluates rulesets; `governance:profile:*` manages profiles (ruleset refs = Maven GAV). Result-read shape pending | 2026-08-28 |
| Can applied policies be fetched in bulk per environment? | governed-only discovery perf | UNVERIFIED | per-instance confirmed; bulk endpoint pending plugin analysis | — |
| MCP-specific + enforcement policies observed | the Verification milestone (portability) | VERIFIED (CLI) | `mcp-support` (`injectMcpNameHeaders`), `client-id-enforcement` (client_id/client_secret headers), `header-injection` (`x-gateway-token`) | 2026-08-28 |
| Governed MCP endpoint URL shape | `McpServerHandle.endpoint_url` | VERIFIED (CLI) | ingress gw: `https://agent-network-ingress-gw-<id>.<region>.cloudhub.io/mcp/<name>/` | 2026-08-28 |
| Governed **LLM proxy** policy stack (live) | governed-state join, the Verification milestone | VERIFIED (LIVE) | on `openai-sdk`: `cors 1.3.2`, `dataweave-headers-transformation 1.0.0`, `client-id-enforcement 1.3.3`, `llm-proxy-core 1.0.5`, `model-based-routing 1.0.3`, `openai-transcoding-policy 1.0.3` — all Enabled | 2026-08-28 |

**Decision (2026-09-27, #661): Donkey Development Kit does not support Omni/Flex
Gateway Local Mode.** The rows above stay `UNVERIFIED` for full Local Mode
capability — this is a scope decision, not a completed verification — but the
bounded findings against a Flex Gateway 1.14.0 stock image are sufficient to
make the call: the LLM Proxy and MCP Bridge policies this SDK depends on are
rejected as missing extensions, and the gateway will not accept configuration
without control-plane registration. The pure-Python local gateway simulator
(`donkey mock` / `simulate()`, BG §1.4, the `local_gateway` pytest marker) is
DDK's supported local dev loop; real-gateway behavior is exercised against a
hosted (Connected Mode) proxy. See `docs/unsupported-boundary.md` for the
consumer-facing statement of this same decision, and #65 for the verification
questions this decision resolves as out of scope rather than answered.

## 7. Publication / Exchange (BG §2.5)

| Item | Gates | Status | Finding | Source |
|---|---|---|---|---|
| First-class Exchange asset types for MCP servers & AI agents? | BG §2.5 + `asset_types` filter (governed-only discovery) | VERIFIED (CLI) | Yes — Exchange assets named "… MCP Server" and "… Agent Network"/agent, managed as API Manager instances | 2026-08-28: `api-mgr:api:list` |
| Exact publication / discovery asset-type tokens (`mcp`, `a2a-agent`, `agent`, `api`) | `PublicationAssetType`, `AssetRef.type`, `ExchangeRegistry.search(asset_types=)` | UNVERIFIED | SDK-local assumption only: publication currently reuses the discovery `AssetType` Literal values; the direct publication and search contracts remain blocked pending capture | — |
| Publication mechanism for non-Mule assets (REST / CLI / Maven)? JVM needed? | §7 CI story | VERIFIED (CLI) | Maven + CLI: `agent-network project publish` publishes the built project to Exchange; **JVM required** (`mvnw`) | 2026-08-28: `agent-network:project:publish --help` |
| Publication uses Maven GAV coordinates | BG §2.5 | VERIFIED (CLI) | group-id/asset-id/asset-version; groupId defaults to org id | 2026-08-28: `:project:create --help`, `api-mgr:api:describe` |
| Documentation pages publishable? | BG §2.5 | PARTIAL | `exchange asset page` + `exchange asset resource` topics exist | 2026-08-28: `exchange:asset --help` |
| Can arbitrary metadata/tags be attached (content digest)? | BG §2.5 | UNVERIFIED | Tags field exists on instances (empty here); attach mechanism pending | — |
| Asset lifecycle states (draft/published/deprecated) via API? | `require_lifecycle`, BG §2.5 | PARTIAL | `Deprecated` + `Public` flags on instances; A2D showed `status: draft\|published` | 2026-08-28 |
| Native descriptor formats (MCP manifest, A2A card) vs file attach? | BG §2.5 output | VERIFIED (plugin) | typed Exchange files w/ fixed classifiers (`agent-metadata`, `mcp-metadata`, `llm-metadata`, `a2a-card`, `schema`, `mule-application`) attached to `agent-network` root asset | 2026-08-28 | §12.7 |

## 8. Framework APIs (BG §1.8) — re-verify every constructor

"Confirmed offline" below means exactly what `verify_frameworks.py` Check A checks
(#681): the adapter's factory constructs the native object without raising, and
that object is an instance of the class the script's `FRAMEWORKS` table records
for the framework — normally the constructor named in this table's Symbol
column. CrewAI is the exception: `crewai.LLM` is a factory that returns a
different class, so the harness checks against the concrete class it returns
(see its row). An `ImportError` during construction counts as a failure
whenever the framework's distribution is installed; only a missing distribution
reads as not installed.
It does **not** mean each named kwarg was validated as a genuine accepted
parameter — a pydantic/LiteLLM-backed constructor (or Strands' opaque
`client_args={...}` dict, forwarded rather than named) can silently absorb an
unknown or renamed kwarg via `**kwargs`/`model_kwargs`/`additional_params`
without raising. Per §0.3, `VERIFIED` on this table is reserved for a
real-sandbox round-trip; every row below that has not had one reads
UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off),
regardless of which issue/PR produced the offline pass.

| Framework | Symbol / kwarg | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| LangGraph | `langchain_openai.ChatOpenAI(model, base_url, api_key, default_headers, http_async_client, max_retries=0, use_responses_api=True)` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` against the recorded path confirmed offline with `langgraph==1.2.12`, `langchain-openai==1.6.6`, `langchain-core==1.6.5`. The deep adapter (#198) retains `use_responses_api=True` for the live-verified `/responses` data plane (§2); overridable via `chat_model(..., use_responses_api=False)`. Tested with `openai==2.54.0`; no live round-trip. | 2026-09-27 | #34; `python/scripts/verify_frameworks.py --only langgraph` (also `--emit-verified`) |
| Google ADK | `google.adk.models.lite_llm.LiteLlm(model="openai/…", api_base, api_key, extra_headers, max_retries=0, client)` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` against the recorded path confirmed offline with `google-adk==2.10.0`, `litellm==1.102.1`. The pre-built OpenAI `client` sends through the SDK's shared client (#740), so every call carries the run's correlation id and `donkey.last_call` is `OBSERVED` after a call; this row does not verify request/header forwarding live. A refusal surfaces as a LiteLLM error with the openai `APIStatusError` in its `__cause__` chain; the SDK does not convert it to a typed error. Tested with `openai==2.54.0`; no live round-trip. `max_retries=0` goes to LiteLLM, which sets it on `client` per call (default 2); one send per budget 429 confirmed by request count against a loopback proxy with `litellm==1.103.2` (#734, `tests/unit/test_framework_retries.py`). | 2026-09-27 | #34; `python/scripts/verify_frameworks.py --only adk` (also `--emit-verified`) |
| Google ADK (native Gemini) | `google.adk.models.Gemini(model, base_url, client_kwargs={api_key, http_options={base_url, api_version="", headers, timeout, httpx_async_client}})` | VERIFIED (LIVE) | Round-trip through a `Format=Gemini` proxy with `google-adk==2.10.0`, `google-genai==2.25.0`: `generate_content_async` → 200, SSE streaming, and a tool-calling agent run. `client_kwargs` **replaces** ADK's default `http_options` wholesale, so the adapter passes the full set; an injected `httpx_async_client` disables genai's aiohttp path, so buffered and streamed calls both go through the shared `DonkeyAsyncClient`. `api_key` is required by genai and sent as `x-goog-api-key` (gateway-ignored placeholder). `timeout` is milliseconds (`None` would disable it). genai raises its own `google.genai.errors.APIError` whose `.response` is the httpx response, so `classify(exc.response)` works. `Gemini.client_kwargs` first appears in google-adk **2.4.0** (absent in 2.0.0 and 2.3.0, checked 2026-10-01); ADK's pydantic config ignores unknown fields, so on older versions the governed client is dropped silently. The `adk` extra floors at `google-adk>=2.4`, `gemini()` refuses a `Gemini` lacking either field with `_verify.blocked(...)`, and the round-trip test runs in CI at 2.4.0 and the newest release (#735). ADK caches one genai `Client` per event loop, and the shared `DonkeyAsyncClient` keeps one connection pool per loop, so one Donkey serves repeated `asyncio.run(...)` calls (#807). | 2026-10-01 | #691, #735; `python/scripts/verify_frameworks.py --only adk.gemini --live` against `ddk-gemini-inbound` |
| MS Agent Framework | `agent_framework.openai.OpenAIChatClient(model, base_url, api_key, default_headers)` — the **Responses API** client (`POST /responses`), built by `chat_client(..., api="responses")`; `agent_framework.openai.OpenAIChatCompletionClient(…)` with the same kwargs — the **Chat Completions** client (`POST /chat/completions`), built by `chat_client()` by default (#1043) | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` against both recorded paths confirmed offline with `agent-framework==1.19.0` / `agent-framework-openai==1.14.4`. The constructor kwarg is `model=` — `model_id` is **not** accepted. `base_url`/`api_key`/`default_headers`/`async_client` are accepted by both, so `connection_kwargs()` serves either. Both chain the openai error as `__cause__` of `ChatClientException` (`OpenAIContentFilterException` for a content-filter 400), so `policy_middleware()` covers both. The default is `/chat/completions` (#1043), the only route every upstream serves (§2 per-upstream route matrix, #894); `/responses` is the opt-in for OpenAI-routed proxies. The switch traces back to an Azure OpenAI route that answered `/responses` with **404** `Resource not found` and `/chat/completions` with 200 in one live probe of `ddk-azure-openai-model-routing` (2026-09-30, #826); re-captured 2026-10-07 as the §2 fixture `src/donkey_kit/simulator/_fixtures/anypoint/azure_openai_routing/` (#896, moved in #895); the `/chat/completions` route itself is recorded in §2 (Chat Completions row). Also confirmed offline at the `agent-framework==1.13.0` extras floor, where the call reaches the proxy (`test_framework_redirects.py`). 1.12.0 constructs but sends elsewhere (§8.3, #743). No live round-trip of either client. (Reclassified from a bare `VERIFIED` per #681 — same offline-only evidence class as its peer rows; §0.3 reserves `VERIFIED` for a real-sandbox round-trip.) | 2026-10-01 | #520, #826 (`verify_frameworks.py --only agent_framework`) |
| MS Agent Framework (middleware) | `agent_framework.chat_middleware` decorator; `ChatContext.stream` / `.result`; `ResponseStream.with_pull_context_manager(cm_factory)`; `agent_framework.exceptions.ChatClientException` chaining the openai error as `__cause__` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Confirmed offline with `agent-framework==1.19.0`: `Agent(middleware=[...])` accepts a `@chat_middleware`-marked function (an undecorated `(context, next)` function whose first parameter is not annotated `ChatContext` raises `MiddlewareException: Cannot determine middleware type`). `OpenAIChatClient._handle_request_error` re-raises every openai error as `ChatClientException` from the original, so `policy_middleware()` runs the call inside the typed-refusal bridge (`core/refusals.py`, #724), whose chain walk reaches the `openai.APIStatusError` and classifies its response in core; the adapter's `refusal_translator` sees through `agent_framework.exceptions.AgentFrameworkException` (the base of `ChatClientException`, confirmed offline with `agent-framework-core==1.20.0`), so an unreachable gateway surfaces as `GatewayUnavailable`. A streamed refusal surfaces on the caller's pull, after the middleware has returned; `with_pull_context_manager` wraps each pull, so the same conversion applies. Pinned by `test_agent_framework_policy_middleware.py` on a real `Agent` (one send, typed `PIIDetected` with the run's correlation id, streaming and non-streaming). No live round-trip. | 2026-10-01 | #739 |
| OpenAI Agents SDK | `agents.OpenAIChatCompletionsModel(model, openai_client=openai.AsyncOpenAI(base_url, api_key, default_headers, http_client, max_retries=0))` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` against the recorded path confirmed offline with `openai-agents==0.20.0`. Tested with `openai==2.54.0`; no live round-trip. | 2026-09-27 | #34; `python/scripts/verify_frameworks.py --only openai_agents` (also `--emit-verified`) |
| Anthropic SDK | `anthropic.AsyncAnthropic(base_url, api_key, default_headers, http_client, max_retries=0)` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` against the recorded path confirmed offline with `anthropic==0.116.0`, and again with `anthropic==0.125.0` and `anthropic==1.9.0` (`httpx2==2.13.1`) after #701. `http_client` is the shared `DonkeyAsyncClient`'s non-owning view on `anthropic<1` (#733) and a bridged `httpx2.AsyncClient` on `anthropic>=1.0` (§8.1). Model id remains per-call; the separately live-verified Anthropic Messages ingress (§2, #304) requires a `Format=Anthropic` proxy. Tested with `openai==2.54.0` (0.116.0) and `openai==3.22.1` (1.9.0); no live round-trip. | 2026-09-30 | #34, #701; `python/scripts/verify_frameworks.py --only anthropic` (also `--emit-verified`) |
| CrewAI | `crewai.LLM(model="openai/…", base_url, api_key, extra_headers, max_retries=0, interceptor)` → returns `crewai.BaseLLM` (concretely `OpenAICompletion`) | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` confirmed offline with `crewai==1.15.22`, `openai==2.54.0` — against the concrete `OpenAICompletion` class the factory returns, **not** `crewai.LLM`. `crewai.LLM.__new__` is a documented factory: an `openai/`-prefixed model with an explicit `base_url` (which `connection_kwargs()` always supplies) routes to and returns `crewai.llms.providers.openai.completion.OpenAICompletion`, CrewAI's native OpenAI provider and a sibling `crewai.BaseLLM` subclass, not a `crewai.LLM` instance (`isinstance(obj, crewai.LLM)` is false; `isinstance(obj, crewai.BaseLLM)` is true). That provider strips the `openai/` prefix and does not go through LiteLLM, which is only the factory's fallback route. `crewai.BaseLLM` (`crewai.llms.base_llm.BaseLLM`, a public top-level export) is the correct common return type and the adapter's annotation, but every provider is one, so the harness pins the concrete `OpenAICompletion` class: a routing change fails the check instead of passing as another `BaseLLM` (#640). The prior `crewai.LLM` expectation and its `CLASS RENAMED` verdict were an artifact of checking against the wrong class, not a real incompatibility. `extra_headers` is not a named `OpenAICompletion` field: `BaseLLM` collects it into `additional_params`, which `OpenAICompletion` merges into its request parameters, so the proxy auth headers arrive by that passthrough rather than a named parameter — the silently-absorbed-kwarg case above (seen on the wire against a local stub server, sync and streaming; not a sandbox round-trip). `max_retries=0` reaches the provider's OpenAI client (a 503 is sent once); CrewAI's own `BaseLLM` rate-limit retry still sends a budget 429 3 times and has no off switch, an asserted exemption confirmed with `crewai==1.15.23` (#734, `tests/unit/test_framework_retries.py`). | 2026-09-27 | #640; `python/scripts/verify_frameworks.py --only crewai` (also `--emit-verified`) |
| LlamaIndex | `llama_index.llms.openai_like.OpenAILike(model, api_base, api_key, default_headers, is_chat_model=True, is_function_calling_model=True)` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` against the recorded path confirmed offline with `llama-index-llms-openai-like==0.8.0`. Tested with `openai==2.54.0`; no live round-trip. `http_client` / `async_http_client` carry the SDK's shared client (#740), so every call carries the run's correlation id and `donkey.last_call` is `OBSERVED` after a call. LlamaIndex raises the openai `APIStatusError` on a refusal; `typed_refusals()` converts it to the SDK's typed error. Caveat (#829): LlamaIndex keys reasoning-model handling (`llama_index.llms.openai.utils.O1_MODELS`) and `context_window` (`openai_modelname_to_contextsize`) on the exact bare OpenAI name, and `OpenAILike` defaults `context_window` to 3,900. `LlamaIndexAdapter.llm()` resolves both from the name after one `<provider>/` prefix. A prefixed reasoning model gets `temperature=1.0`, plus `max_completion_tokens` / `reasoning_effort` via `additional_kwargs`. Confirmed offline against `llama-index-llms-openai==0.7.10`. `connection_kwargs()` carries no model, so it does not apply these. | 2026-09-27 | #34; #829; `python/scripts/verify_frameworks.py --only llamaindex` (also `--emit-verified`) |
| Strands | `strands.models.openai.OpenAIModel(model_id, client_args={base_url, api_key, default_headers, http_client, max_retries=0}, stream=False)` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Construction and `isinstance` against the recorded path confirmed offline with `strands-agents==1.57.1`. Tested with `openai==2.54.0`; no live round-trip. `client_args.max_retries=0` reaches the OpenAI client; one send per budget 429 from the model and from `Agent(retry_strategy=None)` confirmed by request count with `strands-agents==1.57.1`, `openai==3.22.1` (#734, `tests/unit/test_framework_retries.py`). Error wrappers, for the typed-refusal bridge (#724): `strands.types.exceptions.ModelThrottledException`, `ContextWindowOverflowException` and `EventLoopException` are each raised `from` the original error and carry no request or response, so the adapter's `refusal_translator` hands their `__cause__` back to `core.refusals.translate()`; confirmed offline with `strands-agents==1.57.2` (`tests/unit/test_refusal_bridge.py`). Agent throttle retry (#951), read from the installed source of `strands-agents==1.57.2` with `openai==2.54.0` (the `openai` extra pins `openai<3`): `OpenAIModel.stream` (`strands/models/openai.py`) raises `ModelThrottledException(str(error)) from error` for an openai error that `classify_openai_error` calls throttling, a 429 included; `Agent(retry_strategy=...)` defaults to `strands.ModelRetryStrategy` (`strands/event_loop/_retry.py`), whose public `is_retryable()` is `isinstance(exception, ModelThrottledException)` and which sets `AfterModelCallEvent.retry` up to `max_attempts=6`; `_handle_model_execution` (`strands/event_loop/event_loop.py`) re-raises a non-retried exception as it is (`raise e`), and `event_loop_cycle` re-raises a model-call error without wrapping it in `EventLoopException`. So `model()` returns an `OpenAIModel` subclass whose `stream` (the public `Model.stream` interface) raises `TokenBudgetExceeded` in place of the throttle a budget 429 becomes: a default `Agent` sends it once, and `invoke_async()` and `__call__` raise that same object; a throttle the transport did not send passes through and is retried 3 times under `ModelRetryStrategy(max_attempts=3)`. Confirmed offline by request count with `simulate()` and a loopback proxy (`tests/unit/test_strands_terminal_refusal.py`, `tests/unit/test_framework_retries.py`). | 2026-10-05 | #34; `python/scripts/verify_frameworks.py --only strands` (also `--emit-verified`) |

### 8.1 Known upstream incompatibilities (floors, not ceilings)

Per the floors-never-ceilings rule the extras declare **floors and no ceilings**, so a fresh resolve always
takes the newest release. That is deliberate: the nightly framework matrix exists
to find breakage early rather than to pin it away. Recorded here so a red matrix
run is diagnosable instead of surprising.

| Dependency | Breaks at | Symptom | Status | Date |
|---|---|---|---|---|
| `openai` | `>=3.0` | The SDK passes its shared `DonkeyAsyncClient` (an `httpx.AsyncClient` subclass) into `AsyncOpenAI(http_client=…)`. openai 3.x retyped that parameter to `httpx2.AsyncClient`, a distinct class from a separate distribution, so `mypy --strict` flagged `llm/client.py` and `integrations/openai_agents.py`. **Type-annotation-only** — when an `http_client` is injected, openai builds and sends every request *through that client*, so `httpx2` never touches our path. Since #728, where the SDK builds the `OpenAI`/`AsyncOpenAI` itself it no longer relies on openai accepting an `httpx` client: when `openai.DefaultAsyncHttpxClient` is built on `httpx2` (openai 3.x), `donkey.llm.client()` (async and sync) and the adapters that pre-build an `AsyncOpenAI` (`_proxy_openai_client`: OpenAI Agents SDK, MS Agent Framework, ADK `client`) pass the same core `httpx2` bridge the `anthropic` row below uses (`core/transport/httpx2.py`, chosen by `built_on_httpx2()`); openai `<3` keeps the non-owning view. The adapters whose framework builds the OpenAI client from an `http_client` kwarg (LangGraph `http_async_client`/`http_client`, LlamaIndex `async_http_client`/`http_client`, Strands `client_args["http_client"]`) hand it a *reusable* variant of the same bridge on openai 3.x (its close is a no-op, because Strands closes its client after every request), and the view on openai `<3`; one helper (`llm.client.openai_http_client` / `openai_sync_http_client`) makes the choice for all of them, pinned by `test_adapter_openai3_bridge.py` (#728). llama-index-llms-openai and `strands-agents[openai]` still pin `openai<3`, so today only LangGraph reaches the bridge in a natural resolve. **Mitigated** with a `cast(Any, …)` on the argument at the three version-conditional `http_client` sites (`llm/client.py` ×2 and `_proxy_openai_client` in `integrations/_base.py`) — `cast(Any, …)` erases the type on both majors, so `mypy --strict` passes under **both** openai `<3` and `>=3` (was a targeted `# type: ignore[arg-type]` / `cast("httpx.Response", …)` that passed only under openai `>=3` and became `unused-ignore` / `redundant-cast` under openai `<3`, #597; original guards #137). The `exc.response` side needs no guard: `classify()` and `DonkeyError.response` take the structural `ResponseLike` Protocol (`core/_response.py`), which `httpx.Response` and `httpx2.Response` both satisfy, so the documented `classify(exc.response)` type-checks on both majors; pinned by `tests/typecheck/classify_response_contract.py` (#933). **Runtime re-verified and test-pinned** against openai 3.x (async + sync): `test_llm_client_openai3_injection.py` drives `donkey.llm.client()` through a mock transport and asserts the `client_id`/`client_secret` pair is injected and the base URL carries no `/v1` (#18). | MITIGATED | 2026-10-07 |
| The framework extras together (`adk`, `agent_framework`, `anthropic`, `crewai`, …) | `anthropic>=1.0`, `google-adk>=2.10`, `agent-framework>=1.19` | The framework extras are not co-installable at their current releases. `agent-framework-anthropic` (pulled in by every `agent-framework`) requires `anthropic<1`, while the `anthropic` extra resolves to 1.x; `google-adk>=2.10` requires `opentelemetry-api`/`-sdk<=1.42.1`, while `agent-framework>=1.19` needs OpenTelemetry `>=1.43`. With all of them in one resolve, pip backtracks for minutes and exits `resolution-too-deep`; raising the floors to the §8-verified versions makes the set unsatisfiable, so no floor fixes it. **Mitigated** by scoping `all` to the extras that install together (`llm`, `langgraph`, `otel`, `cli`, `local`); each framework extra resolves on its own and alongside `all`, at its newest release. CI dry-runs `.[all]` on every PR and nightly so a new upstream conflict fails there first (#697). | MITIGATED | 2026-09-30 |
| `anthropic` | `>=1.0` | `anthropic` 1.0 moved from `httpx` to `httpx2` (Pydantic's continuation of `httpx`: same API, separate distribution and classes) and, unlike openai 3.x above, **rejects** an `httpx` object at runtime: `AsyncAnthropic(http_client=<DonkeyAsyncClient>)` raised `TypeError: Invalid 'http_client' argument; 'httpx.AsyncClient' is from the 'httpx' package, but this SDK uses 'httpx2'`, so `donkey.anthropic.client()` failed on every 1.x release. The old `[all]` hid it, because `agent-framework-anthropic` held `anthropic<1` there. **Mitigated** without a ceiling: on `anthropic>=1.0` the adapter passes an `httpx2.AsyncClient` whose transport forwards each request through the shared `DonkeyAsyncClient` (the core bridge `core/transport/httpx2.py`, #728), so header injection, retries and the 401 refresh, the GenAI span, budget and `donkey.last_call` still run in one HTTP stack; `anthropic<1` gets the shared client's non-owning view (#733). The stack is chosen from `anthropic.DefaultAsyncHttpxClient` (an `httpx.AsyncClient` subclass before 1.0). The transport cannot see `send(stream=…)`, so the bridge streams when the JSON body carries `"stream": true` or the `X-Stainless-Raw-Response: stream` header is set; only these Stainless-style signals are recognised. The bridge has a blocking twin over the shared `DonkeyClient` (#728). The ids the shared client sends are copied back onto the framework's request, so `classify(exc.response)` carries the run's correlation id and the sent call id on both stacks (#738). Pinned by `test_anthropic_httpx2_bridge.py` and `test_anthropic_client_stacks.py` (a real client end to end, run on `anthropic==1.9.0` and `0.125.0`) (#701, #738). | MITIGATED | 2026-10-01 |
| `langgraph` × `langchain-core` | `langgraph==0.5.0` with `langchain-core>=1.0` | `langgraph` 0.5.0 declares `langchain-core>=0.1` but fails at import against langchain-core 1.x (`TypeError: Cannot create a consistent method resolution order (MRO) for bases ABC, Generic`). A single upstream point release: 0.4.0, 0.4.10, 0.5.4 and 0.6.0 all pass the LangGraph tests and the conformance suite against langchain-core 1.x. No resolve picks it unless `langgraph==0.5.0` is pinned (a fresh resolve takes the newest; lowest-direct takes 0.4.0). Recorded, not excluded: excluding it would be a ceiling-style exclusion (#743). | OPEN (upstream) | 2026-10-01 |

The CrewAI `crewai.LLM` return-class finding is **not** repeated here — it is a
constructor return-type contract issue, not version-triggered dependency
breakage, so its one row of record is the §8 CrewAI row above (#683).

The `cast(Any, …)` guards keep `mypy --strict` green against the newest `openai` a fresh
resolve pulls (the floors-never-ceilings rule). A full fix — migrating `core/transport/` off `httpx` onto
`httpx2` — would be a core-layer change (the layered architecture) and out of scope for the mitigation;
since #728 every OpenAI client the SDK wires on openai 3.x, including those built from an adapter's
`http_async_client` / `client_args` kwargs, gets the core `httpx2` bridge instead, so none depends on that migration.
The re-verification (#18) confirms it is unnecessary: injection works end to end under openai 3.x, and
`test_llm_client_openai3_injection.py` is the standing gate that would fail loudly if a future openai
release actually broke it — the point of keeping the extras floor-only rather than capping `openai<3`.
`cast(Any, …)` is resolve-independent — the argument is a concrete type on both majors, so the cast is
never `redundant-cast`, and passing `Any` to any parameter type is never an error — so `mypy --strict`
passes under **both** an `openai<3` and an `openai>=3` resolve. This replaced the earlier
`# type: ignore[arg-type]` / `cast("httpx.Response", …)` guards, which passed only under `openai>=3`:
because `--strict` enables `warn_unused_ignores` / `warn_redundant_casts`, a pinned `openai<3` (where the
mismatch does not occur) flagged them as *unused* / *redundant* (#597).

### 8.2 Known upstream runtime limitations

Behaviour of a supported framework that the SDK cannot change, so it is
recorded here rather than worked around.

| Framework | Python | Symptom | Status | Date |
|---|---|---|---|---|
| LangGraph `interrupt()` | `<3.11` | Under `graph.ainvoke()`, `interrupt()` raises `RuntimeError: Called get_config outside of a runnable context`, from an async node and from a sync node alike. `langgraph.config.get_config()` reads the run's config from langchain-core's `var_child_runnable_config` context variable, and asyncio tasks only accept an explicit context from 3.11, so the variable is unset inside the node. `interrupt()` takes no config argument, so there is nothing to pass through explicitly. `graph.invoke()` works on 3.10. Reproduced on Python 3.10 with `langgraph==0.3.0`, `0.4.0` and `1.2.12`. **Documented, 3.11+ required for `interrupt()` under `ainvoke()`**: `test_interrupt_and_typed_refusal_compose` is a strict `xfail` on 3.10 (an asserted exemption, not a skip), and `test_interrupt_and_typed_refusal_compose_sync` proves the composition on every supported Python. CI coverage of 3.10 × langgraph is #769 (#865). | DOCUMENTED | 2026-10-01 |
| CrewAI native OpenAI provider (`OpenAICompletion`) | all | The provider cannot be routed through the SDK's shared transport, so its calls carry no per-run correlation id, `donkey.last_call` reads UNAVAILABLE, jwt/bearer auth is refused, and CrewAI's own rate-limit retry stays on. Read from the installed source of `crewai==1.15.23` (the newest release) and `crewai==1.15.3` (the extra's floor), with `openai==2.54.0` (#958). **No supported field takes a client:** `_build_sync_client` and `_build_async_client` both start from the one dict `_get_client_params()` returns, which merges the public `client_params` field in, and pass it to `OpenAI(...)` and `AsyncOpenAI(...)`. The openai SDK type-checks `http_client` in each constructor (`httpx.Client` for the sync one, `httpx.AsyncClient` for the async one), so any single value fails one of them. Passing `client_params={"http_client": ...}` with either client type makes `crewai.LLM(...)` raise `ImportError: Error importing native provider: Invalid http_client argument` (reproduced). **The interceptor cannot reroute the send:** with `interceptor` set, both builders replace `http_client` with a fresh `httpx.Client` / `httpx.AsyncClient` around CrewAI's `crewai.llms.hooks.transport.HTTPTransport` / `AsyncHTTPTransport`. Their `handle_request` / `handle_async_request` call `on_outbound` (or `aon_outbound`), then always send through `httpx.HTTPTransport`'s own `handle_request`, then call `on_inbound`. The `BaseInterceptor` hooks can only edit the request and response objects, so the shared client's retry, `_on_request` and `_on_response` never run. The `crewai.hooks` LLM-call hooks (`LLMCallHookContext`) work on messages and response strings, not HTTP. The remaining routes all need private API: the `_client` / `_async_client` private attributes, overriding the `_build_*_client` methods in a subclass, or a client that is both an `httpx.Client` and an `httpx.AsyncClient`. **The rate-limit retry has no off switch:** `BaseLLM.__init_subclass__` wraps every subclass's `call` / `acall` in `run_with_rate_limit_retry` / `arun_with_rate_limit_retry` (`crewai.llms.retry`, new in 1.15.23, absent at 1.15.3), which makes up to `_LLM_RATE_LIMIT_MAX_ATTEMPTS` (3) attempts on any 429. The only bypass is the private `_active_llm_rate_limit_retry` context variable. **Documented, exemption kept:** `KNOWN_LIMITATIONS["crewai"]` in `tests/conformance/suite.py` (three scenarios), `_CREWAI_RATE_LIMIT_EXEMPTION` in `tests/unit/test_framework_retries.py`, and `CrewAIAdapter.observes_last_call = False`. | DOCUMENTED | 2026-10-04 |

### 8.3 Extras floors (lowest verified versions)

Floors, never ceilings, still means **every allowed version works**. Each floor
in `python/pyproject.toml` is the lowest release where the adapter's kwargs
behave as its §8 row records (#743). Every row below was installed with
`uv pip install --resolution lowest-direct -e ".[dev,<extra>]"` on Python 3.10
(and 3.11 for `langgraph`). That puts httpx 0.27.0, pydantic 2.6.0, pytest
8.0.0, pytest-asyncio 0.23.1 and typer 0.15.4 at their floors where the extra
allows it. Each row then passed `pytest tests/unit` and
`verify_frameworks.py --only <fw>` (offline signature check). Like §8, this is
offline evidence, not a live round-trip.

| Extra | Floor | Why this floor | §8 row |
|---|---|---|---|
| `llm` | `openai>=1.66` | First openai with the Responses API (`client.responses`), the data plane `donkey.llm.client()` speaks (§2). | — (§2) |
| `langgraph` | `langgraph>=0.4`, `langchain-openai>=0.3.9`, `langchain-core>=0.3.45` | langchain-openai 0.3.9 added `use_responses_api`, which the adapter always passes; on 0.3.8 and earlier it lands in the request body instead of switching the route. langgraph 0.3.x does not return `__interrupt__` from `ainvoke` on a pause, so the `interrupt()` composition test fails. langchain-core 0.3.45 is what langchain-openai 0.3.9 requires. The conformance plugin passes 4/4 at these floors. | LangGraph |
| `adk` | `google-adk>=2.4`, `litellm>=1.84` | 2.4.0 is the first whose `Gemini` has `client_kwargs`. Below it, ADK's pydantic config drops the kwarg silently and `gemini()` bypasses the gateway (#735). `LiteLlm` and `Gemini` both pass the signature check at 2.4.0. | Google ADK; Google ADK (native Gemini) |
| `agent_framework` | `agent-framework>=1.13` | Bisected from the verified 1.19.0. In 1.0.0–1.11.0 `agent_framework.openai.OpenAIChatClient` does not exist, so the adapter raises its `blocked on verification` import error. 1.12.0 builds it and passes the signature check, but `test_framework_redirects.py::test_agent_framework` fails: the call never reaches the proxy. 1.13.0 is the first that passes both. | MS Agent Framework |
| `openai-agents` | `openai-agents>=0.20`, `openai>=1.66` | The verified release; `openai` matches `[llm]`. | OpenAI Agents SDK |
| `anthropic` | `anthropic>=0.116` | The lowest verified release of the `AsyncAnthropic` row (also run on 0.125.0 and 1.9.0, §8.1). | Anthropic SDK |
| `crewai` | `crewai>=1.15.3` | 1.15.3 added the `openai/` + explicit `base_url` route to the native `OpenAICompletion`. Earlier releases fall back to LiteLLM for any id that does not look like an OpenAI model, returning `crewai.LLM` and LiteLLM exceptions. Signature check against `OpenAICompletion` passes at 1.15.3. | CrewAI |
| `llamaindex` | `llama-index-llms-openai-like>=0.8` | The verified release. | LlamaIndex |
| `strands` | `strands-agents[openai]>=1.57.1` | The verified release, with Strands' own `openai` extra: the bare distribution does not install `openai`, so `model()` raised `ModuleNotFoundError`. `test_strands_real_model_is_built_and_called_through_our_transport` builds and calls the real model with only `[strands]` installed. | Strands |
| `cli` / dev group | `typer>=0.15.4` | typer below 0.13 rejects `Path \| None` options, and below 0.15.4 it breaks against a fresh click 8.2+ (`make_metavar() missing … 'ctx'`). | — |
| `test` / dev group | `pytest>=8.0`, `pytest-asyncio>=0.23.1` | pytest-asyncio 0.23.0 crashes collection (`INTERNALERROR`) on a module-level `importorskip`; 0.23.1 runs the suite. pytest 8.0.0 is fine. | — |

On Python 3.10 the extras pass the full unit suite at their floors. The one
3.10 gap, `interrupt()` under `ainvoke()`, holds at every langgraph version and
is not a floor issue; it is recorded in §8.2.

A CI job that keeps these floors honest (lowest-direct per extra) is #769;
until it lands, this table is the record.

## 9. MCP tool binding classes (BG §2.7) — verify each name

| Framework | Binding class | Status | Source |
|---|---|---|---|
| LangGraph | `langchain_mcp_adapters.client.MultiServerMCPClient` | UNVERIFIED | — |
| Google ADK | `McpToolset` + `StreamableHTTPConnectionParams` | UNVERIFIED | — |
| MS Agent Framework | MCP client/tool class for streamable HTTP | UNVERIFIED | — |
| OpenAI Agents SDK | `agents.mcp.MCPServerStreamableHttp` | UNVERIFIED | — |
| Anthropic SDK | streamable-HTTP MCP via SDK `mcp_servers` integration | UNVERIFIED | — |
| CrewAI | `crewai_tools.MCPServerAdapter` | UNVERIFIED (signature confirmed offline) | `crewai_tools==1.15.22`: `inspect.signature(MCPServerAdapter.__init__)` → `(serverparams: StdioServerParameters \| dict[str, Any], *tool_names: str, connect_timeout: int = 30)`. Class path confirmed at `crewai_tools.adapters.mcp_adapter.MCPServerAdapter`, re-exported at top level. No MCP server actually connected/bound. 2026-09-27, #640 |
| LlamaIndex | `llama_index.tools.mcp.BasicMCPClient` + `McpToolSpec` | UNVERIFIED | — |
| Strands | `MCPClient(lambda: streamablehttp_client(...))` | UNVERIFIED | — |

## 10. Descriptor-derivation attributes (BG §2.5) — semi-public, put in nightly matrix

| Framework | Attributes read | Status | Source |
|---|---|---|---|
| FastMCP | `.name` `.description` `.inputSchema` | UNVERIFIED | — |
| LangChain | `.name` `.description` `.args_schema.model_json_schema()` | UNVERIFIED | — |
| Strands | tool spec input schema | UNVERIFIED | — |
| ADK | `FunctionTool` declaration params | UNVERIFIED | — |
| LlamaIndex | `.metadata.name` `.description` `.fn_schema` | UNVERIFIED | — |
| OpenAI Agents SDK | `.name` `.description` `.params_json_schema` | UNVERIFIED | — |
| Anthropic SDK | tool param dict `name`/`description`/`input_schema` | UNVERIFIED | — |
| CrewAI | `.name` `.description` `.args_schema.model_json_schema()` | UNVERIFIED (signature confirmed offline) | `crewai==1.15.22`: `crewai.tools.BaseTool.model_fields` has `name`, `description`, `args_schema: type[pydantic.BaseModel]` — `.args_schema.model_json_schema()` is a real call on that field's type. No bound tool actually inspected end to end. 2026-09-27, #640 |
| MS Agent Framework | `AIFunction` declaration + JSON schema | UNVERIFIED | — |

## 11. A2D platform MCP tools — shapes captured 2026-08-28 (NOT the direct Anypoint REST API)

Source: the session-connected `mcp-a2d` MCP server (host `www.a2d-ai.com`), an
agent/API **design + mocking + Exchange-publishing** tool. Server specs carry
`"platform": "mulesoft"` and it exposes `list_exchange_organizations` /
`publish_to_exchange_*` tools that take *separate* Anypoint credentials — so it
is Anypoint-adjacent but wraps an **unknown backend REST contract**. These are
therefore recorded as `VERIFIED-SHAPE-ONLY`: good enough to validate the SDK's
value types against real data, **not** a license to point `ExchangeRegistry` at
`www.a2d-ai.com` as if it were Anypoint Exchange (that stays blocked, verification discipline).

| Item | Where used | Status | Verified value |
|---|---|---|---|
| MCP runtime endpoint pattern | `registry/models.py` `McpServerHandle.endpoint_url` | VERIFIED-SHAPE-ONLY | `https://<host>/api/platform/{asset_id}/{mcp\|a2a\|api}` — suffix per asset_type |
| Transport kind | `McpServerHandle.transport` | VERIFIED-SHAPE-ONLY | spec `transport.kind = "streamableHttp"` → normalize to `streamable_http` |
| MCP protocol version | registry | VERIFIED-SHAPE-ONLY | `2025-06-18` |
| Tool-descriptor shape | `tools/filter.py` `ToolDescriptor` | VERIFIED-SHAPE-ONLY | `{name, description, inputSchema (JSON Schema)}` per tool |
| MCP server list record | registry search | VERIFIED-SHAPE-ONLY | `{id (uuid), name, type (openapi\|mock), description, status (published\|draft), enabled, organization_id, protocol_version, created_at, updated_at}` |
| Environment record | environment targeting | VERIFIED-SHAPE-ONLY | `{id, organization_id, asset_type (mcp_server\|agent_card\|rest_api), asset_id, name, base_url, environment_type (mocked\|pre_prod\|prod), auth_type, auth_config, extra_headers, timestamps}` |

### Open design questions surfaced by the probe (need a platform-team decision)

1. **Identity model mismatch.** A2D identifies assets by a bare **UUID**;
   `AssetRef.parse` expects Anypoint **Maven coordinates** (`group/asset/version`).
   The bridge between them is unresolved. Asserted as a finding in
   `test_registry_shapes.py::test_a2d_uuid_identity_is_not_a_maven_ref`.
2. **Integration architecture.** Is the SDK meant to (a) call the Anypoint
   Exchange REST API **directly** (the current design), or (b) be a client of
   this A2D platform? The captures verify shapes for (a)'s value types but do not
   reveal the REST endpoints behind the A2D MCP tools.
3. **Auth for MCP endpoints.** Captured environments report `auth_type: null`
   (mocked/staging/prod), so the auth requirement for a *governed* endpoint is
   still unconfirmed — `McpServerHandle.auth_required` default stays `True`.
4. **Environment→version semantics.** A2D environments are
   `mocked|pre_prod|prod` per asset, orthogonal to Exchange asset versions;
   reconcile with the plan's `environment` targeting.

## 12. Agent-donkey CLI plugin — direct REST contract (static analysis, 2026-08-28)

Source: static analysis of the installed
`mulesoft-anypoint-cli-agent-fabric-plugin` **v1.0.11** (`dist/**`) and its HTTP
transport dependency `anypoint-cli-command` **1.6.8** (`lib/**`), at
`~/.local/share/anypoint-cli-v4-public/node_modules/`. This is the compiled,
official client that issues the real calls against the sandbox — so paths,
header names, and bodies here are **read from the shipping client, not
invented**. Status label **`VERIFIED (plugin)`** = the exact signature is known
from authoritative client code; a live request has not additionally been
replayed. Per the verification discipline, code guards are only removed after the owning row is
confirmed and the maintainer signs off on scope (see "Unblocking" note below).

### 12.1 Auth (transport dependency `anypoint-cli-command/lib/`)

| Item | Status | Verified value | Source |
|---|---|---|---|
| OAuth2 client-credentials token endpoint | VERIFIED (plugin) | `POST /accounts/api/v2/oauth2/token`, body `{client_id, client_secret, grant_type: "client_credentials"}` → `{access_token, expires_in}` | `lib/uris.js:44` (`accountToken`), `lib/login.js` `loginWithClientIdAndSecret` |
| Username/password login | VERIFIED (plugin) | `POST /accounts/login` body `{username, password}` (then `/accounts/api/users/me`; MFA falls back to browser flow) | `lib/uris.js` `accountLogin`/`userMe`, `lib/login.js` |
| Authorization header | VERIFIED (plugin) | `Authorization: Bearer <access_token>` — set for any host matching `/anypoint\.mulesoft|platform\.mulesoft/` | `lib/api-client.js` request interceptor |
| Auth precedence | VERIFIED (plugin) | bearer > username/password > client_id/secret | `lib/login.js` `getAuthenticationMethod` |
| Known hosts | VERIFIED (plugin) | `anypoint.mulesoft.com` (default), `eu1.`, `stgx.`, `qax.`, `devx.` + `*.platform.mulesoft.com` region set | `lib/uris.js:47` `validServers`; `dist/helpers/utils.js` `MULESOFT_ORGS` |
| Host override mechanism | VERIFIED (plugin) | `--host` flag via `CredentialsSingleton`; no `ANYPOINT_HOST` env var read in client code | `lib/api-client.js` baseURL derivation |

### 12.2 Control-plane attribution / correlation headers (CLI request headers)

Set by the shared axios interceptor on **every** Anypoint-domain request
(`lib/api-client.js`), unless noted:

| Header | Meaning |
|---|---|
| `X-ANYPNT-ENV-ID` | environment id |
| `X-ANYPNT-ORG-ID` | organization id |
| `x-organization-id` | org id (duplicate) |
| `x-owner-id` | account/user id |
| `x-request-id` | per-request uuid (axios default) |
| `x-request-d` | deploy-command correlation echo of `x-request-id` (`dist/commands/agent-network/project/deploy.js`) |
| `User-Agent` | `Anypoint-CLI/<version>` |
| `x-sync-publication: true` | Exchange publish POST only (`dist/utils/facets/asset-facet.js`) |
| `x-anypoint-api-instance-id` | **downstream/data-plane**, NOT a CLI header — injected into deployed Flex Gateway traffic by the `header-injection` policy for telemetry attribution between agent-network components (`dist/utils/builders/policies-factory.js`) |

> **§3 caveat (highest-priority unknown, still open).** These are
> *control-plane* headers for the management API. The **data-plane LLM-proxy
> token/cost-attribution header** the plan §3 needs is still UNVERIFIED: the CLI
> never calls the LLM data plane itself (§12.6). `x-anypoint-api-instance-id` is
> the closest analog (gateway-injected, per-instance) but is telemetry
> correlation, not confirmed to be the per-agent cost-attribution key. Do not
> wire `core/transport/` attribution to it without a data-plane capture.

### 12.3 Exchange publish / read (`dist/utils/exchange.js`, `dist/utils/facets/asset-facet.js`)

| Method | Path | Notes |
|---|---|---|
| GET | `/exchange/api/v2/assets/{groupId}/{assetId}[/{version}]` | read asset |
| GET | `/exchange/api/v2/assets/{groupId}/{assetId}/asset` | asset info |
| POST | `/exchange/api/v2/organizations/{groupId}/assets/{groupId}/{assetId}/{version}` | **publish** (multipart), header `x-sync-publication: true` |
| POST | `/exchange/api/v2/assets/{groupId}/{assetId}/versionGroups/{versionGroup}/instances/external` | create external instance `{name, endpointUri}` |
| POST | `/graph/api/v2/graphql` | asset metadata GraphQL |
| GET | `/exchange/api/v2/assets/{groupId}/{policyId}/minorVersions/{minorVersion}` | policy metadata (`dist/helpers/policy-helper.js`) |

Publish multipart fields: `type` (`agent|mcp|llm|app|policy|agent-network`), `name`,
`description`, `dependencies` (`g:a:v,…`), `tags` (csv), `files.{classifier}`
(octet-stream blobs), `properties.{key}`, `properties.source`
(`urn:gav:<g>:<a>:<v>`).

### 12.4 Agent Network gateway setup + Private Space (`dist/utils/uris.js`, `gateway.js`)

| Method | Path |
|---|---|
| GET/POST | `/gatewaymanager/api/v1/organizations/{org}/environments/{env}/gateways` |
| GET | `/gatewaymanager/api/v1/organizations/{org}/environments/{env}/gateways/{gatewayId}` |
| GET | `/gatewaymanager/xapi/v1/organizations/{org}/environments/{env}/gateways/{gatewayId}` (status) |
| GET | `/gatewaymanager/xapi/v1/organizations/{org}/environments/{env}/gateways/targets` (Private Spaces) |
| GET | `/gatewaymanager/xapi/v1/gateway/versions` |
| GET | `/runtimefabric/api/organizations/{org}/targets/{targetId}` |
| GET | `/runtimefabric/api/organizations/{org}/targets/{targetId}/environments/{env}/domains?sendAppUniqueId=true` |

`setup gateways` POST body: `{name, targetId, releaseChannel:"edge", runtimeVersion,
size:"small"|"large", configuration:{ingress:{publicUrl,forwardSslSession:false,
lastMileSecurity:false}, logging, properties, tracing}}`. Constants:
`agent-network-ingress-gw` (small) + `agent-network-egress-gw` (large) →
`agent-network-space`. The **plugin code** contains no "MCP Bridge"/"Omni
Gateway" term (explicit grep, zero hits) — it provisions plain Flex Gateway
ingress/egress + API Manager. HOWEVER the **sandbox itself** has a separate
`omni-gateway-shared-space` Private Space (endpoints
`https://omni-gateway-shared-space-<id>.<region>.cloudhub.io/…`) hosting agent
`connections`, alongside the `agent-network-ingress-gw-<id>.…/mcp/<name>/` space
for MCP servers. So "Omni Gateway" is a real deployment target here (the plan's
Pillar-1 LLM proxy home), just not a plugin-code identifier.

### 12.5 API Manager governance + app deploy (`dist/utils/facets/*`)

Governance (used for ingress `apiInstances` and egress `connections` alike):

| Method | Path |
|---|---|
| GET | `/apimanager/api/v1/organizations/{org}/environments/{env}/apis?assetId={a}&groupId={g}` |
| POST/PATCH | `/apimanager/api/v1/…/apis[/{apiId}]` |
| POST | `/apimanager/api/v1/…/apis/{apiId}/policies` (inbound) |
| POST | `/apimanager/xapi/v1/…/apis/{apiId}/policies/outbound-policies` |
| DELETE | `/apimanager/api/v1/…/apis/{apiId}/policies/{policyId}` |
| POST | `/proxies/xapi/v1/…/apis/{apiId}/deployments` |

Create-API body: `{spec:{groupId,assetId,version}, endpoint:{type,deploymentType:"HY",
uri,proxyUri,isCloudHub:null}, endpointUri:"${ingressUrl}/${path}/",
technology:"flexGateway", instanceLabel, description,
metadata:{connectionId, source:"urn:gav:…", protectionDirection:"ingress"|"egress"}}`
— this confirms the exact shape behind the `api_list.sandbox.json` fixture and
the `McpServerHandle` mapping in `test_governed_state_shapes.py`.

App deploy (CloudHub 2.0 / RTF): `/amc/application-manager/api/v2/organizations/{org}/environments/{env}/deployments[/{id}]`;
status poll `/amc/adam/api/organizations/{org}/environments/{env}/deployments/{id}`.

### 12.6 LLM proxy — how governance is actually wired

The CLI **never calls a `/v1/chat/completions` data-plane path.** An LLM
connection is deployed as an **egress Flex Gateway API instance** whose upstream
is the provider URL (template default `https://api.openai.com/v1/`), with model
transcoding applied as **API Manager outbound policies**, fetched by GAV from
Exchange (`dist/utils/constants.js`, `dist/helpers/policy-helper.js`):

- `openai-transcoding-policy` `1.0` → `openai`, `azureopenai`
- `gemini-llm-provider-policy` `1.0` → `gemini`
- auth outbound policies: `credential-injection-api-key` `1.0`,
  `credential-injection-oauth2` `1.2`, `credential-injection-basic-auth` `1.0`,
  `credential-injection-oauth2-obo` `1.1`, `intask-authorization-code-policy` `1.0`.

Provider catalog (from `exchange:asset:list llm`, stock-policy org
`68ef9520-24e9-4cf2-b2f5-620025690913`): `openai`, `azureopenai`, `gemini`,
`bedrock` (`bedrock-llm-provider-policy-flex`), `anthropic`
(`anthropic-llm-provider-policy-flex`). Governance policies present as first-class
Exchange assets: `llm-proxy-core(-flex)`, `llm-token-rate-limit-policy-flex`,
`llm-pii-detection-policy-flex`.

Project descriptor for an LLM proxy (`agent-network.yaml`, from a real
`project create`): a `llmProviders.<name>` block (`metadata.platform`, e.g.
`OpenAI`) + a `connections.<name>` of `kind: llm` with
`spec.url: https://api.openai.com/v1/` and `spec.configuration.apiKey:
${openai.apiKey}` (injected at deploy via `--property openai.apiKey:…`). A broker
that consumes it references `spec.llm.ref.name` + `configuration.model`
(template default model `gpt-5-mini`).

Pre-existing asset: `00000000…/llm-test-proxy/1.0.0` — Exchange type `llm`, tag
`platform: openai`, file classifier `fat-llm-metadata` (zip). **Published but NOT
deployed** to Sandbox or Design (confirmed via `api-mgr:api:list`), so it is not a
live capture surface.

Consequence for plan §1/§2: the "governed model access" **ingress** data-plane
base URL and its request/response shape are still **UNVERIFIED** — they live at
the deployed gateway runtime, which requires a live deploy to observe (§12.8
item 3). The upstream/egress URL and provider model above ARE now known.

### 12.7 Agent Network project format (`dist/commands/agent-network/project/create.js`, `templates/`)

On-disk layout: `exchange.json` (classifier `agent-network`, GAV +
`descriptorVersion:"1.0.0"`) + `agent-network.yaml` (main file: sections
`brokers`, `agents`, `mcpServers`, `llmProviders`, `connections`) + `target/`
(generated Maven broker project + built jar). **No `pom.xml` ships**; the Maven
project is generated at build time by a bundled JVM uber-jar
(`com.mulesoft.agents:agent-fabric-transformation:1.0.0-EAP-SNAPSHOT`) invoked
via `./mvnw clean package`. Per-component Exchange files: `agent-metadata.json`
(`agent-metadata`), `mcp-metadata.json` (`mcp-metadata`), `llm-metadata.json`
(`llm-metadata`), `a2a-card.json` (`a2a-card`), `schema.json` (`schema`),
`mule-application.jar` (`mule-application`/type `app`).

This resolves §7's "native descriptor formats" question: descriptors are
**typed Exchange files with fixed classifiers**, attached to the agent-network
root asset — not a single bespoke manifest.

### 12.8 Unblocking guidance (verification discipline)

These rows are strong enough to *design against* but a live request should
confirm each before its `NotImplementedError("blocked on verification: …")`
guard is removed. Ordered by confidence:

1. **Safe to unblock now (behind a live smoke test):** OAuth2 token path
   (§12.1) — single, unambiguous, matches `core/auth.py`'s intended flow.
2. **Design-ready, unblock after one live GET:** Exchange read (§12.3) and API
   Manager list/describe/policy (§12.5) — these back the governed-state join and
   already have fixture coverage.
3. **Do NOT unblock:** LLM data-plane (§2, §12.6) and §3 token-attribution
   header — the CLI does not exercise them; capturing them needs a deployed
   gateway, not static analysis.
