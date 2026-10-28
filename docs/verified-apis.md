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
> 2026-09-24 against `ddk-bedrock-guardrails` (#568), and the Google Model
> Armor shape (same family) on 2026-10-19 against `ddk-google-model-armor`
> (#1178). The CrowdStrike AIDR shape (same family, #1176) is read from the
> policy source, not live-captured (no proxy runs it; capture tracked in #1184).
> The Injection Protection
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
> constructor/binding names** (§§8–9; the §10 descriptor-derivation reads were
> `VERIFIED (local capture)` on 2026-10-26, #1024), and the on-behalf-of token
> exchange behind `donkey.as_user()` (§13, #255). §11
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
> contract. `VERIFIED (local capture)` = read off a real object of the
> installed framework at the pinned version, committed as a fixture and
> re-asserted by the nightly matrix (no Anypoint involved). `UNSUPPORTED` = a
> deliberate gap, with its reason; its code guard stays. `UNVERIFIED` = not
> yet confirmed; its code guard stays in place.
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
  - [8.4 Docs status roster](#84-docs-status-roster)
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
- [13. On-behalf-of token exchange (BG §2.4)](#13-on-behalf-of-token-exchange-bg-24)
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
| Control-plane host (EU) | `core/_verify.py` `REGION_HOSTS` | VERIFIED (plugin) | `eu1.anypoint.mulesoft.com`. Live smoke (unauthenticated, #22): `GET /accounts/api/users/me` → `401`, and `POST /accounts/api/v2/oauth2/token` with `{}` → `401 {"error":"invalid_request","error_description":"Invalid grant_type"}`, the same response as the US host. | 2026-10-12 | `anypoint-cli-command` 1.6.6 / `anypoint-cli-command-lib` 1.6.14 `lib/uris.js` `validServers`; [Hyperforce docs](https://docs.mulesoft.com/hyperforce/) |
| Control-plane host (CA Hyperforce) | `core/_verify.py` `REGION_HOSTS` | VERIFIED (LIVE) | `ca1.platform.mulesoft.com` (on `platform.`, **not** `anypoint.`; the previous `ca1.anypoint.mulesoft.com` placeholder does not resolve in DNS, #22). An SDK run with `region="ca"` and no `base_url` against a real ca1 org: `AnypointConnectedApp` minted a client-credentials token at `POST /accounts/api/v2/oauth2/token`, `GET /accounts/api/me` → `200`, `GET /accounts/api/organizations/{org}/environments` → `200` (2 envs). On the same root, `GET /apimanager/api/v1/organizations/{org}/environments/{env}/apis` → `403` JSON (connected app lacks the scope) and `GET /exchange/api/v2/assets/{org}/<missing>` → `404` JSON: both services answer there, but neither contract was exercised. `ca1.omni.mulesoft.com` is not a control-plane API host (`405` on the token path). | 2026-10-12 | live probe, ca1 org, connected app (client credentials); [Hyperforce docs](https://docs.mulesoft.com/hyperforce/) |
| Control-plane host (JP Hyperforce) | `core/_verify.py` `REGION_HOSTS` | VERIFIED (LIVE) | `jp1.platform.mulesoft.com` (on `platform.`, **not** `anypoint.`; the previous `jp1.anypoint.mulesoft.com` placeholder does not resolve in DNS, #22). An SDK run with `region="jp"` and no `base_url` against a real jp1 org: `AnypointConnectedApp` minted a client-credentials token at `POST /accounts/api/v2/oauth2/token`, `GET /accounts/api/me` → `200`, `GET /accounts/api/organizations/{org}/environments` → `200` (2 envs), `GET /apimanager/api/v1/organizations/{org}/environments/{env}/apis` → `200` JSON. `GET /exchange/api/v2/assets/{org}/<missing>` → `404` JSON: Exchange answers on the same root, but its contract was not exercised. Not in the shipping CLI's `validServers`. | 2026-10-12 | live probe, jp1 org, connected app (client credentials); [Hyperforce docs](https://docs.mulesoft.com/hyperforce/) |
| Connected-app scopes: Exchange read | `core/auth.py`, docs | UNVERIFIED | — | — | — |
| Connected-app scopes: API Manager write | provisioning, `donkey expose` (#226) | PARTIAL: a sufficient set VERIFIED (LIVE) | A `client_credentials` token ran the whole A2A instance write path in §12.5 (create, upstreams, outbound policy, deploy, status, delete) and an Exchange hard delete. The app held 57 scopes on the environment, among them `manage:apis`, `manage:api_configuration`, `manage:api_policies`, `manage:api_proxies`, `read:api_configuration`, `read:api_policies` and `view:environment`. Which of them each call needs was not isolated: that takes an app granted one scope at a time (#23). | 2026-10-27 | live run, sandbox, connected app (#254) |
| Connected-app scopes: policy management | provisioning, `donkey expose` (#226) | PARTIAL: a sufficient set VERIFIED (LIVE) | The outbound-policy POST and the policy listing in §12.5 succeeded with the token above (`manage:api_policies` and `read:api_policies` among its scopes). The minimal set was not isolated (#23). | 2026-10-27 | live run, sandbox, connected app (#254) |
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
| Inbound correlation id on a served A2A task (#225) and any served request (#308) | `core/inbound.py` `correlation_id_from_headers` ← `a2a/_server.py`, `Donkey.run_from_headers` | UNVERIFIED | `donkey.serve()` and `donkey.run_from_headers()` adopt an inbound `X-Correlation-Id` (or the `correlation_header` name) when it is 1–128 of `[A-Za-z0-9._:-]`, else mints one, and sends it on every downstream call. The header name is the verified LLM-proxy join key (row above). **Not verified:** whether an Omni Gateway A2A proxy in front of the agent forwards the caller's `X-Correlation-Id` to the upstream agent, or mints its own. Until a live A2A-proxy probe, adoption is proven only for a caller that sends the header directly. The a2a-sdk exposes inbound headers on `ServerCallContext.state["headers"]` (`DefaultServerCallContextBuilder`, a2a-sdk 1.2.2, read from the installed package). | — | offline: `tests/unit/test_a2a_governed_inbound.py`, `tests/unit/test_inbound_correlation.py` |

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
header (Azure Content Safety / Amazon Bedrock Guardrails / Google Model Armor /
CrowdStrike AIDR) on a `403` → `ContentSafetyBlocked` (parses `categories` from the sibling `…-reason`
header; the same header on the policies' fail-closed `503` is an outage, typed
`UpstreamModelError`, #1178/#1176) — both of these also
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
| Content safety / guardrails | `403` | `x-llm-proxy-azure-content-safety-action` / `x-llm-proxy-bedrock-guardrail-action` / `x-llm-proxy-model-armor-action` / `x-llm-proxy-crowdstrike-aidr-action` == `reject`; reasons in the sibling `…-reason` header. A `503` with the same header is the fail-closed outage (moderation service unreachable), not a block → `UpstreamModelError` | `ContentSafetyBlocked` (parses `categories`) | [Azure Content Safety policy](https://docs.mulesoft.com/gateway/latest/policies-included-azure-content-safety) (v1.13.0); [Amazon Bedrock Guardrails policy](https://docs.mulesoft.com/gateway/latest/policies-included-bedrock-guardrails) (v1.13.0); [Google Model Armor Guardrails policy](https://docs.mulesoft.com/gateway/latest/policies-included-google-model-armor-guardrails) (v1.14.0); [CrowdStrike AIDR Guardrails policy](https://docs.mulesoft.com/gateway/latest/policies-included-crowdstrike-aidr-guardrails) (v1.14.0); VERIFIED (LIVE) 2026-09-22 on `ddk-azure-content-safety` (instance 21180957) + 2026-09-24 on `ddk-bedrock-guardrails` (#568) + 2026-10-19 on `ddk-google-model-armor` (#1178). CrowdStrike AIDR (#1176) is read from the policy source, not live-captured (#1184). The `503` shape is read from the policy source, not live-captured |
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
`x-token-*` / `ms` rule. They are also not the gateway's request window below,
which uses the **unsuffixed** names `x-ratelimit-limit` / `-remaining` /
`-reset`. The SDK reads them into their own source, below.

**Upstream provider window (LIVE-VERIFIED 2026-10-08, #1025).** Which upstream
sends the passthrough, and in what shape, from the per-upstream route captures
(`tests/fixtures/anypoint/routes/`, #894) and the request-rate-limit captures
(`tests/fixtures/anypoint/request_rate_limit/`, #974):

| Upstream | `x-ratelimit-{limit,remaining}-{requests,tokens}` | `x-ratelimit-reset-{requests,tokens}` | Read as | Status |
|---|---|---|---|---|
| `openai` | integers (`5000` / `4999`; `2000000` / `1999990`) | Go duration with a unit: `12ms`, `24ms`, `0s`, `2ms`, `6ms` | `reset_at` = `observed_at` + the duration | VERIFIED (LIVE) |
| `azureopenai` | integers (`250` / `249`; `250000` / `249987`) | bare `0`, no unit, sent while `remaining < limit`; plus `x-ratelimit-renewalperiod-*: 60` and `x-ratelimit-key` | counts only; `reset_at` stays `None` | VERIFIED (LIVE) (shape); reset meaning UNVERIFIED |
| `bedrockanthropic`, `gemini` | not sent | not sent | window stays unobserved | VERIFIED (LIVE) (absent) |

`Budget.observe()` fills `donkey.budget.upstream.requests` / `.tokens` (each an
`UpstreamWindow`, with `provider` from `x-llm-proxy-llm-provider`). Each
response replaces the whole window. The source never writes the gateway's
token window or `donkey.budget.requests`, and they never write it.
`tests/unit/test_budget_upstream.py` pins that on a capture carrying both. The
reset parser (`core/_budget_headers.parse_go_duration`) takes Go's
`time.ParseDuration` grammar with a unit required. Only `ms` and `s` appear in
the captures, and the other units come from Go's grammar, not a capture.

`Budget.pace()` **does not** guard on this window. It is the provider key's
quota, shared by every consumer of the proxy, not the caller's allowance. It
also depends on the route: a model-based or semantic route can send the next
call to another upstream, or to one that sends no such header. Azure gives no
reset time it states a unit for. Still **UNVERIFIED**: what Azure's bare `0`
means, whether `x-ratelimit-renewalperiod-*` (not read) is in seconds, and the
shape of an upstream-quota `429` passed through the gateway (not captured, so
`classify()` treats it as any other `429`).

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
| Google Model Armor Guardrails | `google-model-armor-guardrails-policy` `1.0.0` (OpenAI ingress, Gemini upstream) | VERIFIED (LIVE) | `403`, `x-llm-proxy-model-armor-action: reject` + `-phase` (`request`/`response`) + `-reason` headers; body `{"error":"Content blocked by Google Model Armor","categories":[…]}` (the template's matched filters, e.g. `pi_and_jailbreak`, `rai_dangerous`, `malicious_uri`). A response-phase `403` keeps the upstream's headers (`x-gemini-*`). `allow` on every moderated success; `skip_detection` rides on `allow`. Streamed responses through the Gemini transcoder are not scanned (policy gap), so a streamed call sees only the request-phase `403` | 2026-10-19 | live probe, `ddk-google-model-armor` instance 21244069 (shared-omni-gateway) |
| CrowdStrike AIDR Guardrails | `crowdstrike-aidr-guardrails-policy` `1.0.0` (OpenAI Chat Completions traffic only) | UNVERIFIED (read from policy source; live capture #1184) | `403`, `content-type: application/json` + `x-llm-proxy-crowdstrike-aidr-action: reject` + `-phase` (`request`/`response`) + `-reason` (triggered detectors, comma-joined: `malicious_prompt`, `pii`, `secrets`, `topic`, `language`, `access_rule`, …; omitted when none) headers; body `{"detectors":[…],"error":"Content blocked by CrowdStrike Falcon AIDR","message":"<AIDR summary>"}`. A streamed response is buffered and a block comes back as the same JSON `403`. `allow` / `transform` (redacted content forwarded) on a success. Fail-closed `503` `{"error":"CrowdStrike Falcon AIDR service unavailable"}` with `action: reject` and a cause reason (`auth_failed`, `rate_limited`, `timeout`, `indeterminate`, …) → `UpstreamModelError` | 2026-10-20 | policy source 1.0.0 + the public policy page (status codes); no proxy, needs a CrowdStrike AIDR for Agents subscription (#1176) |
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
| Can Local Mode run the LLM Proxy? | the Verification milestone | UNSUPPORTED | No. MuleSoft documents Model Proxy as "supported on Managed Omni Gateway and Self-Managed Omni Gateway running in Connected Mode". This matches the stock Flex Gateway 1.14.0 Local Mode image rejecting `llm-proxy-core`, `model-based-routing`, and `openai-transcoding-policy` as missing extensions (#651, #661, #258) | 2026-10-27: docs.mulesoft.com `general/model-proxy`; 2026-09-27: 1.14.0 image |
| Can Local Mode run MCP Bridge? | the Verification milestone | UNSUPPORTED | No. The Omni Gateway 1.14 docs mark the MCP Support policy, and every other MCP policy, "isn't supported in Local Mode"; the same stock image rejects `mcp-support` as a missing extension (#651, #661, #258) | 2026-10-27: docs.mulesoft.com `gateway/latest/policies-included-mcp-support`; 2026-09-27: 1.14.0 image |
| Can Local Mode run the A2A policies? | `donkey dev` Option A (BG §2.9.3, #227) | UNSUPPORTED | No. The Omni Gateway 1.14 docs mark every A2A policy (Agent Card, PII Detector, Prompt Decorator, Quality Evaluation, Schema Validation, Token Based Rate Limit, their v1 forms, the guardrails, and the v1-to-v0.3 transcoder) "isn't supported in Local Mode". Client ID Enforcement isn't either (#258) | 2026-10-27: docs.mulesoft.com `gateway/llms-full.txt` (1.14) |
| Does Local Mode need a control-plane licence/registration artifact? | OSS/CI viability | UNSUPPORTED | Yes. The docs say a Local Mode gateway "only connects to the control plane for registration and logging usage metrics", so it isn't air-gapped and needs an Anypoint org. The stock image refuses API configuration until it is registered (#651, #661, #258) | 2026-10-27: docs.mulesoft.com `gateway/latest/index`; 2026-09-27: 1.14.0 image |
| Is a self-managed image available for local dev, and on what terms? | `donkey dev` Option A (BG §2.9.3, #227) | UNSUPPORTED | The image is public: `docker.io/mulesoft/flex-gateway`, latest `1.14.4`, published for `linux/amd64` only. The docs require an x86 processor ("M1 and M2 processors are not supported"). Running it needs registration to an Anypoint org (a Trial org is offered). Running the image locally costs nothing per developer (maintainer-confirmed). Option A is still ruled out by the capability rows above (#258) | 2026-10-27: Docker Hub `mulesoft/flex-gateway` tags API; docs.mulesoft.com `gateway/latest/gateway-getting-started`; cost: maintainer, #258 |
| Which policies are Connected-Mode-only? (portability table) | the Verification milestone | UNVERIFIED | — | — |
| Is "deployed to gateway" readable per API instance? | `require_deployed` | VERIFIED (CLI) | Yes — `api-mgr:api:list` (per env) + `:api:describe <id>` returns Endpoint URI, gateway, deployment target | 2026-08-28 |
| Are applied policies readable per API instance? | governed-state join | VERIFIED (CLI) | Yes — `api-mgr:policy:list <id>` returns `{ID, Template ID, Asset ID, Asset Version, Label, Status, Configuration}` | 2026-08-28 |
| Are governance ruleset results exposed via API? | `require_governance_pass` | PARTIAL | `governance:api` evaluates rulesets; `governance:profile:*` manages profiles (ruleset refs = Maven GAV). Result-read shape pending | 2026-08-28 |
| Can applied policies be fetched in bulk per environment? | governed-only discovery perf | UNVERIFIED | per-instance confirmed; bulk endpoint pending plugin analysis | — |
| MCP-specific + enforcement policies observed | the Verification milestone (portability) | VERIFIED (CLI) | `mcp-support` (`injectMcpNameHeaders`), `client-id-enforcement` (client_id/client_secret headers), `header-injection` (`x-gateway-token`) | 2026-08-28 |
| Governed MCP endpoint URL shape | `McpServerHandle.endpoint_url` | VERIFIED (CLI) | ingress gw: `https://agent-network-ingress-gw-<id>.<region>.cloudhub.io/mcp/<name>/` | 2026-08-28 |
| How does an Exchange MCP asset resolve to its gateway endpoint? (#1297) | `resolve_mcp()` | VERIFIED (LIVE) 2026-10-28 | Three documented reads. (1) The environment id, by name: `GET /accounts/api/organizations/{org}/environments` → `data[{id, name}]` (Access Management API). (2) The asset version's instances: `GET /apimanager/api/v1/organizations/{org}/environments/{env}/apis?assetId=&groupId=&assetVersion=` (API Manager API `listApiInstances`) → `assets[].apis[]`, `total` counting assets, not instances; then `GET …/apis/{id}` for `endpoint.proxyUri` and `deployment.targetId`. (3) The gateway: `GET /gatewaymanager/api/v1/organizations/{org}/environments/{env}/gateways/{targetId}` (Omni Gateway Manager API) → `configuration.ingress.publicUrl`. The endpoint is `publicUrl` + the `proxyUri` path + `/`. The instance's own `endpointUri` stays `null` on a managed-gateway MCP instance, deployed or not, so it is not read | 2026-10-28 sandbox, shared-omni-gateway; `tests/fixtures/anypoint/mcp_resolve/` |
| Are the instance-list filters server-side? (#1297) | `resolve_mcp()` | VERIFIED (LIVE) 2026-10-28 | Yes. `assetId` matches exactly (`ddk-anthropic-inbound` does not return `ddk-anthropic-inbound-bearer`) and `assetVersion` filters (a version with no instance returns `total: 0`). A version can have several instances: one undeployed and one deployed came back together; an undeployed one has `deployment: null` in the list and `status: unregistered`. `resolve_mcp()` takes the one with `deployment.expectedStatus: deployed` and refuses several. A `deployment` sent inline on `POST …/apis` was ignored: the instance stayed unregistered until `POST /proxies/xapi/v1/…/apis/{id}/deployments` | 2026-10-28 sandbox; `mcp_resolve/apis.by-asset*.json` |
| Does the gateway route the resolved MCP path? (#1297) | `McpServerHandle.endpoint_url` | VERIFIED (LIVE) 2026-10-28 | Only with a trailing slash. An MCP `initialize` with no credentials to `<publicUrl>/<label>/` got `401 {"error":"Client ID is not present"}`, `WWW-Authenticate: Client-ID-Enforcement` (the instance's policy); `<publicUrl>/<label>` got an empty `404` from `Anypoint Flex Gateway`, the same as an unrouted path, on every one of the alternating calls. No call carried credentials or reached the upstream | 2026-10-28 sandbox; `mcp_resolve/traffic.*.json` |
| Governed **LLM proxy** policy stack (live) | governed-state join, the Verification milestone | VERIFIED (LIVE) | on `openai-sdk`: `cors 1.3.2`, `dataweave-headers-transformation 1.0.0`, `client-id-enforcement 1.3.3`, `llm-proxy-core 1.0.5`, `model-based-routing 1.0.3`, `openai-transcoding-policy 1.0.3` — all Enabled | 2026-08-28 |

**Decision (2026-09-27, #661): Donkey Development Kit does not support Omni/Flex
Gateway Local Mode.** The decision first rested on bounded findings against a
Flex Gateway 1.14.0 stock image. That image rejects the LLM Proxy and MCP
Bridge policies this SDK depends on as missing extensions, and it won't accept
configuration until it is registered to a control plane. The vendor
documentation has since confirmed this independently of image version (#258,
2026-10-27): Model Proxy is Connected Mode only, and every MCP and A2A policy
is unsupported in Local Mode. So the capability rows above are `UNSUPPORTED`,
not open questions. This also rules out `donkey dev` Option A (BG §2.9.3), so
`donkey dev` ships as the simulated-ingress Option B only (#227). The
pure-Python local gateway simulator (`donkey mock` / `simulate()`, BG §1.4,
the `local_gateway` pytest marker) is DDK's supported local dev loop, and
real-gateway behavior is exercised against a hosted (Connected Mode) proxy. See
the site's `reference/unsupported-boundary` page for the consumer-facing
statement of this same decision, and #65 for the verification questions this
decision resolves as out of scope rather than answered.

## 7. Publication / Exchange (BG §2.5)

| Item | Gates | Status | Finding | Source |
|---|---|---|---|---|
| First-class Exchange asset types for MCP servers & AI agents? | BG §2.5 + `asset_types` filter (governed-only discovery) | VERIFIED (LIVE) 2026-10-27 | Yes — Exchange assets named "… MCP Server" and "… Agent Network"/agent, managed as API Manager instances. 2026-10-27 (#53): `type` is a field Exchange filters on, not a naming convention: the Graph API search with `types: ["mcp"]` returns only `mcp` assets and `types: ["mcp", "agent"]` only those two, so `asset_types` is a server-side filter | 2026-08-28: `api-mgr:api:list`; 2026-10-27: `tests/fixtures/anypoint/exchange_search/search.{types-mcp,page1,page2}.json` |
| Exact publication / discovery asset-type tokens (`mcp`, `a2a-agent`, `agent`, `api`) | `PublicationAssetType`, `AssetRef.type`, `ExchangeRegistry.search(asset_types=)` | PARTIAL: `mcp` VERIFIED (LIVE) 2026-10-26; `agent` VERIFIED (LIVE) 2026-10-27 | `mcp` is confirmed by a live publish: the asset comes back with `"type":"mcp"` and `donkey publish` publishes MCP servers with it (#218). `agent` is confirmed the same way: a live publish with `type=agent` comes back as `"type":"agent"`, and `donkey publish` publishes each `@donkey.agent` with it (#1260). `a2a-agent` is not an Exchange type: an A2A agent publishes as `agent`, so `search(asset_types=["a2a-agent"])` stays blocked. Discovery searches by the same tokens; `api` is `rest-api`, `http-api` and `soap-api`, the types MuleSoft's Graph API page lists (#53) | 2026-10-26 and 2026-10-27 sandbox publishes, `tests/fixtures/anypoint/exchange_publish/{publish,agent.publish}.201.json`; Exchange Experience API spec; plugin `asset-facet.js`; `tests/fixtures/anypoint/exchange_search/` |
| Exchange asset search endpoint, auth, paging (#53) | `ExchangeRegistry.search()` | VERIFIED (LIVE) 2026-10-27 | `POST /graph/api/v1/graphql` with the control-plane bearer token: `assets(query: $query, latestVersionsOnly: true)`, `$query` a `SearchAsset`. `organizationIds` takes the business group. `searchTerm`, `organizationIds`, `tags` and `latestVersionsOnly` are on MuleSoft's page; `types`, `limit` and `offset` are `SearchAsset` fields it does not list, observed live. Paged by `limit`/`offset` with no total; a short page is the last. Without `latestVersionsOnly` an asset comes back once per version (790 rows for 371 assets across the root organisation); the v2 `assets` query that `anypoint-cli-v4 exchange asset list` sends returns every version too, so the SDK uses v1. The documented REST `GET /exchange/api/v2/assets/search` needs the root organisation's ID and documents no text filter | 2026-10-27 sandbox; [Searching the Graph API](https://docs.mulesoft.com/exchange/to-search-with-graph-api); `tests/fixtures/anypoint/exchange_search/`; plugin `lib/helpers/queries.js` |
| Wildcard/substring `query` server-side? (#53) | `search(query=)` | VERIFIED (LIVE) 2026-10-27 | No. `searchTerm` matches whole words and word prefixes of the asset ID, name and description, ignoring case (`sku`, `SKU`, `lookup`, `ddk-demo` and `keycloak`, a description word, match). It does not match inside words (`ku-loo` matches nothing), and `*` is not a wildcard (`*sku*` matches what `sku` does). So a plain `query` is sent as `searchTerm`, and a `query` with `*`, `?` or `[` is matched by the SDK over the asset ID, name and description, reading 250-asset pages with the other filters applied | 2026-10-27 sandbox probes; `exchange_search/search.term.json`, `search.description-term.json` |
| Which search filters are server-side? (#53) | `search()`, §6.1.3 | VERIFIED (LIVE) 2026-10-27 | `types` and `tags` are. Several types are ORed; several tags are ANDed, ignoring case (`crewai` + `langgraph` matches nothing). A tag filter also matches the keyed tags Exchange derives (`v0.3` matches every agent), so the SDK keeps an asset only when every tag is a keyless one, the kind set in Exchange or at publish. `domain` has no Exchange equivalent and stays blocked. Lifecycle is a v2 `assets(status: [...])` argument (`published`, `deprecated` and, per the page, `development`), not used by search yet | 2026-10-27 sandbox; `exchange_search/search.tags*.json`, `search.page{1,2}.json` |
| Publication mechanism for non-Mule assets (REST / CLI / Maven)? JVM needed? | §7 CI story | VERIFIED (CLI) | Maven + CLI: `agent-network project publish` publishes the built project to Exchange; **JVM required** (`mvnw`) | 2026-08-28: `agent-network:project:publish --help` |
| Publication uses Maven GAV coordinates | BG §2.5 | VERIFIED (CLI) | group-id/asset-id/asset-version; groupId defaults to org id | 2026-08-28: `:project:create --help`, `api-mgr:api:describe` |
| Documentation pages publishable? | BG §2.5 | PARTIAL | `exchange asset page` + `exchange asset resource` topics exist | 2026-08-28: `exchange:asset --help` |
| Can arbitrary metadata/tags be attached (content digest)? | BG §2.5, `donkey publish --if-changed` | VERIFIED (LIVE) 2026-10-26 | Yes, as a `properties.{key}` multipart field. Exchange keeps it only in the version's generated POM `<properties>` (not in GraphQL `attributes`, `labels` or `customFields`), so `donkey publish` sends `properties.ddkDigest` and reads it back from the POM, downloaded without credentials from the pre-signed `externalLink` the GraphQL `asset` query returns. A republish of an existing version is `409` `ASSET_PRE_CONDITIONS_FAILED` and leaves the version as it was, so a digest is never overwritten (#218) | 2026-10-26 sandbox publish; `tests/fixtures/anypoint/exchange_publish/` (`pom.xml`, `publish.409.json`, `graphql.latest.json`) |
| Asset lifecycle states (draft/published/deprecated) via API? | `require_lifecycle`, BG §2.5 | PARTIAL | `Deprecated` + `Public` flags on instances; A2D showed `status: draft\|published`. 2026-10-27 (#53): every asset has a `status`, and the v2 GraphQL `assets` query filters by the `Status` enum (`status: published` and `status: deprecated` were accepted live; the group has no deprecated asset). [Searching the Graph API](https://docs.mulesoft.com/exchange/to-search-with-graph-api) documents `development`, `published` and `deprecated` | 2026-08-28; 2026-10-27 |
| Native descriptor formats (MCP manifest, A2A card) vs file attach? | BG §2.5 output | VERIFIED (plugin) | typed Exchange files w/ fixed classifiers (`agent-metadata`, `mcp-metadata`, `llm-metadata`, `a2a-card`, `schema`, `mule-application`) attached to `agent-network` root asset. VERIFIED (LIVE) 2026-10-27 for a standalone `agent` asset (#1260): `files.agent-metadata.json` (`{protocol, platform, kind, name, description, connections, provenance}`) and, for protocol `a2a_v1`, `files.a2a-v1-card.json` (an A2A 1.0 card) are stored as sent, each under its classifier as `json`, `zip`, `fat-*` and `original-*` | 2026-08-28; 2026-10-27 | §12.7; `tests/fixtures/anypoint/exchange_publish/agent.*` |

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
| LangGraph | `langchain_openai.ChatOpenAI(model, base_url, api_key, default_headers, http_async_client, http_client, max_retries=0, callbacks, use_responses_api=False)` | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `langgraph==1.2.15`, `langchain-openai==1.7.1`, `langchain-core==1.6.10`, `openai==3.29.0`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: `ChatOpenAI.ainvoke` on the default `use_responses_api=True` (`POST /responses`) against `ddk-openai-model-routing` (`openai/gpt-5-mini`), and, in acceptance group 7, a one-node `StateGraph` turn with `use_responses_api=False` on `/chat/completions` against `ddk-pii-masking`, a single `azureopenai/gpt-5-mini` route. Offline: construction and `isinstance` against the recorded path confirmed offline with `langgraph==1.2.12`, `langchain-openai==1.6.6`, `langchain-core==1.6.5`. Re-checked offline 2026-10-14 (#1128, #1129, #1130) on the PR lock `langgraph==1.2.15`, `langchain-openai==1.7.1`, `langchain-core==1.6.10`, `openai==3.31.0`, `pydantic==2.14.1`, Python 3.12: `verify_frameworks.py --only langgraph` reports the signature ok; the `interrupt()` composition tests behind §8.2 pass; `nox -s langgraph-demo` (3.11) passes with conformance 4/4. langchain-openai 1.7.1 only adds `mid_conversation_*` fields to the resolved model profile (`mid_conversation_tools` is forced off unless the Responses API is used); the adapter reads no profile and passes the same constructor kwargs. The deep adapter (#198) defaults to `use_responses_api=False` (`/chat/completions`) since #1043, the one route every upstream behind an OpenAI-format proxy serves (§2, route matrix #894); `chat_model(..., use_responses_api=True)` opts into the live-verified `/responses` data plane. Also tested offline with `openai==2.54.0`. Re-checked offline 2026-10-16 (#1162) on the PR lock `langgraph==1.2.15`, `langchain-openai==1.7.2`, `langchain-core==1.6.11`, `openai==3.31.0`, `pydantic==2.14.1`, Python 3.12: `verify_frameworks.py --only langgraph` reports the signature ok. The `interrupt()` composition tests and `nox -s langgraph-demo` were not re-run for this bump. Re-checked offline 2026-10-19 (#1166, #1167) on the PR lock `langgraph==1.2.16`, `langchain-openai==1.7.2`, `langchain-core==1.6.12`, `openai==3.35.1`, `pydantic==2.14.1`, Python 3.12: `verify_frameworks.py --only langgraph` reports the signature ok; the whole suite passes (3300 passed, 271 skipped) and `smoke_example.py langgraph` passes the conformance kit; the `interrupt()` composition tests behind §8.2 pass (27 passed, 2 skipped in the langgraph-demo venv on Python 3.11, where the opentelemetry-gated ones run); `nox -s langgraph-demo` (3.11) passes with conformance 4/4. langgraph 1.2.16 leaves `interrupt()`, `Command` and `GraphInterrupt` byte-identical to 1.2.15 (diff of the installed source); its resume fixes (#9322, #9265) touch pending-write bookkeeping and the invalid-resume handling for `response_schema` validation, which `approval_interrupt()` does not pass, so the adapter is unchanged. langchain-core 1.6.12 (content-block-start extras, #41272) is in `language_models/_compat_bridge.py`; the adapter imports only `langchain_core.callbacks.BaseCallbackHandler`. This was not re-checked live; the live round-trip above is unchanged. Re-checked offline 2026-10-25 (#1204, #1211) on the PR lock `langgraph==1.2.17`, `langchain-openai==1.7.2`, `langchain-core==1.7.0`, `openai==3.37.0`, `pydantic==2.14.1`, Python 3.12: `verify_frameworks.py --only langgraph` reports the signature ok; the whole suite passes (3481 passed, 279 skipped) and `smoke_example.py langgraph` passes the conformance kit; the `interrupt()`/`Command(resume=...)` approval tests pass (19 passed, 10 skipped) and `nox -s langgraph-demo` (3.11) passes with conformance 4/4. langchain-core 1.7.0 changes only `language_models/_compat_bridge.py` (per-slice `tool-call-delta` events replace `block-delta` snapshots; diff of the installed source), which the adapter does not use; langgraph 1.2.17 leaves `interrupt()`, `Command` and `GraphInterrupt` byte-identical to 1.2.16. This was not re-checked live; the live round-trip above is unchanged. | 2026-10-13 | #34; `python/scripts/verify_frameworks.py --only langgraph` (also `--emit-verified`); `python/scripts/verify_frameworks.py --only langgraph --live`; acceptance group 7 |
| Google ADK | `google.adk.models.lite_llm.LiteLlm(model="openai/…", api_base, api_key, extra_headers, max_retries=0, client, llm_client)` | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `google-adk==2.11.0`, `litellm==1.105.0`, `openai==2.54.0`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: an `Agent` on the adapter's `LiteLlm`, one turn through `InMemoryRunner.run_async` (`POST /chat/completions`) against `ddk-openai-model-routing` (`openai/gpt-5-mini`); acceptance group 7 also ran it on `/chat/completions` against `ddk-pii-masking`, a single `azureopenai/gpt-5-mini` route. Offline: construction and `isinstance` against the recorded path confirmed offline with `google-adk==2.10.0`, `litellm==1.102.1`. Re-checked offline 2026-10-14 with `google-adk==2.12.0`, `litellm==1.105.1`, `openai==2.54.0`, `google-genai==2.30.1`, Python 3.12 (#1139, #1140): `verify_frameworks.py --only adk` gives signature ok and expected-class ok; `LiteLlm` still has exactly `model` and `llm_client`, `LiteLLMClient.acompletion(self, model, messages, tools, **kwargs)` is unchanged and still awaited as `llm_client.acompletion(...)` for streamed and non-streamed requests; the adk/litellm tests pass, including the `max_retries=0` request-count and typed-refusal-bridge tests. The 2.12.0 `LiteLlm` fixes (tool-call argument parsing, streamed tool-call thought signatures, tool-call id handling) and litellm 1.105.x's HTTP-pool refactor (BerriAI/litellm#43245, proxy-server side) were read from the release notes only, not exercised live. The pre-built OpenAI `client` sends through the SDK's shared client (#740), so every call carries the run's correlation id and `donkey.last_call` is `OBSERVED` after a call; the live turn above confirms the consumer-auth headers reach the proxy. A refusal surfaces as a LiteLLM error (`litellm.exceptions.APIError` for a PII 403, `InternalServerError` for a transport-raised `ModelSubstituted` or `GatewayUnavailable`) with no gateway headers and no `__cause__`; confirmed offline with `google-adk==2.11.0`, `litellm==1.104.2`, `openai==2.54.0` (#969). `LiteLlm.llm_client` (a pydantic field typed `LiteLLMClient`, present from `google-adk==2.4.0`) is awaited as `llm_client.acompletion(...)` for every model request, streamed or not, so `model()` passes a `LiteLLMClient` subclass that tags LiteLLM's error with the refusal the transport recorded for that call, and the typed-refusal bridge raises it typed (`tests/conformance/test_typed_refusal_bridge.py`). Also tested offline with `openai==2.54.0`. `max_retries=0` goes to LiteLLM, which sets it on `client` per call (default 2); one send per budget 429 confirmed by request count against a loopback proxy with `litellm==1.103.2` (#734, `tests/unit/test_framework_retries.py`). Re-checked offline 2026-10-19 with `google-adk==2.12.0`, `litellm==1.106.0`, `openai==2.54.0`, `google-genai==2.31.0`, Python 3.12, on the PR lock `adapter-contract-adk-py3.12.txt` (#1170): `verify_frameworks.py --only adk` gives signature ok and expected-class ok for `adk` and `adk.gemini` (live not run); the whole suite passes (3286 passed, 228 skipped for extras not installed on this leg), including the adk/litellm tests, the `max_retries=0` request-count tests and the typed-refusal-bridge tests. litellm 1.106.0's attribution-header change (BerriAI/litellm#44291) was read in the installed source: `BaseConfig.get_attribution_headers()` returns an empty mapping by default and only the Novita and Perplexity configs override it, `with_attribution_headers` lets caller headers win on a name clash and never mutates its input, and the adapter's `extra_headers` goes to the request through `optional_params["extra_headers"]`, outside that merge; no wire capture was taken and no live round-trip was run on 1.106.0. Re-checked offline 2026-10-22 with `google-adk==2.12.0`, `litellm==1.106.2`, `openai==2.54.0`, `google-genai==2.32.0`, Python 3.12, on the PR lock `adapter-contract-adk-py3.12.txt` (#1194): `verify_frameworks.py --only adk` gives signature ok and expected-class ok for `adk` and `adk.gemini` (live not run); the whole suite passes (3411 passed, 228 skipped for extras not installed on this leg), including the adk/litellm tests, the `max_retries=0` request-count tests and the typed-refusal-bridge tests. No live round-trip was run on 1.106.2. Re-checked offline 2026-10-24 with `google-adk==2.13.0`, `litellm==1.106.2`, `openai==2.54.0`, `google-genai==2.32.0`, Python 3.12, on the PR lock `adapter-contract-adk-py3.12.txt` with only `google-adk` moved to 2.13.0 (#1207): 2.13.0 iterates the model's response generator in a copy of the flow's context (`google.adk.utils._runner_utils._stream_in_caller_scope`, read in the installed source), so the record the transport sets stayed in that copy and an `after_model_callback` read `UNOBSERVED` (`test_adk_runner_is_unobserved_but_after_model_callback_sees_it` failed). `model()` and `gemini()` now return a subclass (`GovernedLiteLlm`, `GovernedGemini`, from `_last_call_bridged_class`) whose `generate_content_async`, a plain `def`, opens a `LastCallBridge` in the flow's context before ADK copies it, and closes it before the first complete (non-partial) response, because ADK runs that response's function calls (in tasks of their own on 2.13.0) before it resumes the generator. `verify_frameworks.py --only adk --emit-verified` gives signature ok and expected-class ok for `adk` and `adk.gemini` (the class check is `isinstance`; actual class `donkey_kit.integrations.adk.GovernedLiteLlm` / `GovernedGemini`), live not run; the same at the `google-adk==2.4.0` floor. The whole suite passes on 2.13.0 and on the 2.12.0 lock (3468 passed, 228 skipped each), and `scripts/smoke_example.py adk` passes on 2.13.0. A `LiteLlm` built from `connection_kwargs()` is not bridged, so on 2.13.0 its callback reads `UNOBSERVED`; re-checked offline 2026-10-26 with `google-adk==2.13.0` (#1218): `last_call_bridged(LiteLlm)` / `last_call_bridged(Gemini)` return the same `_last_call_bridged_class` subclass (once per class, an already-bridged class returned as is), and a model built from it with `connection_kwargs()` / `gemini_connection_kwargs()` reads `OBSERVED` in its `after_model_callback` under `InMemoryRunner` (`test_adk_hand_built_models_reach_after_model_callback_when_bridged`), while a plain `LiteLlm` from the same kwargs still reads `UNOBSERVED`. No live round-trip was run on 2.13.0. Re-checked offline 2026-10-25 with `google-adk==2.13.0`, `litellm==1.106.4`, `openai==2.54.0`, `google-genai==2.33.0`, Python 3.12, on the PR lock `adapter-contract-adk-py3.12.txt` (#1215, with #1207's 2.13.0 now in the lock): `verify_frameworks.py --only adk` gives signature ok and expected-class ok for `adk` and `adk.gemini` (actual classes `GovernedLiteLlm` / `GovernedGemini`; live not run); the whole suite passes (3475 passed, 228 skipped for extras not installed on this leg), including `test_adapter_async_last_call.py -k adk` (6 passed), and `scripts/smoke_example.py adk` passes. Per the release notes, litellm 1.106.3 changes the image cosign key and drops the eMudhra root CA from its trust bundles, and 1.106.4 fixes a proxy startup `ImportError`; none touches the `LiteLlm` kwargs the adapter sets, and `model()` sends TLS through the pre-built `AsyncOpenAI` client. No live round-trip was run on 1.106.4. | 2026-10-13 | #34; `python/scripts/verify_frameworks.py --only adk` (also `--emit-verified`); `python/scripts/verify_frameworks.py --only adk --live`; acceptance group 7 |
| Google ADK (native Gemini) | `google.adk.models.Gemini(model, base_url, client_kwargs={api_key, http_options={base_url, api_version="", headers, timeout, httpx_async_client}})` | VERIFIED (LIVE) | Round-trip through a `Format=Gemini` proxy with `google-adk==2.10.0`, `google-genai==2.25.0`: `generate_content_async` → 200, SSE streaming, and a tool-calling agent run. ADK builds its genai `Client` from `client_kwargs`, and through google-adk 2.12.0 `client_kwargs['http_options']` **replaces** ADK's default `http_options` wholesale, so the adapter passes the full set (from 2.13.0 ADK merges into it instead; see the 2.13.0 re-check below); an injected `httpx_async_client` disables genai's aiohttp path, so buffered and streamed calls both go through the shared `DonkeyAsyncClient`. `api_key` is required by genai and sent as `x-goog-api-key` (gateway-ignored placeholder). `timeout` is milliseconds (`None` would disable it). genai raises its own `google.genai.errors.APIError` whose `.response` is the httpx response, so `classify(exc.response)` works. `Gemini.client_kwargs` first appears in google-adk **2.4.0** (absent in 2.0.0 and 2.3.0, checked 2026-10-01); ADK's pydantic config ignores unknown fields, so on older versions the governed client is dropped silently. The `adk` extra floors at `google-adk>=2.4`, `gemini()` refuses a `Gemini` lacking either field with `_verify.blocked(...)`, and the round-trip test runs in CI at 2.4.0 and the newest release (#735). ADK caches one genai `Client` per event loop, and the shared `DonkeyAsyncClient` keeps one connection pool per loop, so one Donkey serves repeated `asyncio.run(...)` calls (#807). Signature re-checked offline 2026-10-14 with `google-adk==2.12.0`, `google-genai==2.30.1` (#1139): `Gemini` still has `base_url` and `client_kwargs`, `verify_frameworks.py --only adk` reports `adk.gemini` signature ok, and `test_adapter_contract.py::test_streamed_call[adk.gemini]` passes; no live Gemini round-trip was run on 2.12.0. Re-checked offline 2026-10-24 with `google-adk==2.13.0`, `google-genai==2.32.0`, Python 3.12 (#1207): `Gemini` still has `base_url` and `client_kwargs`, and `verify_frameworks.py --only adk --emit-verified` reports `adk.gemini` signature ok and expected-class ok (`isinstance`; actual class `donkey_kit.integrations.adk.GovernedGemini`), live not run. Two 2.13.0 changes were read in the installed source. (1) `Gemini._with_adk_http_options` no longer lets `client_kwargs['http_options']` replace ADK's options wholesale: it fills only the `api_version`, `base_url` and `retry_options` the caller left unset, so the caller's fields win, and it merges ADK's tracking values into the caller's `headers` (`merge_tracking_headers`, which keeps every other header). The adapter sets `base_url` and `api_version` itself, so neither is filled. 2.12.0 already merged the tracking values per request in `generate_content_async`, so the wire is unchanged: an offline capture through `MockTransport` on 2.12.0 and 2.13.0 shows the same `POST <proxy>/models/gemini-2.5-flash:generateContent` with `client_id`, `client_secret`, `x-goog-api-key` and the `x-goog-api-client`/`user-agent` tracking values, differing only in the `google-adk/<version>` token. (2) `_stream_in_caller_scope` iterates the response generator in a copy of the flow's context, so `gemini()` returns the bridged subclass described in the Google ADK row; `test_adk_runner_is_unobserved_but_after_model_callback_sees_it` and `test_adk_streamed_run_after_model_callback_sees_every_response` fail on 2.13.0 without it and pass with it, as they do on 2.12.0 and 2.4.0. A `Gemini` built from `gemini_connection_kwargs()` is not bridged, so on 2.13.0 its callback reads `UNOBSERVED`, unless its class is `last_call_bridged(Gemini)` (#1218, re-checked offline 2026-10-26 with `google-adk==2.13.0`; see the `LiteLlm` row). Streamed usage re-checked offline 2026-10-26 with `google-adk==2.13.0`, `google-genai==2.33.0` (#1219). An earlier offline check saw `donkey.last_call` usage stay `None` for a streamed call. That was the test double: `httpx.Response(content=...)` reads the body when the response is built, so the transport's stream wrapper never scans the SSE events. Delivered event by event, as from the network, the recorded `responses.stream.*` fixture leaves usage `None` in the partial callbacks, and the complete response's callback reads the last event's counts (`total_tokens == 38`). The same holds for `model()` with a chat-completions stream whose last event carries `usage`. `test_adk_streamed_run_after_model_callback_sees_every_response` and `test_adk_model_streamed_run_usage_lands_on_the_complete_response` pin both, and both fail with a `content=` body. No live streamed round-trip was run. No live Gemini round-trip was run on 2.13.0. Re-checked offline 2026-10-25 with `google-adk==2.13.0`, `google-genai==2.33.0`, Python 3.12 (#1215 lock): `verify_frameworks.py --only adk` reports `adk.gemini` signature ok and expected-class ok (`GovernedGemini`), live not run. No live Gemini round-trip was run on `google-genai` 2.33.0. | 2026-10-01 | #691, #735; `python/scripts/verify_frameworks.py --only adk.gemini --live` against `ddk-gemini-inbound` |
| MS Agent Framework | `agent_framework.openai.OpenAIChatClient(model, base_url, api_key, default_headers)` — the **Responses API** client (`POST /responses`), built by `chat_client(..., api="responses")`; `agent_framework.openai.OpenAIChatCompletionClient(…)` with the same kwargs — the **Chat Completions** client (`POST /chat/completions`), built by `chat_client()` by default (#1043) | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `agent-framework==1.21.0`, `agent-framework-openai==1.16.0`, `openai==3.29.0`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: one `Agent.run` turn on each client — the default Chat Completions client (`POST /chat/completions`) and the `api="responses"` client (`POST /responses`) — against `ddk-openai-model-routing` (`openai/gpt-5-mini`); acceptance group 7 also ran it on `/chat/completions` against `ddk-pii-masking`, a single `azureopenai/gpt-5-mini` route (Chat Completions client). Offline: construction and `isinstance` against both recorded paths confirmed offline with `agent-framework==1.19.0` / `agent-framework-openai==1.14.4`, and re-checked at `agent-framework==1.21.0` / `agent-framework-openai==1.16.0` / `openai==3.31.0` (#1126): `verify_frameworks.py --only agent_framework` still resolves `OpenAIChatCompletionClient` and `OpenAIChatClient` with signature ok, both accept `model`, `base_url`, `api_key`, `default_headers`, `async_client` and neither accepts `model_id`. The constructor kwarg is `model=` — `model_id` is **not** accepted. `base_url`/`api_key`/`default_headers`/`async_client` are accepted by both, so `connection_kwargs()` serves either. Both chain the openai error as `__cause__` of `ChatClientException` (`OpenAIContentFilterException` for a content-filter 400), so `policy_middleware()` covers both. The default is `/chat/completions` (#1043), the only route every upstream serves (§2 per-upstream route matrix, #894); `/responses` is the opt-in for OpenAI-routed proxies. The switch traces back to an Azure OpenAI route that answered `/responses` with **404** `Resource not found` and `/chat/completions` with 200 in one live probe of `ddk-azure-openai-model-routing` (2026-09-30, #826); re-captured 2026-10-07 as the §2 fixture `src/donkey_kit/simulator/_fixtures/anypoint/azure_openai_routing/` (#896, moved in #895); the `/chat/completions` route itself is recorded in §2 (Chat Completions row). Also confirmed offline at the `agent-framework==1.13.0` extras floor, where the call reaches the proxy (`test_framework_redirects.py`). 1.12.0 constructs but sends elsewhere (§8.3, #743). (Reclassified from a bare `VERIFIED` per #681 — same offline-only evidence class as its peer rows; §0.3 reserves `VERIFIED` for a real-sandbox round-trip.) Re-checked offline 2026-10-16 at `agent-framework==1.22.0` / `agent-framework-openai==1.17.0` / `openai==3.31.0` (#1156): `verify_frameworks.py --only agent_framework` resolves `agent_framework_openai._chat_completion_client.OpenAIChatCompletionClient` and `agent_framework_openai._chat_client.OpenAIChatClient` with signature ok and expected-class ok; the constructor kwarg set was not re-listed. The live round-trip was not re-run. Re-checked offline 2026-10-25 with `agent-framework==1.23.0`, `agent-framework-core==1.23.0`, `agent-framework-openai==1.17.0`, `openai==3.37.0`, `mcp==2.3.0`, `pydantic==2.14.1`, Python 3.12, on the PR lock `adapter-contract-agent_framework-py3.12.txt` (#1203, which also moved `mcp` 1.30.0 → 2.3.0): `verify_frameworks.py --only agent_framework agent_framework.responses --require-installed` resolves `OpenAIChatCompletionClient` and `OpenAIChatClient` with signature ok and expected-class ok (live not run), and both still accept `model`, `base_url`, `api_key`, `default_headers` and `async_client`; the whole suite passes (3588 passed, 182 skipped) and `smoke_example.py agent_framework` passes the conformance kit. `agent-framework-openai` is unchanged at 1.17.0. The live round-trip was not re-run. | 2026-10-13 | #34, #520, #826 (`verify_frameworks.py --only agent_framework`); `python/scripts/verify_frameworks.py --only agent_framework --live`; acceptance group 7 |
| MS Agent Framework (middleware) | `agent_framework.chat_middleware` decorator; `ChatContext.stream` / `.result`; `ResponseStream.with_pull_context_manager(cm_factory)`; `agent_framework.exceptions.ChatClientException` chaining the openai error as `__cause__` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Confirmed offline with `agent-framework==1.19.0`: `Agent(middleware=[...])` accepts a `@chat_middleware`-marked function (an undecorated `(context, next)` function whose first parameter is not annotated `ChatContext` raises `MiddlewareException: Cannot determine middleware type`). `OpenAIChatClient._handle_request_error` re-raises every openai error as `ChatClientException` from the original, so `policy_middleware()` runs the call inside the typed-refusal bridge (`core/refusals.py`, #724), whose chain walk reaches the `openai.APIStatusError` and classifies its response in core; the adapter's `refusal_translator` sees through `agent_framework.exceptions.AgentFrameworkException` (the base of `ChatClientException`, confirmed offline with `agent-framework-core==1.20.0`), so an unreachable gateway surfaces as `GatewayUnavailable`. A streamed refusal surfaces on the caller's pull, after the middleware has returned; `with_pull_context_manager` wraps each pull, so the same conversion applies. Pinned by `test_agent_framework_policy_middleware.py` on a real `Agent` (one send, typed `PIIDetected` with the run's correlation id, streaming and non-streaming). Re-checked at `agent-framework==1.21.0` / `agent-framework-core==1.21.0` (#1126): the `agent-framework-middleware` session passes, `ResponseStream.with_pull_context_manager(cm_factory)` and `chat_middleware` are unchanged, `ChatClientException` still subclasses `AgentFrameworkException`, and the two streaming fixes in 1.21.0 (delegated provider streams close on release, microsoft/agent-framework#9100; streamed logprobs kept across empty chunks, #8997) change cleanup ordering and one metadata field, not the pull-context wrapping or the exception chain. No live round-trip. Re-checked at `agent-framework==1.22.0` / `agent-framework-core==1.22.0` (#1156): the `agent-framework-middleware` session's test files pass (96 passed, 55 skipped for other frameworks, 0 failed); the 1.22.0 release notes and API surface were not otherwise inspected. Re-checked offline 2026-10-25 at `agent-framework==1.23.0` / `agent-framework-core==1.23.0` (#1203): `_middleware.py` (`chat_middleware`, `ChatContext`) and `exceptions.py` are byte-identical to 1.22.0 (diff of the two wheels), `ResponseStream.with_pull_context_manager(cm_factory)` keeps its signature, and the middleware and typed-refusal-bridge tests pass on the contract lock (50 passed, 42 skipped for other frameworks, 0 failed); the `agent-framework-middleware` CI session passed on the #1206 lock. The 1.23.0 release notes were not otherwise inspected. | 2026-10-16 | #739, #1126, #1156 |
| OpenAI Agents SDK | `agents.OpenAIChatCompletionsModel(model, openai_client=openai.AsyncOpenAI(base_url, api_key, default_headers, http_client, max_retries=0))` | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `openai-agents==0.23.1`, `openai==3.29.0`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: an `Agent` on the adapter's model, one turn through `Runner.run` (`POST /chat/completions`) against `ddk-openai-model-routing` (`openai/gpt-5-mini`); acceptance group 7 also ran it on `/chat/completions` against `ddk-pii-masking`, a single `azureopenai/gpt-5-mini` route. Offline: construction and `isinstance` against the recorded path confirmed offline with `openai-agents==0.20.0`. Also tested offline with `openai==2.54.0`. Re-checked offline 2026-10-14 (#1142) at `openai==3.31.0` with `openai-agents==0.23.1` (the PR lock): `verify_frameworks.py --only openai_agents` resolves `agents.models.openai_chatcompletions.OpenAIChatCompletionsModel` with the signature ok; `openai.AsyncOpenAI.__init__` still takes `base_url`, `api_key`, `default_headers`, `http_client` (typed `httpx2.AsyncClient | None`) and `max_retries`; the adapter-contract suite and the strict example smoke pass. openai 3.30.0 drains an async HTTP/1.1 stream for at most 0.1 s after `[DONE]` (openai/openai-python#4041); through the bridge the forwarded stream is already consumed, so the drain hits `httpx.StreamConsumed`, which openai catches, and the response is closed rather than pooled. Streams complete with no added latency or hang, as at 3.24.0. The live round-trip was not re-run. Re-checked offline 2026-10-16 (#223, promotion to a deep, conformance-gated adapter) at `openai-agents==0.24.0` with `openai==3.31.0` (the new `openai-agents-demo` lock): `verify_frameworks.py --only openai_agents` resolves the class with the signature ok; the installed constructor is `OpenAIChatCompletionsModel(model, openai_client, should_replay_reasoning_content=None, strict_feature_validation=False, buffer_streamed_tool_calls=False)`, and the adapter passes only `model` and `openai_client`. The demo additionally relies on the framework's own documented `agents.Agent(name, instructions, model, tools)`, `agents.Runner.run(starting_agent, input, *, run_config=…)`, `agents.RunConfig(tracing_disabled=True)` and `agents.RunResult.final_output`, all confirmed present at 0.24.0; `Agent.instructions` accepts a `(RunContextWrapper, Agent) -> str` callable. The four public conformance scenarios and the Runner-level correlation test (`tests/unit/test_openai_agents_correlation.py`) pass against it. The live round-trip was not re-run. Re-checked offline 2026-10-19 (#1171) at `openai==3.35.1` with `openai-agents==0.24.0` (the PR lock, Python 3.12, installed with the adapter-contract recipe): `verify_frameworks.py --only openai_agents` resolves `agents.models.openai_chatcompletions.OpenAIChatCompletionsModel` with the signature ok; `openai.AsyncOpenAI.__init__` still takes `base_url`, `api_key`, `default_headers`, `http_client` (typed `httpx2.AsyncClient | None`) and `max_retries`, and `openai.DefaultAsyncHttpxClient` is still built on `httpx2.AsyncClient`; the full suite (3266 passed, 273 skipped for other frameworks' extras) and the strict example smoke pass. The live round-trip was not re-run. Re-checked offline 2026-10-25 with `openai-agents==0.24.0`, `openai==3.37.0`, `pydantic==2.14.1`, Python 3.12, on the PR lock `adapter-contract-openai-agents-py3.12.txt` (#1205): `verify_frameworks.py --only openai_agents` gives signature ok and expected-class ok (live not run); the whole suite passes (3447 passed, 281 skipped for other frameworks' extras) and `smoke_example.py openai_agents` passes the conformance kit. openai 3.37.0's breaking change is confined to the Decisions and Live types (release notes), which the adapter does not use. The live round-trip was not re-run. | 2026-10-13 | #34; #223; `python/scripts/verify_frameworks.py --only openai_agents` (also `--emit-verified`); `python/scripts/verify_frameworks.py --only openai_agents --live`; acceptance group 7 |
| OpenAI Agents SDK (tool approval, #213) | `agents.function_tool(func, needs_approval=True, failure_error_function=…)` → `FunctionTool.on_invoke_tool(ctx, arguments)`; `RunContextWrapper.is_tool_approved(tool_name, call_id) -> bool | None`; `ToolContext.tool_name` / `.tool_call_id`; `RunResult.interruptions` / `.to_state()`; `RunState.get_interruptions()`, `.approve(item, always_approve=False)`, `.reject(item, always_reject=False, *, rejection_message=None)`, `.to_string()`, `await RunState.from_string(agent, s)`; `ToolApprovalItem.name` / `.call_id` / `.arguments` (JSON str) / `.agent`; a tool error re-raised by the failure function leaves `Runner.run` as `UserError("Error running tool …")` with it as `__cause__` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | Read from installed source and exercised offline against the mocked transport (`tests/unit/test_openai_agents_approvals.py`, `tests/conformance/test_approval_round_trip.py`) at `openai-agents==0.20.0` (floor) and `0.24.0`. A callable `needs_approval` is not called when the arguments do not parse, and the call is held for approval (`agents/util/_approvals.py` `evaluate_function_tool_approval` at 0.24, `agents/run_internal/tool_execution.py` `function_needs_approval` at 0.20), so the adapter sets `True` and records the pending approval in `pending_approvals()`. The gated tool keeps the SDK invoker inside its own and honours the invoker protocol `FunctionTool.__post_init__` / `__copy__` read with `getattr` (`__agents_bind_function_tool__`, same at both versions), so `copy.copy`, `agents.tool_namespace()` and `dataclasses.replace` rebind the gate to the copy; `FunctionTool.__wrapped__` answers only for the SDK's own invoker class, so the tool is re-classed as a `FunctionTool` subclass (supported by `FunctionTool.__copy__`) that reads `__wrapped__` off the gate. `RunState.get_interruptions()` still lists a decided call and no public accessor says whether it was decided, so `approve`/`reject` track decided ids per `RunState` object; each `RunResult.to_state()` builds a new state (over the shared context at 0.20.0, a copied one at 0.24.0), so a decision on another `to_state()` or a `from_string()` state is not seen. Not live round-tripped. | 2026-10-26 | `agents/tool.py` (`function_tool`, `FunctionTool.__post_init__` / `__copy__` / `__wrapped__`, `_FailureHandlingFunctionToolInvoker.__agents_bind_function_tool__`, `tool_namespace`, `default_tool_error_function`); `agents/result.py` (`RunResult.to_state`); `agents/run_state.py` (`RunState`); `agents/run_context.py` (`RunContextWrapper.is_tool_approved`); `agents/tool_context.py` (`ToolContext`); `agents/items.py` (`ToolApprovalItem`); `agents/run_internal/tool_execution.py` (`UserError` wrap) |
| Anthropic SDK | `anthropic.AsyncAnthropic(base_url, api_key, default_headers, http_client, max_retries=0)` | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `anthropic==1.13.0`, `httpx2==2.13.1`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: `messages.create` (`POST /v1/messages`) against the `Format=Anthropic` proxy `ddk-anthropic-inbound` (`claude-haiku-4-5-20251001`), in the harness and in acceptance group 7. Offline: construction and `isinstance` against the recorded path confirmed offline with `anthropic==0.116.0`, and again with `anthropic==0.125.0` and `anthropic==1.9.0` (`httpx2==2.13.1`) after #701. Re-checked offline on 2026-10-14 against the PR lock at `anthropic==1.13.0` (`httpx2==2.13.1`, `openai==3.31.0`, Python 3.12, #1127): `verify_frameworks.py --only anthropic` reports the signature ok; the constructor still takes `base_url`, `api_key`, `default_headers`, `http_client` and `max_retries`; `DefaultAsyncHttpxClient` is still an `httpx2.AsyncClient`; an `httpx` `http_client` is still rejected with `TypeError`; the default `anthropic-version` is still `2023-06-01` (now `ANTHROPIC_API_VERSION` in `_constants.py`, a rename). 1.13.0 also tracks an explicit timeout in a write-only `_timeout_overridden` flag behind a `timeout` property, which changes nothing for this adapter, as it passes no `timeout`. `http_client` is the shared `DonkeyAsyncClient`'s non-owning view on `anthropic<1` (#733) and a bridged `httpx2.AsyncClient` on `anthropic>=1.0` (§8.1). Model id remains per-call; the separately live-verified Anthropic Messages ingress (§2, #304) requires a `Format=Anthropic` proxy. Also tested offline with `openai==2.54.0` (0.116.0) and `openai==3.22.1` (1.9.0). Re-checked offline 2026-10-16 against the PR lock at `anthropic==1.15.0` (`httpx2==2.13.1`, `openai==3.31.0`, Python 3.12, #1161): `verify_frameworks.py --only anthropic` reports the signature ok; the constructor still takes `base_url`, `api_key`, `default_headers`, `http_client` and `max_retries` (it also accepts `auth_token`, `credentials`, `config`, `profile`, `webhook_key`, `open_telemetry` and `middleware`, none of which the adapter passes); `DefaultAsyncHttpxClient` is still an `httpx2.AsyncClient`; an `httpx` `http_client` is still rejected with `TypeError`; the default `anthropic-version` is still `2023-06-01` (`ANTHROPIC_API_VERSION` in `_constants.py`). The live round-trip was not re-run. Re-checked offline 2026-10-22 against the PR lock at `anthropic==2.1.0` (`httpx2==2.16.0`, `openai==3.36.1`, Python 3.12, #1195): `verify_frameworks.py --only anthropic` reports the signature ok and expected-class ok; the constructor still takes `base_url`, `api_key`, `default_headers`, `http_client` and `max_retries`; `DefaultAsyncHttpxClient` is still an `httpx2.AsyncClient`; an `httpx` `http_client` is still rejected with `TypeError`; the whole suite passes (3318 passed, 299 skipped for extras not installed on this leg). **The default `anthropic-version` changed:** 2.0.0 ("add support for new API version 2026-10-21", marked breaking) sets `ANTHROPIC_API_VERSION` to `2026-10-21`, so the client now sends `anthropic-version: 2026-10-21` unless the caller overrides it in `default_headers`. The live round-trip above, and the §2 native-ingress row, were taken with `2023-06-01`. **Live round-trip 2026-10-22** (#1195) with `anthropic==2.1.0`, `httpx2==2.16.0`, Python 3.12, from source on the PR lock: `verify_frameworks.py --only anthropic --live` sends `messages.create` (`POST /v1/messages`) with the new default `anthropic-version: 2026-10-21` through the `Format=Anthropic` proxy `ddk-anthropic-inbound` (`claude-haiku-4-5-20251001`) and gets a completion back, so the gateway passes the new version through. Acceptance group 7 was not re-run. Re-checked offline 2026-10-25 with `anthropic==2.3.0`, `httpx2==2.16.0`, Python 3.12, on the PR lock `adapter-contract-anthropic-py3.12.txt` (#1209): `verify_frameworks.py --only anthropic` gives signature ok and expected-class ok (live not run); the whole suite passes (3438 passed, 299 skipped) and `smoke_example.py anthropic` passes the conformance kit. Per the 2.2.0/2.3.0 changelog, the request-layer changes are limited to beta routes; nothing touches the `AsyncAnthropic` kwargs, `messages.create` or the error classes the adapter uses. No live round-trip was run on 2.3.0. | 2026-10-22 | #34, #701, #1127, #1161, #1195; `python/scripts/verify_frameworks.py --only anthropic` (also `--emit-verified`); `python/scripts/verify_frameworks.py --only anthropic --live`; acceptance group 7 |
| CrewAI | `crewai.LLM(model="openai/…", base_url, api_key, extra_headers, max_retries=0, interceptor)` → returns `crewai.BaseLLM` (concretely `OpenAICompletion`) | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `crewai==1.15.27`, `openai==2.54.0`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: an `Agent` on the adapter's LLM, one `Task` through `Crew.kickoff` (`POST /chat/completions`) against `ddk-openai-model-routing` (`openai/gpt-5-mini`); acceptance group 7 also ran it on `/chat/completions` against `ddk-pii-masking`, a single `azureopenai/gpt-5-mini` route. Offline: construction and `isinstance` confirmed offline with `crewai==1.15.22`, `openai==2.54.0` — against the concrete `OpenAICompletion` class the factory returns, **not** `crewai.LLM`. `crewai.LLM.__new__` is a documented factory: an `openai/`-prefixed model with an explicit `base_url` (which `connection_kwargs()` always supplies) routes to and returns `crewai.llms.providers.openai.completion.OpenAICompletion`, CrewAI's native OpenAI provider and a sibling `crewai.BaseLLM` subclass, not a `crewai.LLM` instance (`isinstance(obj, crewai.LLM)` is false; `isinstance(obj, crewai.BaseLLM)` is true). That provider strips the `openai/` prefix and does not go through LiteLLM, which is only the factory's fallback route. `crewai.BaseLLM` (`crewai.llms.base_llm.BaseLLM`, a public top-level export) is the correct common return type and the adapter's annotation, but every provider is one, so the harness pins the concrete `OpenAICompletion` class: a routing change fails the check instead of passing as another `BaseLLM` (#640). The prior `crewai.LLM` expectation and its `CLASS RENAMED` verdict were an artifact of checking against the wrong class, not a real incompatibility. `extra_headers` is not a named `OpenAICompletion` field: `BaseLLM` collects it into `additional_params`, which `OpenAICompletion` merges into its request parameters, so the proxy auth headers arrive by that passthrough rather than a named parameter — the silently-absorbed-kwarg case above (seen on the wire against a local stub server, sync and streaming; not a sandbox round-trip). `max_retries=0` reaches the provider's OpenAI client (a 503 is sent once); CrewAI's own `BaseLLM` rate-limit retry still sends a budget 429 3 times and has no off switch, an asserted exemption confirmed with `crewai==1.15.23` (#734, `tests/unit/test_framework_retries.py`). Re-confirmed 2026-10-12 with `crewai==1.15.27`, `openai==2.54.0` (#1116): the signature check still resolves to `OpenAICompletion`, and the `adapter-contract(crewai)` suite, retry exemption included, passes. Re-confirmed 2026-10-14 with `crewai==1.15.28`, `openai==2.54.0` (#1141): the signature check still resolves to `OpenAICompletion`, and the crewai conformance, retry-exemption and adapter tests pass. The 1.15.28 change the AI impact review flagged, context-window sizes now read from a bundled LiteLLM catalog (crewAIInc/crewAI#8077), does not touch this row: `crewai/llms/providers/openai/completion.py`, `crewai/llms/base_llm.py`, `crewai/llms/retry.py` and `crewai/llms/hooks/transport.py` are byte-identical to 1.15.27 (diff of the two wheels), the `crewai.LLM.__new__` routing is unchanged (docstring-only edit), and the adapter sets no context-window option. The live round-trip above is still the 1.15.27 one; no `--live` run was made at 1.15.28. Re-checked offline 2026-10-25 with `crewai==1.15.30`, `openai==2.54.0`, `mcp==1.30.0`, Python 3.12, on the PR lock `adapter-contract-crewai-py3.12.txt` (#1210): `verify_frameworks.py --only crewai` gives signature ok and expected-class ok (`OpenAICompletion`; live not run); the whole suite passes (3489 passed, 256 skipped) and `smoke_example.py crewai` passes the conformance kit. 1.15.29's LiteLLM-compatible kwargs enter `crewai.LLM.__new__` only through an active `llm_overlay` (`_overlay_mapped`, read in the installed source); without one, `openai/…` still routes to the native `OpenAICompletion` with the adapter's kwargs. No `--live` run was made at 1.15.30. | 2026-10-13 | #34, #640, #1116, #1141; `python/scripts/verify_frameworks.py --only crewai` (also `--emit-verified`); `python/scripts/verify_frameworks.py --only crewai --live`; acceptance group 7 |
| LlamaIndex | `llama_index.llms.openai_like.OpenAILike(model, api_base, api_key, default_headers, is_chat_model=True, is_function_calling_model=True)` | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `llama-index-core==0.14.25`, `llama-index-llms-openai-like==0.8.1`, `llama-index-llms-openai==0.8.3`, `openai==2.54.0`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: a `FunctionAgent` on the adapter's LLM, one `run` turn (`POST /chat/completions`) against `ddk-openai-model-routing` (`openai/gpt-5-mini`); acceptance group 7 also ran it on `/chat/completions` against `ddk-pii-masking`, a single `azureopenai/gpt-5-mini` route. Offline: construction and `isinstance` against the recorded path confirmed offline with `llama-index-llms-openai-like==0.8.0`. `verify_frameworks.py --only llamaindex` re-run 2026-10-14 on 0.8.1: signature ok, resolves to `llama_index.llms.openai_like.base.OpenAILike`. Also tested offline with `openai==2.54.0`. `http_client` / `async_http_client` carry the SDK's shared client (#740), so every call carries the run's correlation id and `donkey.last_call` is `OBSERVED` after a call. LlamaIndex raises the openai `APIStatusError` on a refusal; `typed_refusals()` converts it to the SDK's typed error. Caveat (#829): LlamaIndex keys reasoning-model handling (`llama_index.llms.openai.utils.O1_MODELS`) and `context_window` (`openai_modelname_to_contextsize`) on the exact bare OpenAI name, and `OpenAILike` defaults `context_window` to 3,900. `LlamaIndexAdapter.llm()` resolves both from the name after one `<provider>/` prefix. A prefixed reasoning model gets `temperature=1.0`, plus `max_completion_tokens` / `reasoning_effort` via `additional_kwargs`. Re-confirmed offline 2026-10-14 against `llama-index-llms-openai==0.8.3` (with `llama-index-workflows==2.26.0`): `O1_MODELS` still keys on the exact bare name (`gpt-5-mini` is in it, `openai/gpt-5-mini` is not), `openai_modelname_to_contextsize` still raises `ValueError` for a prefixed name, and `OpenAILike` still defaults `context_window` to 3,900 and `is_chat_model` to `False`. A prefixed `openai/gpt-5-mini` still resolves to `context_window=400000`, `temperature=1.0`, and `max_tokens` / `reasoning_effort` in `additional_kwargs`. 0.8.3 adds `gpt-6.1-sol` (1,050,000) to `O1_MODELS`, which the adapter picks up from the table. `connection_kwargs()` carries no model, so it does not apply these. | 2026-10-13 | #34; #829; `python/scripts/verify_frameworks.py --only llamaindex` (also `--emit-verified`); `python/scripts/verify_frameworks.py --only llamaindex --live`; acceptance group 7 |
| Strands | `strands.models.openai.OpenAIModel(model_id, client_args={base_url, api_key, default_headers, http_client, max_retries=0}, stream=False)` | VERIFIED (LIVE) | **Live round-trip 2026-10-13** (#34) with `strands-agents==1.60.0`, `openai==2.54.0`, on the published `donkey-kit==0.1.3.dev0` (TestPyPI), Python 3.12: an `Agent` on the adapter's model, one turn through `invoke_async` (`POST /chat/completions`) against `ddk-openai-model-routing` (`openai/gpt-5-mini`); acceptance group 7 also ran it on `/chat/completions` against `ddk-pii-masking`, a single `azureopenai/gpt-5-mini` route, two turns followed by a `donkey.llm` call on the same `Donkey` (#852). Offline: construction and `isinstance` against the recorded path confirmed offline with `strands-agents==1.60.0` (`verify_frameworks.py --only strands`: signature ok, `OpenAIModel`, with `openai==2.54.0`, #1132; earlier `1.57.1`). `client_args.max_retries=0` reaches the OpenAI client; one send per budget 429 from the model and from `Agent(retry_strategy=None)` confirmed by request count with `strands-agents==1.57.1`, `openai==3.22.1` (#734, `tests/unit/test_framework_retries.py`); re-run with `strands-agents==1.60.0`, `openai==2.54.0` (`strands-agents[openai]` pins `openai<3`): `test_framework_retries.py` and `test_strands_terminal_refusal.py` pass (the `openai==3.22.1` leg was not re-run). Error wrappers, for the typed-refusal bridge (#724): `strands.types.exceptions.ModelThrottledException`, `ContextWindowOverflowException` and `EventLoopException` are each raised `from` the original error and carry no request or response, so the adapter's `refusal_translator` hands their `__cause__` back to `core.refusals.translate()`; confirmed offline with `strands-agents==1.57.2`, re-checked with `1.60.0` (`tests/unit/test_refusal_bridge.py`, passes). Agent throttle retry (#951), read from the installed source of `strands-agents==1.57.2`, and re-read from `1.60.0`, with `openai==2.54.0` (the `openai` extra pins `openai<3`): `OpenAIModel.stream` (`strands/models/openai.py`) raises `ModelThrottledException(str(error)) from error` for an openai error that `classify_openai_error` calls throttling, a 429 included; `Agent(retry_strategy=...)` defaults to `strands.ModelRetryStrategy` (`strands/event_loop/_retry.py`), whose public `is_retryable()` is `isinstance(exception, ModelThrottledException)` and which sets `AfterModelCallEvent.retry` up to `max_attempts=6`; `_handle_model_execution` (`strands/event_loop/event_loop.py`) re-raises a non-retried exception as it is (`raise e`), still so at 1.60.0, which changed `OpenAIModel.stream` (usage read from the `finish_reason` chunk, strands-agents#4851; the `reasoningContent` warning logged once per request, #5016) but not the throttle path (`classify_openai_error`, then `ModelThrottledException(str(error)) from error`), and `event_loop_cycle` re-raises a model-call error without wrapping it in `EventLoopException`. So `model()` returns an `OpenAIModel` subclass whose `stream` (the public `Model.stream` interface) raises `TokenBudgetExceeded` in place of the throttle a budget 429 becomes: a default `Agent` sends it once, and `invoke_async()` and `__call__` raise that same object; a throttle the transport did not send passes through and is retried 3 times under `ModelRetryStrategy(max_attempts=3)`. Confirmed offline by request count with `simulate()` and a loopback proxy (`tests/unit/test_strands_terminal_refusal.py`, `tests/unit/test_framework_retries.py`). Re-checked offline 2026-10-16 with `strands-agents==1.61.1`, `openai==2.54.0` (#1163): `verify_frameworks.py --only strands` reports the signature ok and resolves `OpenAIModel`. The retry, refusal-bridge and throttle-path checks above were not re-run at 1.61.1. The live round-trip was not re-run. | 2026-10-13 | #34; `python/scripts/verify_frameworks.py --only strands` (also `--emit-verified`); `python/scripts/verify_frameworks.py --only strands --live`; acceptance group 7 |
| Strands (HITL, #1234) | `strands.hooks.BeforeToolCallEvent.interrupt(name, reason=…)` → `strands.interrupt.InterruptException`; `BeforeToolCallEvent.cancel_tool`; `HookProvider.register_hooks(registry)`; `AgentResult.interrupts`; resume `Agent.invoke_async([{"interruptResponse": {"interruptId", "response"}}])` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | `integrations.strands.ApprovalHook` pauses a `@donkey.tool(approval="required")` call from `BeforeToolCallEvent` with Strands' own interrupt (BG §2.3). Read from the installed source of `strands-agents==1.62.0` and confirmed to behave the same at the `1.57.1` floor: `_Interruptible.interrupt` (`strands/types/interrupt.py`) stores an `Interrupt` keyed `v1:before_tool_call:{toolUseId}:{uuid5(name)}` on `agent._interrupt_state`, returns the stored response when the agent is resumed on an `interruptResponse`, and otherwise raises `InterruptException` (`strands/interrupt.py`); `HookRegistry.invoke_callbacks_async` (`strands/hooks/registry.py`) runs a sync callback in the caller's context and collects every interrupt the callbacks raise, so the concurrent executor (`strands/tools/executors/concurrent.py`) pauses every gated call in a turn together and the sequential one pauses on each in turn; a string `cancel_tool` becomes an error tool result carrying it (`strands/tools/executors/_executor.py`), which is how a rejection reaches the model; a sync tool runs under `asyncio.to_thread` (`strands/tools/decorator.py`), which copies the context, so the approval set in the hook reaches the gate on the worker thread. Offline: `tests/unit/test_strands_approvals.py` (approve sync and async, reject, both executors, retry, a fresh hook, real span events) and the Strands leg of `tests/conformance/test_approval_round_trip.py` pass on both versions. Not run live. | — | offline: `tests/unit/test_strands_approvals.py`; `tests/conformance/test_approval_round_trip.py` |
| Google ADK (HITL, #1235) | `google.adk.plugins.BasePlugin(name)` with `before_tool_callback(*, tool, tool_args, tool_context)` / `after_tool_callback` / `on_tool_error_callback` / `after_run_callback(*, invocation_context)`; `ToolContext.request_confirmation(*, hint, payload)`, `.tool_confirmation`, `.function_call_id`, `.invocation_id`, `.session.events`, `.actions.skip_summarization`; `FunctionTool._context_param_name`; `Event.author`, `.actions.requested_tool_confirmations`; `ToolConfirmation(hint, confirmed, payload)`; the `adk_request_confirmation` function call (`args.originalFunctionCall`, `args.toolConfirmation`); resume on a user `FunctionResponse(id, name="adk_request_confirmation", response={"confirmed", "payload"})`; `App(plugins=[…])` | UNVERIFIED (signature confirmed; pending maintainer `--live` + sign-off) | `integrations.adk.approval_plugin()` pauses a `@donkey.tool(approval="required")` call (a plain function, which ADK wraps in a `FunctionTool` whose `func` it is) from the plugin's `before_tool_callback` with ADK's own tool confirmation (BG §2.3). Read from the installed source of `google-adk==2.13.0` and confirmed at the `2.4.0` floor: plugin `before_tool_callback` runs before agent callbacks and before ADK's own confirmation gate (`flows/llm_flows/tools/_caller.py` at 2.13, `flows/llm_flows/functions.py` at 2.4); `request_confirmation` stores a `ToolConfirmation` under `actions.requested_tool_confirmations[function_call_id]` (`agents/context.py`), which the flow turns into a long-running `adk_request_confirmation` call, and `skip_summarization` ends the turn instead of calling the model again; on resume the request processor maps the confirmation back to the original call and runs it again with `tool_context.tool_confirmation` set. From 2.13 it accepts the confirmation only if the call is in session history with the same args and a confirmation was requested for it, and does not run a call already answered (`flows/llm_flows/tools/_confirmation.py`). At 2.4.0 it does neither: a user message can carry a forged `adk_request_confirmation` call and resume on it, and the same answer sent twice runs the call twice. So the plugin accepts a confirmation only when an agent-authored session event holds a DDK request for that function-call id and approval id, takes the trace fields from that stored request, and refuses a call that already has a non-placeholder response in the session. It binds the arguments as `FunctionTool.run_async` does: drops keys the function does not take and passes `_context_param_name` (`tool_context` by default) itself; a missing required argument is left to ADK, which answers with an error and does not run the tool. At 2.4.0 `skip_summarization` also puts the paused call's placeholder result on the pause's last event as text (documented in `hitl.mdx`); ADK keeps confirmation calls and responses out of the model's contents (`contents.py`), so the payload's approver never reaches the model; each function call runs on its own task (`_batch_executor.py:187` at 2.13, `functions.py:443` at 2.4) and a sync tool's thread-pool run copies that task's context, so the approval set in the callback reaches the gate and no other call. `TOOL_CONFIRMATION` is experimental but on by default at both versions. Offline: `tests/unit/test_adk_approvals.py` (reject, forged confirmation, replayed answer, a `ToolContext` parameter, no plugin) and the ADK leg of `tests/conformance/test_approval_round_trip.py` (approve, resumed by a fresh plugin and runner) pass at 2.4.0 and 2.13.0. Not run live. | — | offline: `tests/unit/test_adk_approvals.py`; `tests/conformance/test_approval_round_trip.py` |

#### OpenAI Agents approval identities (#1238)

This updates the tool-approval row above: `_GatedInvoker` uses
`RunContextWrapper.get_approval_status(tool_name, call_id, *, tool_namespace=...,
tool_lookup_key=..., current_invocation=...)`,
not the bare-name `is_tool_approved` query. Status remains **UNVERIFIED
(signature confirmed; pending maintainer `--live` + sign-off)**.

Installed-source and offline checks on 2026-10-26: `openai-agents==0.20.0`
with `openai==2.54.0`, Python 3.11.16, and `openai-agents==0.24.0` (latest)
with `openai==3.37.0`, Python 3.12.16. Both expose the public
`ToolContext.tool_namespace`, `.tool_call`, `.agent`, `FunctionTool.qualified_name`
and `.defer_loading`, and `ToolApprovalItem.qualified_name`, `.tool_namespace`
and `.tool_lookup_key`. The public `ToolApprovalItem(agent, raw_item,
tool_name=..., tool_namespace=..., tool_lookup_key=...)` carries the current
invocation to the approval query. At 0.24.0, `RunContextWrapper.get_approval_status`
requires the owning agent to resolve an agent-owned execution decision;
namespace alone is insufficient. A deferred top-level invocation uses
`tool_lookup_key=("deferred_top_level", name)` and the synthetic namespace
equal to its routed name; the runner may remove that namespace before invoking
the tool, so the invoker retains the bound native tool's public metadata.

`_gated_spec` compares public qualified names and distinguishes deferred
top-level tools from visible same-name tools using `defer_loading` and the
interruption's namespace. No private `_tool_namespace` or private SDK module
is read by the adapter. Evidence: the `test_responses_*` regressions in
`tests/unit/test_openai_agents_approvals.py` use a fake Responses `Model` through
the real `Runner`, covering approval, rejection, unapproved controls,
same-name namespace/deferred collisions with different specs, and serialized
`RunState` restoration. Chat Completions rejects these tool features before
execution. No live gateway check or verification-status promotion is implied.

#### Strands tool-refusal propagation (#1237)

Offline source check, 2026-10-26, with `strands-agents==1.57.1` (floor) and
`1.62.0`: `strands.hooks.AfterToolCallEvent.exception`
preserves the original tool exception. `DecoratedFunctionTool.stream` in
`strands/tools/decorator.py` attaches it to the error `ToolResultEvent`, and
`ToolExecutor._stream` in `strands/tools/executors/_executor.py` passes it to
the after-hook before checking `retry` or emitting the result. An exception
raised by an after-hook is caught once by the executor and passed to a second
after-hook invocation; raising again escapes the tool loop. The concurrent
executor forwards that exception and cancels its other tasks; the event loop
wraps it in `EventLoopException` with the original on `__cause__`.

`integrations.strands.ApprovalHook` clears the call's approval context, recovers
only governed errors through `core.refusals.translate`, and re-raises them at
this seam. The existing `run()` / `@governed` bridge unwraps the event-loop
exception. Ordinary tool errors and human rejections remain error tool results.
Evidence: `tests/unit/test_strands_approvals.py` (sync/async tools, both
executors, refusal identity, retry prevention, cleanup and ordinary failures),
`tests/unit/test_strands_approval_bookkeeping.py` (framework-free cleanup), and
the `strands.tool` surface in `tests/conformance/test_typed_refusal_bridge.py`
(PII refusal, model substitution and transport failure, both bridge scopes and
the bridge-disabled control). No live gateway check or verification-status
promotion is implied.

### 8.1 Known upstream incompatibilities (floors, not ceilings)

Per the floors-never-ceilings rule the extras declare **floors and no ceilings**, so a fresh resolve always
takes the newest release. That is deliberate: the nightly framework matrix exists
to find breakage early rather than to pin it away. Recorded here so a red matrix
run is diagnosable instead of surprising.

| Dependency | Breaks at | Symptom | Status | Date |
|---|---|---|---|---|
| `openai` | `>=3.0` | The SDK passes its shared `DonkeyAsyncClient` (an `httpx.AsyncClient` subclass) into `AsyncOpenAI(http_client=…)`. openai 3.x retyped that parameter to `httpx2.AsyncClient`, a distinct class from a separate distribution, so `mypy --strict` flagged `llm/client.py` and `integrations/openai_agents.py`. **Type-annotation-only** — when an `http_client` is injected, openai builds and sends every request *through that client*, so `httpx2` never touches our path. Since #728, where the SDK builds the `OpenAI`/`AsyncOpenAI` itself it no longer relies on openai accepting an `httpx` client: when `openai.DefaultAsyncHttpxClient` is built on `httpx2` (openai 3.x), `donkey.llm.client()` (async and sync) and the adapters that pre-build an `AsyncOpenAI` (`_proxy_openai_client`: OpenAI Agents SDK, MS Agent Framework, ADK `client`) pass the same core `httpx2` bridge the `anthropic` row below uses (`core/transport/httpx2.py`, chosen by `built_on_httpx2()`); openai `<3` keeps the non-owning view. The adapters whose framework builds the OpenAI client from an `http_client` kwarg (LangGraph `http_async_client`/`http_client`, LlamaIndex `async_http_client`/`http_client`, Strands `client_args["http_client"]`) hand it a *reusable* variant of the same bridge on openai 3.x (its close is a no-op, because Strands closes its client after every request), and the view on openai `<3`; one helper (`llm.client.openai_http_client` / `openai_sync_http_client`) makes the choice for all of them, pinned by `test_adapter_openai3_bridge.py` (#728). llama-index-llms-openai and `strands-agents[openai]` still pin `openai<3`, so today only LangGraph reaches the bridge in a natural resolve. **Mitigated** with a `cast(Any, …)` on the argument at the three version-conditional `http_client` sites (`llm/client.py` ×2 and `_proxy_openai_client` in `integrations/_base.py`) — `cast(Any, …)` erases the type on both majors, so `mypy --strict` passes under **both** openai `<3` and `>=3` (was a targeted `# type: ignore[arg-type]` / `cast("httpx.Response", …)` that passed only under openai `>=3` and became `unused-ignore` / `redundant-cast` under openai `<3`, #597; original guards #137). The `exc.response` side needs no guard: `classify()` and `DonkeyError.response` take the structural `ResponseLike` Protocol (`core/_response.py`), which `httpx.Response` and `httpx2.Response` both satisfy, so the documented `classify(exc.response)` type-checks on both majors; pinned by `tests/typecheck/classify_response_contract.py` (#933). **Runtime re-verified and test-pinned** against openai 3.x (async + sync): `test_llm_client_openai3_injection.py` drives `donkey.llm.client()` through a mock transport and asserts the `client_id`/`client_secret` pair is injected and the base URL carries no `/v1` (#18). | MITIGATED | 2026-10-07 |
| The framework extras together (`adk`, `agent_framework`, `anthropic`, `crewai`, …) | `anthropic>=1.0`, `google-adk>=2.10`, `agent-framework>=1.19` | On 2026-09-30 the framework extras were not co-installable at their releases of the day. `agent-framework-anthropic` (pulled in by every `agent-framework`) requires `anthropic<1`, while the `anthropic` extra resolves to 1.x; `google-adk>=2.10` requires `opentelemetry-api`/`-sdk<=1.42.1`, while `agent-framework>=1.19` needs OpenTelemetry `>=1.43`. With all of them in one resolve, pip backtracks for minutes and exits `resolution-too-deep`; raising the floors to the §8-verified versions makes the set unsatisfiable, so no floor fixes it. **Mitigated** by scoping `all` to the extras that install together (`llm`, `langgraph`, `otel`, `cli`, `local`); each framework extra resolves on its own and alongside `all`, at its newest release. CI dry-runs `.[all]` on every PR and nightly so a new upstream conflict fails there first (#697). Since #769 the nightly `co-installability` job regenerates the pairwise table in [`co-installability.md`](co-installability.md) with uv and fails when it changes. On 2026-10-10 every framework extra resolved on its own, in every pair, and all eight together with `[all]` (uv, and `pip install --dry-run` on Python 3.12). `all` stays scoped: no floor can keep a co-install true. Re-read 2026-10-14 (#1126): `agent-framework-anthropic` 1.0.0b261008 (with `agent-framework` 1.21.0) now requires `anthropic>=1.11.0,<2` (b261002 required `>=0.80.0,<0.117.0`), so the `anthropic<1` half of this conflict no longer applies to `agent_framework`; the `google-adk` / OpenTelemetry half is unchanged and is not re-checked here, and `scripts/coinstall_matrix.py` was not re-run. | MITIGATED | 2026-10-10 |
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
| LangGraph `interrupt()` | `<3.11` | Under `graph.ainvoke()`, `interrupt()` raises `RuntimeError: Called get_config outside of a runnable context`, from an async node and from a sync node alike. `langgraph.config.get_config()` reads the run's config from langchain-core's `var_child_runnable_config` context variable, and asyncio tasks only accept an explicit context from 3.11, so the variable is unset inside the node. `interrupt()` takes no config argument, so there is nothing to pass through explicitly. `graph.invoke()` works on 3.10. Reproduced on Python 3.10 with `langgraph==0.3.0`, `0.4.0` and `1.2.12`. **Documented, 3.11+ required for `interrupt()` under `ainvoke()`**: `test_interrupt_and_typed_refusal_compose` is a strict `xfail` on 3.10 (an asserted exemption, not a skip), and `test_interrupt_and_typed_refusal_compose_sync` proves the composition on every supported Python. The nightly `lowest-direct` langgraph leg runs on Python 3.10, so the strict `xfail` is held there on every run (#769, #865). | DOCUMENTED | 2026-10-01 |
| CrewAI native OpenAI provider (`OpenAICompletion`) | all | The provider cannot be routed through the SDK's shared transport, so its calls carry no per-run correlation id, `donkey.last_call` reads UNAVAILABLE, jwt/bearer auth is refused, and CrewAI's own rate-limit retry stays on. Read from the installed source of `crewai==1.15.23` and `crewai==1.15.3` (the extra's floor), with `openai==2.54.0` (#958), and re-read at `crewai==1.15.27` (#1116), where every claim below still holds. **No supported field takes a client:** `_build_sync_client` and `_build_async_client` both start from the one dict `_get_client_params()` returns, which merges the public `client_params` field in, and pass it to `OpenAI(...)` and `AsyncOpenAI(...)`. The openai SDK type-checks `http_client` in each constructor (`httpx.Client` for the sync one, `httpx.AsyncClient` for the async one), so any single value fails one of them. Passing `client_params={"http_client": ...}` with either client type makes `crewai.LLM(...)` raise `ImportError: Error importing native provider: Invalid http_client argument` (reproduced). **The interceptor cannot reroute the send:** with `interceptor` set, both builders replace `http_client` with a fresh `httpx.Client` / `httpx.AsyncClient` around CrewAI's `crewai.llms.hooks.transport.HTTPTransport` / `AsyncHTTPTransport`. Their `handle_request` / `handle_async_request` call `on_outbound` (or `aon_outbound`), then always send through `httpx.HTTPTransport`'s own `handle_request`, then call `on_inbound`. The `BaseInterceptor` hooks can only edit the request and response objects, so the shared client's retry, `_on_request` and `_on_response` never run. The `crewai.hooks` LLM-call hooks (`LLMCallHookContext`) work on messages and response strings, not HTTP. The remaining routes all need private API: the `_client` / `_async_client` private attributes, overriding the `_build_*_client` methods in a subclass, or a client that is both an `httpx.Client` and an `httpx.AsyncClient`. **The rate-limit retry has no off switch:** `BaseLLM.__init_subclass__` wraps every subclass's `call` / `acall` in `run_with_rate_limit_retry` / `arun_with_rate_limit_retry` (`crewai.llms.retry`, new in 1.15.23, absent at 1.15.3), which makes up to `_LLM_RATE_LIMIT_MAX_ATTEMPTS` (3) attempts on any 429. The only bypass is the private `_active_llm_rate_limit_retry` context variable. **Documented, exemption kept:** `KNOWN_LIMITATIONS["crewai"]` in `tests/conformance/suite.py` (three scenarios), `_CREWAI_RATE_LIMIT_EXEMPTION` in `tests/unit/test_framework_retries.py`, and `CrewAIAdapter.observes_last_call = False`. | DOCUMENTED | 2026-10-12 |

### 8.3 Extras floors (lowest verified versions)

Floors, never ceilings, still means **every allowed version works**. Each floor
in `python/pyproject.toml` is the lowest release where the adapter's kwargs
behave as its §8 row records (#743). Every row below was installed with
`uv pip install --resolution lowest-direct -e ".[dev,<extra>]"` on Python 3.10
(and 3.11 for `langgraph`). That puts httpx 0.27.0, pydantic 2.6.0, pytest
8.0.0, pytest-asyncio 0.23.1 and typer 0.26.0 at their floors where the extra
allows it. Each row then passed `pytest tests/unit` and
`verify_frameworks.py --only <fw>` (offline signature check). Like §8, this is
offline evidence, not a live round-trip.

| Extra | Floor | Why this floor | §8 row |
|---|---|---|---|
| `llm` | `openai>=1.66` | First openai with the Responses API (`client.responses`), the data plane `donkey.llm.client()` speaks (§2). | — (§2) |
| `langgraph` | `langgraph>=0.4`, `langchain-openai>=0.3.9`, `langchain-core>=0.3.45` | langchain-openai 0.3.9 added `use_responses_api`, which the adapter always passes; on 0.3.8 and earlier it lands in the request body instead of switching the route. langgraph 0.3.x does not return `__interrupt__` from `ainvoke` on a pause, so the `interrupt()` composition test fails. langchain-core 0.3.45 is what langchain-openai 0.3.9 requires. The conformance plugin passes 4/4 at these floors. | LangGraph |
| `adk` | `google-adk>=2.4`, `litellm[mcp]>=1.84` | 2.4.0 is the first whose `Gemini` has `client_kwargs`. Below it, ADK's pydantic config drops the kwarg silently and `gemini()` bypasses the gateway (#735). `LiteLlm` and `Gemini` both pass the signature check at 2.4.0. litellm's own `mcp` extra, not a floor: from 1.106.0 a request with `tools` imports `litellm.proxy._experimental.mcp_server.catalog`, which does `from mcp.types import CacheableResult` at module level, and plain `litellm` does not install `mcp`, so a tool-bearing `model()` run fails with "No module named 'mcp'" (#1267, BerriAI/litellm#46045; still present in 1.106.4 and 1.107.0rc2). The extra brings `mcp>=2.2,<3` (litellm's range, not ours); `mcp` 1.x has no `CacheableResult`. Reproduced offline on `adapter-contract-adk-py3.12.txt` before the fix and passing after it (`test_adk_model_runner_with_a_tool_reaches_the_answer`). | Google ADK; Google ADK (native Gemini) |
| `agent_framework` | `agent-framework>=1.13` | Bisected from the verified 1.19.0. In 1.0.0–1.11.0 `agent_framework.openai.OpenAIChatClient` does not exist, so the adapter raises its `blocked on verification` import error. 1.12.0 builds it and passes the signature check, but `test_framework_redirects.py::test_agent_framework` fails: the call never reaches the proxy. 1.13.0 is the first that passes both. | MS Agent Framework |
| `openai-agents` | `openai-agents>=0.20`, `openai>=2.45` | The verified release. openai-agents 0.20.0 itself requires `openai>=2.45`, so the extra's old `openai>=1.66` (copied from `[llm]`) was a floor no install could reach; the lowest-direct leg found it (#769). | OpenAI Agents SDK |
| `anthropic` | `anthropic>=0.116` | The lowest verified release of the `AsyncAnthropic` row (also run on 0.125.0 and 1.9.0, §8.1). | Anthropic SDK |
| `crewai` | `crewai>=1.15.3` | 1.15.3 added the `openai/` + explicit `base_url` route to the native `OpenAICompletion`. Earlier releases fall back to LiteLLM for any id that does not look like an OpenAI model, returning `crewai.LLM` and LiteLLM exceptions. Signature check against `OpenAICompletion` passes at 1.15.3. | CrewAI |
| `llamaindex` | `llama-index-llms-openai-like>=0.8` | The verified release. | LlamaIndex |
| `strands` | `strands-agents[openai]>=1.57.1` | The verified release, with Strands' own `openai` extra: the bare distribution does not install `openai`, so `model()` raised `ModuleNotFoundError`. `test_strands_real_model_is_built_and_called_through_our_transport` builds and calls the real model with only `[strands]` installed. | Strands |
| `a2a` | `a2a-sdk[http-server]>=1.2.0`, `uvicorn>=0.30` | 1.2.0 is the first a2a-sdk with `DefaultRequestHandler.aclose()`, which `build_app()`'s lifespan calls so a shutdown drains the SDK's per-task asyncio tasks (1.0.3 and 1.1.0 serve tasks but have no `aclose()`; without it, the tasks are still pending when the loop closes). Below 1.0.3, 1.0.1–1.0.2 lack `a2a.helpers.new_text_part` and 1.0.0 fails to import without `grpc`. Lowest-direct on Python 3.10.21 installs a2a-sdk 1.2.0 and uvicorn 0.30.0 and passes `pytest tests/unit`, including `test_a2a_serve.py` (#224). | §8.5 |
| `cli` / dev group | `typer>=0.26.0`, `rich>=13.8.0` | Every typer up to 0.25.1 drops a `typer.Argument(help=...)` against click 8.5+, so `donkey scan --help` and the CLI reference lose the `PATH` description (#1196, bisected with `click==8.5.0`: 0.15.4–0.25.1 drop it, 0.26.0–0.27.3 keep it). 0.26.0 vendors click (fastapi/typer#1774). Older floors: below 0.15.4 typer breaks against a fresh click 8.2+ (`make_metavar() missing … 'ctx'`), and below 0.13 it rejects `Path \| None` options. rich 13.8.0 is typer 0.26.0's own floor, so `[cli]`'s old `rich>=13.0` could never be installed. | — |
| `test` / dev group | `pytest>=8.0`, `pytest-asyncio>=0.23.1` | pytest-asyncio 0.23.0 crashes collection (`INTERNALERROR`) on a module-level `importorskip`; 0.23.1 runs the suite. pytest 8.0.0 is fine. | — |

On Python 3.10 the extras pass the full unit suite at their floors. The one
3.10 gap, `interrupt()` under `ainvoke()`, holds at every langgraph version and
is not a floor issue; it is recorded in §8.2.

The nightly `lowest-direct` job keeps these floors honest (#769). It installs
each extra with `uv pip install --resolution lowest-direct` on Python 3.10, runs
the unit and adapter-contract suites, and then `scripts/check_floors.py` fails
the leg if any declared floor is not the installed version. The framework legs
leave `[llm]` out, because a framework that needs a newer `openai` than
`openai>=1.66` would otherwise push its own resolve above its floor. The
`openai-1x` PR job holds `[llm]` to the 1.x line (`openai<2`).

### 8.4 Docs status roster

Which §8 row rates each SDK factory. The docs never state a framework's status
by hand: `python/scripts/generate_docs.py` reads this table, the **Status**
column of the §8 table above, and `conformance_tested` on the `ADAPTERS` roster
(`src/donkey_kit/integrations/__init__.py`), and writes every status the docs
show from them: the root `README.md` framework table and summary, the
`ARCHITECTURE.md` summary, the `website/content/frameworks/*.mdx` frontmatter,
status table and cards, and the Quickstart and Feature overview rows. A unit
test (`tests/unit/test_generated_docs.py`) fails on drift (#1081).

To change a framework's status, change its §8 row. The script maps the Status
column as follows and refuses any other value:

| §8 Status | Docs label | Page `status:` |
|---|---|---|
| `VERIFIED (LIVE)`, `VERIFIED (CLI)` | Live-verified | `live` |
| `UNVERIFIED (signature confirmed; …)` | Signature-confirmed | `offline-verified` |

Every factory in `ADAPTERS` needs a row here, and every row needs its §8 row
and its docs page. A page is rated by its adapter's default factory (the first
in its `factories`), and a conformance-tested adapter's card reads
Conformance-tested.

<!-- BEGIN ROSTER: framework-status (parsed by python/scripts/generate_docs.py) -->
| Framework | SDK factory | §8 row | Docs page |
|---|---|---|---|
| LangGraph | `donkey.langgraph.chat_model()` | LangGraph | `/frameworks/langgraph` |
| Google ADK | `donkey.adk.model()` | Google ADK | `/frameworks/adk` |
| Google ADK | `donkey.adk.gemini()` | Google ADK (native Gemini) | `/frameworks/adk` |
| Strands | `donkey.strands.model()` | Strands | `/frameworks/strands` |
| MS Agent Framework | `donkey.agent_framework.chat_client()` | MS Agent Framework | `/frameworks/agent-framework` |
| OpenAI Agents SDK | `donkey.openai_agents.model()` | OpenAI Agents SDK | `/frameworks/openai` |
| Anthropic SDK | `donkey.anthropic.client()` | Anthropic SDK | `/frameworks/anthropic` |
| CrewAI | `donkey.crewai.llm()` | CrewAI | `/frameworks/crewai` |
| LlamaIndex | `donkey.llamaindex.llm()` | LlamaIndex | `/frameworks/llamaindex` |
<!-- END ROSTER: framework-status -->

### 8.5 A2A server classes (BG §2.9.1)

`donkey.serve()` wraps these `a2a-sdk` names and reimplements none of them
(#224). Each name below was read from the installed `a2a-sdk` 1.2.3, and the
whole path was run twice. First, in process over `httpx.ASGITransport`
(`tests/unit/test_a2a_serve.py`, also at the 1.2.0 floor). Second, on a real
port: `donkey.serve()` under uvicorn 0.54.0 on `127.0.0.1:8765`, called with
plain `curl` and no SDK on the caller side, which got back the generated card
and a `TASK_STATE_COMPLETED` task with its text artifact. That round trip is
local; no gateway fronted the agent.

| Symbol | Used for | Status | Source |
|---|---|---|---|
| `a2a.server.agent_execution.AgentExecutor` (`execute`, `cancel`) | The base class of the executor that runs the handler | VERIFIED (local round-trip) | a2a-sdk 1.2.3, 2026-10-24, #224 |
| `a2a.server.agent_execution.RequestContext` (`.message`, `.current_task`, `.task_id`, `.context_id`) | Reading the caller's message and IDs | VERIFIED (local round-trip) | same |
| `a2a.server.events.EventQueue` | Passed to `TaskUpdater`; the executor enqueues a `Task` before any status update, or the SDK rejects the event | VERIFIED (local round-trip) | same |
| `a2a.server.tasks.TaskUpdater` (`start_work`, `add_artifact`, `complete`, `failed`, `cancel`) | The task lifecycle | VERIFIED (local round-trip) | same |
| `a2a.server.tasks.InMemoryTaskStore` | The task store | VERIFIED (local round-trip) | same |
| `a2a.server.request_handlers.DefaultRequestHandler(agent_executor=, task_store=, agent_card=)`, `.aclose()` | The JSON-RPC request handler; `aclose()` (1.2.0+) runs on lifespan shutdown | VERIFIED (local round-trip) | same |
| `a2a.server.routes.create_agent_card_routes(card)`, `create_jsonrpc_routes(handler, "/")` | The card at `/.well-known/agent-card.json` and JSON-RPC at `/` (method `SendMessage`, header `A2A-Version: 1.0`) | VERIFIED (local round-trip) | same |
| `a2a.types.AgentCard` | The card, built from the `@donkey.agent` record | VERIFIED (local round-trip) | same |
| `a2a.helpers` (`new_task_from_user_message`, `new_text_part`, `new_data_part`, `new_text_message`, `get_text_parts`, `get_data_parts`) | Building and reading message parts | VERIFIED (local round-trip) | same |
| JSON-RPC methods `SendMessage` and `SendStreamingMessage` (`JsonRpcDispatcher.METHOD_TO_MODEL`) | The calls `donkey dev`'s simulated ingress runs its `--scenario` rules on; the other methods pass through | VERIFIED (local round-trip) | a2a-sdk 1.3.0, 2026-10-27, #1284: `donkey dev` on `127.0.0.1:8931`, called with plain `curl`, returned the card and a completed `SendMessage` task with `x-donkey-simulator: true`, and a scenario-matched `SendMessage` got the 79-byte injection-protection fixture |

Fronting an agent with an Omni Gateway A2A instance is `donkey expose` (#226);
the control-plane calls it needs are recorded in §12.5 (#254, and #1278 for an
MCP instance). `donkey dev`'s
simulated ingress (#1284) answers inbound rejections with the LLM-proxy fixtures
of §4; how the gateway answers a policy rejection on an A2A agent instance is
not captured (#226/#1278).

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

Which MCP transports Omni Gateway can front (streamable HTTP, SSE, stdio) is
UNVERIFIED (#1150). §5 verifies only the shape of a gateway MCP endpoint. The
binding guard (ADR 0013) does not depend on the answer: it binds a server only
when its `registry.explain()` verdict is governed, whatever its transport.

## 10. Descriptor-derivation attributes (BG §2.5) — semi-public, put in nightly matrix

Read off one real bound tool per framework, built from the same reference
function by `python/scripts/capture_descriptor_fixtures.py` at the version the
adapter-contract lock pins. Each read is committed under
`python/tests/fixtures/descriptor/`, and `tests/unit/test_descriptor_derivation.py`
holds `registry/introspect.py`'s `derive_descriptor()` to it in every
adapter-contract leg (pinned) and every nightly-matrix leg (latest). 2026-10-26, #1024.

| Framework | Attributes read | Status | Source |
|---|---|---|---|
| MCP Python SDK 1.x (`mcp.server.fastmcp.FastMCP`) | `(await server.list_tools())[i]` → wire `Tool` `.name` `.description` `.inputSchema` | VERIFIED (local capture) | `mcp==1.30.0`, `descriptor/mcp-1.json` |
| MCP Python SDK 2.x (`mcp.server.mcpserver.MCPServer`, FastMCP's 2.x name) | `(await server.list_tools())[i]` → wire `Tool` `.name` `.description` `.input_schema` (alias `inputSchema`) | VERIFIED (local capture) | `mcp==2.3.0`, `descriptor/mcp-2.json` |
| MCP, `auto:live` | initialize + `tools/list` over the SDK's in-memory transport: `mcp.Client(server)` (2.x), `mcp.shared.memory.create_connected_server_and_client_session(server)` (1.x) | VERIFIED (local round-trip) | Equal to the object read above on both majors (`test_live_mcp_tools_list_equals_object_introspection`) |
| Standalone `fastmcp` package | — | UNSUPPORTED | `fastmcp==4.1.4` has a different object shape and no adapter-contract leg installs it, so it raises `_verify.blocked(...)`. Added on demand (#1248) |
| LangChain (`langchain_core.tools.StructuredTool`) | `.name` `.description` `.tool_call_schema` (a pydantic class or a dict) | VERIFIED (local capture) | `langchain-core==1.7.0`, `descriptor/langchain.json`. Not `.args_schema`: `tool_call_schema` drops `InjectedToolArg` parameters, which the model never sees |
| Strands (`strands.tools.decorator.DecoratedFunctionTool`) | `.tool_spec['name']` `['description']` `['inputSchema']['json']` | VERIFIED (local capture) | `strands-agents==1.62.0`, `descriptor/strands.json` |
| ADK (`google.adk.tools.FunctionTool`) | `._get_declaration()` → `FunctionDeclaration` `.name` `.description` `.parameters_json_schema` | VERIFIED (local capture) — **private method** | `google-adk==2.13.0`, `descriptor/adk.json`. The only place ADK exposes the schema it sends; a declaration without `parameters_json_schema` stays blocked |
| LlamaIndex (`llama_index.core.tools.FunctionTool`) | `.metadata.name` `.metadata.description` `.metadata.get_parameters_dict()` | VERIFIED (local capture) | `llama-index-core==0.14.25`, `descriptor/llamaindex.json`. The description is prefixed with the function signature, as the model sees it |
| OpenAI Agents SDK (`agents.FunctionTool`) | `.name` `.description` `.params_json_schema` | VERIFIED (local capture) | `openai-agents==0.24.0`, `descriptor/openai_agents.json` (the strict schema) |
| Anthropic SDK (`beta_tool` → `BetaFunctionTool`, or a `ToolParam` dict) | `.to_dict()` (or the dict) `name` / `description` / `input_schema` | VERIFIED (local capture) | `anthropic==2.3.0`, `descriptor/anthropic.json` |
| CrewAI (`crewai.tools.base_tool.Tool`) | `.name` `.description` `.args_schema.model_json_schema()` | VERIFIED (local capture) | `crewai==1.15.30`, `descriptor/crewai.json`. CrewAI post-processes this for the model (`convert_tools_to_openai_schema`: title stripped, strict); the descriptor records the tool's own schema |
| MS Agent Framework (`agent_framework.tool` → `FunctionTool`, not `AIFunction`) | `.name` `.description` `.parameters()` | VERIFIED (local capture) | `agent-framework-core==1.23.0`, `descriptor/agent_framework.json` |
| AST-only derivation (`auto:static`) | — | UNSUPPORTED | Dropped (#1248): an AST pass can name a tool but cannot produce its schema without the type-hint→JSON-Schema converter BG §2.5 forbids. `donkey scan` is the no-execution view of the code; `mode="auto:static"` is rejected as an unknown mode |

The Exchange descriptor read behind `Publication.verify()` and
`donkey verify` stays blocked on #218.

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
| PUT | `/api-project/api/v1/registry/{urn}` | **the plugin's publish**: the built project as a zip in multipart field `file`, header `x-sync-publication: true`; `{urn}` is the URL-encoded `urn:ms:{groupId}:api-project::{assetId}/{version}` (`asset-facet.js`) |
| POST | `/exchange/api/v2/organizations/{orgId}/assets/{groupId}/{assetId}/{version}` | Exchange's own multipart publish, **the one `donkey publish` calls** (#218). **Not called by the plugin**: its URI helper (`getPublishAssetUri`) is used only by the plugin's tests. VERIFIED (LIVE) 2026-10-26: fields `name`, `description`, `type`, `properties.{key}` and a `files.{classifier}.{packaging}` file field (`files.mcp-metadata.json`, stored as sent); with `x-sync-publication: true` it is `201` with `{organizationId, groupId, assetId, version, name, type}`; `name` and `description` are taken from an asset's first version only, and a later version shows the asset's existing ones whatever it sends (VERIFIED (LIVE) 2026-10-27, #1281); a version that exists is `409` `ASSET_PRE_CONDITIONS_FAILED`, the version unchanged. Without a file Exchange generates an `mcp-metadata` itself (streamable HTTP at `/mcp`, `platform=other`). The file's `protocolVersion` (enum `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`), `provider` `{organization, url}` and boolean `capabilities` `{tools, resources, prompts, logging}` follow agent-fabric-specification's `mcp_metadata.json` and a published Agent Fabric example's file; Agent Fabric showing them is UNVERIFIED until an SDK publish sets them (#1269). For `type=agent` (VERIFIED (LIVE) 2026-10-27, #1260) the same POST takes `properties.{ddkDigest,protocol,platform}` and the `files.agent-metadata.json` and `files.a2a-v1-card.json` file fields; `201`, and the generated POM keeps `<type>`, `<ddkDigest>`, `<protocol>` and `<platform>`; a repeated POST is `409`, and the external instance and page calls behave as for `mcp`. Fixtures: `tests/fixtures/anypoint/exchange_publish/publish.{201,409}.json`, `agent.{publish.201.json,graphql.latest.json,pom.xml}` |
| POST | `/exchange/api/v2/assets/{groupId}/{assetId}/versionGroups/{versionGroup}/instances/external` | create external instance `{name, endpointUri}`. VERIFIED (LIVE) 2026-10-26: `201`; the instance belongs to the version group (`v1.0`), so later patch versions list it too (`instance.201.json`) |
| GET | `/exchange/api/v2/assets/{groupId}/{assetId}/{version}/portal/draft` | open the version's portal draft; the first of the calls `anypoint-cli-v4 exchange asset page upload` makes (`anypoint-cli-exchange-plugin` 1.0.12, `assetPageUpdate`), and the ones `donkey publish` makes for a new version's Home page (#1269). VERIFIED (LIVE) 2026-10-27 with a connected app holding Exchange roles: `200` with the draft (`portal-draft.200.json`) |
| POST | `/exchange/api/v2/assets/{groupId}/{assetId}/{version}/portal/draft/pages` | create a draft page, body `{"pagePath": "home"}`. VERIFIED (LIVE) 2026-10-27: `201` `{commitId, path, name}`; `409` `Page home already exists` when it does (`page.{201,409}.json`) |
| PUT | `/exchange/api/v2/assets/{groupId}/{assetId}/{version}/portal/draft/pages/{pagePath}` | the page's Markdown, `Content-Type: text/markdown`. VERIFIED (LIVE) 2026-10-27: `204`. Exchange renders headings one level down and inline code in a table cell with its backticks; the Home page is Agent Fabric's Documentation section, above Details |
| PATCH | `/exchange/api/v2/assets/{groupId}/{assetId}` | **no version**: the Exchange UI's asset edit, JSON `{name?, description?}`; what `donkey publish` calls when a new version declares a name or description that differs from the asset's (#1281). VERIFIED (LIVE) 2026-10-27 with a connected app holding Exchange roles: `204`, no body; either field alone or both; it changes every version, and later versions take the new values (`graphql.renamed.json`). The versioned `PATCH /exchange/api/v1/organizations/{orgId}/assets/{groupId}/{assetId}/{version}/metadata` that `anypoint-cli-v4 exchange asset modify --name` sends is also `204` but left the name unchanged on later versions |
| PATCH | `/exchange/api/v1/assets/{groupId}/{assetId}/{version}` | **v1**, no body: publishes the portal draft. VERIFIED (LIVE) 2026-10-27: `204`; `GET …/portal/pages/home` then returns the page rendered as HTML |
| POST | `/graph/api/v2/graphql` | asset metadata GraphQL. VERIFIED (LIVE) 2026-10-26: `asset(groupId, assetId)` without a version is the latest, with `version`, `versionGroup`, `files { classifier packaging externalLink }`, `instances { endpointUri }`, `otherVersions { version }`, and (2026-10-27, #1281) the asset's `name` and `description`; an asset the org does not have is a `200` with `data: null` and an error whose `extensions.status` is `404`. `customFields` is a union and needs inline fragments (`graphql.{latest,version,absent,renamed}.json`) |
| DELETE | `/exchange/api/v2/assets/{groupId}/{assetId}/{version}` | header `x-delete-type: hard-delete`. **Not called by the SDK**: used to remove the throwaway fixture assets. VERIFIED (LIVE) 2026-10-27 (#1260): `204`; the GraphQL `asset` query is then a `404` |
| GET | `/exchange/api/v2/assets/{groupId}/{policyId}/minorVersions/{minorVersion}` | policy metadata (`dist/helpers/policy-helper.js`) |

The plugin's asset builders assemble per-asset fields: `type`
(`agent|mcp|llm|app|policy|agent-network`), `name`, `description`,
`dependencies` (`g:a:v,…`), `tags` (csv), `files.{classifier}` (octet-stream
blobs), `properties.{key}` and `properties.source` (`urn:gav:<g>:<a>:<v>`). Its
publish path sends them inside the project zip, not as the Exchange multipart
POST above. The Exchange Experience API spec lists the same multipart fields
for that POST (plus `keywords`, `fields.*`, `categories.*`, `contactName`,
`contactEmail`), and its type enum includes `mcp` and `agent`. Corrected in
#218: an earlier revision of this section said the plugin publishes through the
POST. The SDK publishes through the POST (`donkey_kit.registry.exchange_publish`);
the PUT is not exercised. The 2026-10-26 captures authenticated with a user
session bearer: the same calls with a connected app's `client_credentials` token
need the app to hold the Exchange Contributor permission, which is not yet
confirmed live.

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

**Live: the A2A instance write path (#254).** On 2026-10-27 one A2A instance
was created, given an outbound policy, deployed and deleted in the sandbox
(DDK/Sandbox, managed shared-space Flex target, listener port 8081), for a
throwaway `agent` asset that was hard-deleted afterwards, first with a
username/password session, then again end to end with a connected app's
`client_credentials` token. The scopes that second run had are in §1; the
minimal set is not isolated (#23).
Captures, scrubbed, are in `python/tests/fixtures/anypoint/a2a_instance/`.

| Call | Status | Observed |
|---|---|---|
| `POST /apimanager/api/v1/…/apis` with `endpoint.type: "a2a"` | VERIFIED (LIVE) | `201`, the instance with an `id` and no deployment. Body as above, without `endpointUri`, `description` or `metadata`: `spec`, `endpoint {type, uri, proxyUri: "http://0.0.0.0:8081/<label>", isCloudHub: null, deploymentType: "HY"}`, `instanceLabel`, `technology: "flexGateway"`. The response adds `metadata.assetType: "agent"` from the asset. |
| `POST /apimanager/api/v1/…/apis/{apiId}/policies` with an outbound-only policy (`credential-injection-api-key` 1.0.1) | VERIFIED (LIVE), refused | `400` `PolicyValidationError`, "This policy can not be applied as inbound". |
| `GET /apimanager/api/v1/…/apis/{apiId}/upstreams` | VERIFIED (LIVE) | `200`, `{total, upstreams: [{id, uri, …}]}`: one upstream, made from `endpoint.uri`. |
| `POST /apimanager/xapi/v1/…/apis/{apiId}/policies/outbound-policies` | VERIFIED (LIVE) | `201`, a list with one policy per upstream id sent. Body `{configurationData, groupId, assetId, assetVersion, upstreamIds, apiVersionId}`, as `anypoint-cli-agent-fabric-plugin` 1.0.4 sends it. `GET` on the path is `405`. The policy then shows in `GET …/policies` with `type: "system"` and its secret field left out. |
| `POST /proxies/xapi/v1/…/apis/{apiId}/deployments` | VERIFIED (LIVE) | `201`, `{id, expectedStatus: "deployed", gatewayVersion, …}`. Body `{type: "HY", gatewayVersion, targetId, targetName, environmentId, environmentName}`; the `gatewayVersion` sent (`1.0.0`) is replaced by the target's. |
| `GET /proxies/xapi/v1/…/apis/{apiId}/deployments/{id}/status` | VERIFIED (LIVE) | `200`, `applying`/`unregistered`, then `applied`/`active` within about 10 s. |
| `DELETE /apimanager/api/v1/…/apis/{apiId}` | VERIFIED (LIVE) | `204`; the instance then drops out of the listing. |

Not verified by this run: inbound policies on an A2A instance, and traffic
through the deployed instance (its upstream was not reachable). The `mcp`
instance is below (#1278).

**Live: the MCP instance write path (#1278).** On 2026-10-27 one MCP instance
was created, given two inbound policies, deployed and deleted in the same
sandbox and target, for a throwaway `mcp` asset published with the #218 path
and hard-deleted afterwards, with a username/password session only. Captures,
scrubbed, are in `python/tests/fixtures/anypoint/mcp_instance/`.

| Call | Status | Observed |
|---|---|---|
| `POST /apimanager/api/v1/…/apis` with `endpoint.type: "mcp"` | VERIFIED (LIVE) | `201`, the instance with an `id` and no deployment. The A2A body with `type: "mcp"`. Unlike A2A, the response's `metadata` is `null`. |
| `GET /apimanager/api/v1/…/apis?limit=500`, rows by `instanceLabel` | VERIFIED (LIVE) | Right after the create, `assets[].apis[]` holds exactly one row with the new label: a list read finds an existing instance before a create. |
| The same `POST …/apis` again (same asset version, same `instanceLabel`) | VERIFIED (LIVE), refused | `409` `ConflictError`, "This API (instance label for that product version in that environment) already exists in your organization."; no second instance. |
| `POST /apimanager/api/v1/…/apis/{apiId}/policies`, inbound `mcp-support` 1.1.1 and `client-id-enforcement` 1.3.3 | VERIFIED (LIVE) | `201` each, body `{groupId, assetId, assetVersion, configurationData, pointcutData: null}`. `GET …/policies` lists both with `type: "system"` and `order` 1, 2 in posting order. No `xapi` call is needed: an MCP instance's policies are inbound. |
| `POST /proxies/xapi/v1/…/apis/{apiId}/deployments`, then `…/status` | VERIFIED (LIVE) | `201`, then `applying`/`unregistered` → `applied`/`active` in about 40 s. Same body and response as A2A. |
| `DELETE /apimanager/api/v1/…/apis/{apiId}` | VERIFIED (LIVE) | `204`; the label then has no row in the listing. |

Not verified by this run: traffic through the deployed instance (its upstream
was not reachable, and no call went through the gateway), how the gateway
answers an MCP call a policy refuses (#303), and the run with a connected
app's token, so the scopes in §1 still come from the A2A run (#23).

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

## 13. On-behalf-of token exchange (BG §2.4)

`donkey.as_user(id_token=...)` (#215) trades a user's OIDC `id_token` for a
gateway-scoped access token with an RFC 8693 token-exchange grant and stamps it
on every data-plane request in the block. Nothing here has been observed
against a live gateway; confirming both rows is #255. Until then each is an
`Unverified` placeholder in `core/_verify.py`, warns once when read, and is
overridable in config.

| Item | Where used | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| Token-exchange endpoint path | `core/identity.py`, `core/_verify.py` `TOKEN_EXCHANGE_PATH`; override `token_exchange_path` / `DONKEY_TOKEN_EXCHANGE_PATH` | UNVERIFIED | — (placeholder: the verified OAuth2 token path, `POST /accounts/api/v2/oauth2/token` on the control-plane host, §1; form body `grant_type=urn:ietf:params:oauth:grant-type:token-exchange`, `subject_token`, `subject_token_type=urn:ietf:params:oauth:token-type:id_token`, plus the connected-app `client_id` / `client_secret`; response `access_token`, `expires_in` per RFC 8693 §2.2) | — | — |
| User-token request header | `core/transport/headers.py` `_apply_user_token`, `core/_verify.py` `USER_TOKEN_HEADER`; override `user_token_header` / `DONKEY_USER_TOKEN_HEADER` | UNVERIFIED | — (placeholder: `Authorization: Bearer <token>`, the header the Omni Gateway [OAuth 2.0 OBO Credential Injection policy](https://docs.mulesoft.com/gateway/latest/policies-outbound-oauth-obo) reads the incoming bearer from; a custom header carries the raw token) | — | — |
| Exchange parameters beyond the subject token (`audience`, `resource`, `scope`, `requested_token_type`) | — | UNVERIFIED | — (none sent) | — | — |
