# Verified APIs — output of the verification discipline (M0)

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
> `tests/fixtures/anypoint/llm_proxy/`). Streaming + `/models` on the proxy
> (§2) and the token-rate-limit (`429`, empty body, header-only budget) + PII
> (`403`, `type:pii_detected`) rejection contracts (§4) are now also
> `VERIFIED (LIVE)` — both policies were applied to `openai-sdk` for capture and
> removed. Still `UNVERIFIED`: prompt-injection / regex-prompt-guard /
> content-safety rejection bodies (§4, typed from the policy pages, pending live
> capture #253) and the **framework constructor/binding names** (§§8–10). §11
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
> This is the deliverable of Milestone M0. For any row still `UNVERIFIED`, the
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

## 1. Anypoint control plane

CLI-verified rows were confirmed against the real sandbox org
`82a0453b-22e6-430d-bbf4-35b989d043dc` (user `admin-af`) on 2026-08-28 using
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
| Region host variants (US/EU/CA/JP Hyperforce) | `core/config.py` | UNVERIFIED | US = `anypoint.mulesoft.com` confirmed; others pending | — | — |
| Connected-app scopes: Exchange read | `core/auth.py`, docs | UNVERIFIED | — | — | — |
| Connected-app scopes: API Manager write | provisioning | UNVERIFIED | — | — | — |
| Connected-app scopes: policy management | provisioning | UNVERIFIED | — | — | — |
| Which ops require an **admin** connected app w/ user context | `core/auth.py` | UNVERIFIED | — | — | — |

## 2. LLM Proxy (data plane) — **LIVE-VERIFIED 2026-08-28**

A real governed ingress LLM proxy (`openai-sdk`, instance `21133858`) was
deployed to `agent-network-ingress-gw` in the sandbox and called end-to-end
(HTTP 200 with a real OpenAI completion). Captures in
`tests/fixtures/anypoint/llm_proxy/`. The proxy is an **OpenAI-compatible
passthrough**: the SDK points a framework's OpenAI client at the proxy base and
sends normal OpenAI request bodies; the upstream response is returned verbatim
plus Anypoint governance headers. `VERIFIED (LIVE)` = observed from a real
request against the deployed gateway.

| Item | Where used | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| Ingress base URL the SDK targets | `llm/client.py` | VERIFIED (LIVE) | `https://<ingress-gw-host>/<instance-path>/` — e.g. `https://agent-network-ingress-gw-zovwbn.jeg62f.usa-e2.cloudhub.io/openai-sdk/`. **No `/v1` at the ingress**; OpenAI path segment appended directly (`/responses`, and OpenAI-native routes) | 2026-08-28 | live probe |
| Endpoint/API surface | `llm/client.py` | VERIFIED (LIVE) | OpenAI **Responses API** works (`POST /openai-sdk/responses`, body `{model, input}`). Upstream registered as `https://api.openai.com/v1/`; `proxyUri http://0.0.0.0:8081/openai-sdk` | 2026-08-28 | `api:describe`, live probe |
| Auth: header name / model | `core/transport.py`, `core/config.py` | VERIFIED (LIVE) | **`client_id` + `client_secret` request headers** (NOT bearer) — enforced by `client-id-enforcement` 1.3.3. A consumer **credential pair**, mapped to an Anypoint client application | 2026-08-28 | live probe + `policy:list` |
| Auth: model-wallet ingress (alt to `client_id`/`client_secret`, #372) | `core/transport.py`, `core/config.py` — UNVERIFIED, not wired | UNVERIFIED | Wallet-backed model proxies auth via an **IdP-issued JWT + a client ID**, with **no `client_secret`**: the client ID travels as the `X-Client-Id` request header (selects the wallet) *and* as a `client_id` **JWT claim** (`#[authentication.properties.claims.client_id]`, read after the **JWT Validation** policy publishes verified claims); requires an org **IdP** (JWKS URL / signing key — "orgs without an IdP can't use model wallets"); the default **DataWeave Headers Transformation** + **Client ID Enforcement** policies are disabled. A **parallel** ingress model, NOT a replacement of the LIVE `client_id`/`client_secret` pair above. **Live-capture worklist:** confirm the exact `X-Client-Id` header name/casing, the JWT claim path, and the request auth shape against a wallet-enabled sandbox with an IdP configured — until then no `_verify.py` value is marked verified and no guard comes off (§12.8; a doc read is not a live capture). | — | `docs.mulesoft.com/general/exp-model-wallets-manage` (doc read, not a live probe) |
| Model routing | `llm/client.py`; consumed by `core/lastcall.py` + `core/transport.py` (#309) | VERIFIED (LIVE) | `model-based-routing` 1.0.3 reads `model` from body → provider. Response headers `x-llm-proxy-routing-type: ModelBased`, `x-llm-proxy-routing-fallback: false`, `x-llm-proxy-llm-provider: openai`, `x-llm-proxy-llm-model: gpt-5.1`, `x-llm-proxy-model-based-routing-success: Request successfully matched. …`. As of #309 the SDK surfaces these at `donkey.last_call` (`served_provider`/`served_model`/`routing_type`/`fallback`, `substituted` when served≠requested), emits them as `gen_ai.response.model` / `donkey.routing.type` / `donkey.routing.fallback` span attrs, and **does not retry** a `503` a fallback header already marked failed-over (#309, must not fight #183) | 2026-08-28 | live probe (`responses.success.headers.txt`) |
| Token accounting (cost attribution) | `llm/*`, telemetry | VERIFIED (LIVE) | response `usage: {input_tokens, output_tokens, total_tokens, input_tokens_details, output_tokens_details}` — the detail sub-objects carry `cached_tokens` / `cache_write_tokens` (under `input_tokens_details`) and `reasoning_tokens` (under `output_tokens_details`), surfaced per-call at `donkey.last_call` and on the span (#307, see §3); also upstream `x-ratelimit-*` headers passed through (Go-style **duration-string** resets, e.g. `0s`/`12ms` — distinct from the gateway's own `x-llm-proxy-ratelimit`/`x-token-*` window, see §4) | 2026-08-28 | `responses.success.body.json` |
| Request fields passed through | `llm/client.py` | VERIFIED (LIVE) | OpenAI body passed through verbatim; response returned verbatim (model `gpt-5.1`→`gpt-5.1-2025-11-13`, full Responses object) | 2026-08-28 | live probe |
| Streaming support | `llm/client.py` | VERIFIED (LIVE) | `"stream": true` → `200`, `content-type: text/event-stream`, chunked SSE (`event: response.created` / `response.in_progress` / …); same `x-llm-proxy-*` headers | 2026-08-28 | live probe (`responses.stream.*`) |
| `/models` endpoint | `llm/catalog.py` | VERIFIED (LIVE) | **Does not exist** — `GET /openai-sdk/models` → `404`, `x-llm-proxy-model-based-routing-success: Request passed through without model-based routing`. The proxy only routes requests carrying `model` in the body; no catalog endpoint. `llm/catalog.py` must source models elsewhere | 2026-08-28 | live probe (`models.notfound.headers.txt`) |
| Supported providers | `llm/catalog.py`, `governance.py` | VERIFIED (CLI) | openai, azureopenai, gemini, **bedrock**, **anthropic** (each a `*-llm-provider-policy-flex` in org `68ef9520…`) | 2026-08-28 | `exchange:asset:list llm` |

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
happy path and surfaces them at `donkey.last_call`: `x-request-id` becomes
`LastCall.request_id` (the same value `classify()` surfaces as
`DonkeyError.request_id` on a refusal), and `x-envoy-decorator-operation`
(`api-instance-<instanceId>.<environmentId>.svc`) is split into
`LastCall.api_instance_id` (`21133858`) and `LastCall.environment_id`
(`3e6ce455-…`, a UUID — dashes but no dots, so a three-way split on `.` is
unambiguous). An absent or unrecognised shape leaves both `None` and never
raises (verification discipline). The response `x-correlation-id` is **not** parsed into the record
yet — whether it echoes the client-sent id is unconfirmed (#300).

As of #307, `core/lastcall.py` also parses the per-call **usage token counts**
from the response *body* (the already-VERIFIED `usage` block, §2) and surfaces
them on the same record: `input_tokens` / `output_tokens` / `total_tokens`, plus
the cost-relevant `cached_tokens` / `cache_write_tokens` (from
`usage.input_tokens_details`) and `reasoning_tokens` (from
`usage.output_tokens_details`). Both the Responses-API (`input_tokens*`) and
Chat-Completions (`prompt_tokens*` / `completion_tokens*`) shapes are read. An
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
| Per-agent attribution unit (model-wallet ingress, #372) | `core/config.py`, transport | UNVERIFIED | under a **model wallet**, the caller is identified by the JWT `client_id` claim + the `X-Client-Id` request header (not the `client_id`/`client_secret` pair) and requires an org IdP — see the §2 "model-wallet ingress" row for the full shape and live-capture worklist. Doc read only; nothing wired or verified. | — | `docs.mulesoft.com/general/exp-model-wallets-manage` (doc read) |
| Gateway identity on response | `core/transport.py` (`x-llm-proxy-llm-provider` → `gen_ai.system`, #192); `core/lastcall.py` **parses** `x-envoy-decorator-operation` and `x-request-id` (#362) | VERIFIED (LIVE) | `x-envoy-decorator-operation: api-instance-21133858.3e6ce455-…svc`; `x-correlation-id`; `x-llm-proxy-llm-provider/-llm-model/-routing-type` | 2026-08-28 | `responses.success.headers.txt` |
| Agent→agent egress attribution header | `core/_verify.py` → transport | VERIFIED (build) | `x-anypoint-api-instance-id` → `agent-connection-telemetry` policy `sourceAgentId`; `tracing` labels `mulesoft.api.instance.id`, `mulesoft.api.type=llm` | 2026-08-28 | built `connection.json` (§12.6) |
| Business-group attribution header name | `core/_verify.py` → transport | UNVERIFIED | not surfaced as a request header in the direct-proxy path | — | — |
| Run correlation id **request** header (`X-Correlation-Id`, #195) | `core/_verify.py` `CORRELATION_ID_HEADER` → transport | UNVERIFIED | `x-correlation-id` is verified as a **response echo** (row above); that the gateway **reads** an inbound `X-Correlation-Id` as the run/trace join key is NOT confirmed. Placeholder, overridable via `correlation_header` / `DONKEY_CORRELATION_HEADER`. | — | — |
| Per-call id **request** header (`X-Donkey-Request-Id`, #195) | `core/_verify.py` `CALL_ID_HEADER` → transport | UNVERIFIED | client-generated per logical request, stable across that request's retries; no evidence the gateway reads this name yet. Placeholder, overridable via `call_id_header` / `DONKEY_CALL_ID_HEADER`. | — | — |
| Cost tag: team **request** header (`X-Anypoint-Cost-Team`, #196) | `core/_verify.py` `COST_TEAM_HEADER` → transport | UNVERIFIED | the gateway-side cost-attribution header name is the highest-priority unknown; placeholder, overridable via `cost_team_header` / `DONKEY_COST_TEAM_HEADER`. Value always carried on the `donkey.cost.team` span attribute regardless (SDK owns the span). | — | — |
| Cost tag: project **request** header (`X-Anypoint-Cost-Project`, #196) | `core/_verify.py` `COST_PROJECT_HEADER` → transport | UNVERIFIED | placeholder, overridable via `cost_project_header` / `DONKEY_COST_PROJECT_HEADER`; full value on `donkey.cost.project` span attribute. | — | — |
| Cost tag: env **request** header (`X-Anypoint-Cost-Env`, #196) | `core/_verify.py` `COST_ENV_HEADER` → transport | UNVERIFIED | placeholder, overridable via `cost_env_header` / `DONKEY_COST_ENV_HEADER`; full value on `donkey.cost.env` span attribute. | — | — |
| Cost tag: enduser id **request** header (`X-Anypoint-Cost-Enduser-Id`, #196) | `core/_verify.py` `COST_ENDUSER_HEADER` → transport | UNVERIFIED | placeholder, overridable via `cost_enduser_header` / `DONKEY_COST_ENDUSER_HEADER`; full value on `donkey.cost.enduser.id` span attribute. | — | — |

The two #195 rows are **request** headers the SDK *sends* (the client→gateway
join keys behind `donkey.run()` and `DonkeyError.correlation_id`/`.call_id`).
The verified `x-correlation-id` above is the gateway's **response** echo — a
different direction. Until an inbound-read name is confirmed against a sandbox,
both request-header names stay `Unverified(...)` placeholders and emit the verification-discipline
one-time warning; a customer whose gateway reads different names points the SDK
at them via config rather than the SDK guessing.

The four #196 cost-tag rows follow the same discipline: the fixed dimensions
(`team` / `project` / `env` / `enduser.id`, set once via
`Donkey.from_env(team=…)` / `[donkey.cost]` / `DONKEY_COST_*`, overridable per
run via `donkey.run(team=…)`) are emitted as `X-Anypoint-Cost-*` placeholder
**request** headers *and* as `donkey.cost.*` **span** attributes. Because the
SDK controls the span end to end, the span attribute always carries the full
value regardless of the header question — so cost attribution works in tracing
today, and flips to on-the-wire headers the moment a customer sets the
`cost_*_header` overrides or the gateway-side names are verified here.

## 4. Policy rejection response shapes (capture as fixtures, BG §1.5)

**Four rejection shapes** are LIVE-VERIFIED (2026-08-28) from the `openai-sdk`
proxy (client-id-enforcement, upstream passthrough, PII, token-rate-limit).
Fixtures in `tests/fixtures/anypoint/llm_proxy/reject.*`. The critical lesson:
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
3. **PII detection** — `403` **but NOT auth**: nested object
   `{"error":{"message":"Request contains PII data: […]","type":"pii_detected"}}`,
   **no** `code`/`param`, and crucially **no** `www-authenticate` header. The
   `message` embeds a JSON list of `{"pii_type","value","start","end"}` entries.
4. **Token rate limit** — `429` with an **empty body** (`content-length: 0`).
   Budget state is header-only: `x-token-limit`, `x-token-remaining`,
   `x-token-reset` (**milliseconds** to reset). There is **NO** `retry-after`.

`core/errors.classify()` implements this (tests: `test_llm_proxy_contract.py`,
`test_rejection_contract.py`, `test_errors.py`): error `type == "pii_detected"` →
`PIIDetected` (checked *before* the 401/403→auth rule; parses `entities` from the
message); a top-level `matched_patterns` list → `PromptInjectionBlocked`
(`policy="regex-prompt-guard"`); a vendor `x-llm-proxy-<vendor>-…-action: reject`
header (Azure Content Safety / Amazon Bedrock Guardrails) → `ContentSafetyBlocked`
(parses `categories` from the sibling `…-reason` header) — both of these also
checked *before* the 401/403→auth rule so a `403` moderation block is not
mis-typed as auth; header `x-injection-protection: blocked` →
`PromptInjectionBlocked` (the header, not the status, is the discriminator, so a
bare `400` is unaffected); `429` → `TokenBudgetExceeded` with `retry_after`
derived from `x-token-reset` (ms→s); non-auth 4xx with a nested `error` object →
`UpstreamRequestError` (carries provider `code`/`type`/`param`); `5xx` →
`UpstreamModelError`. Auth is then the verified client-id-enforcement shape: a
`401`, **or** a `403` carrying a `www-authenticate` header → `AuthError`. A `403`
**without** `www-authenticate` that matched none of the discriminators above is
therefore *not* coerced to auth — it falls through to a generic `PolicyViolation`
whose message names the observed status and any `x-llm-proxy-*` policy headers and
states the **shape is unconfirmed** (#184), rather than being mis-typed. The full
eight-shape taxonomy is indexed in `tests/fixtures/rejections/README.md`.

Two of those shapes are **DOCUMENTED, pending live capture (#253)** — typed from
the policy pages, not from a live sandbox round-trip, so **no `_verify.py` row
flips to `verified=True`** for them:

| Shape | HTTP | Discriminator | Maps to | Source |
|---|---|---|---|---|
| Regex Prompt Guard | `403` | top-level `matched_patterns` list (flat-string `error`) | `PromptInjectionBlocked` (`policy="regex-prompt-guard"`) | DOCUMENTED, pending live capture (#253) |
| Content safety / guardrails | `403` | `x-llm-proxy-azure-content-safety-action` / `x-llm-proxy-bedrock-guardrail-action` == `reject`; reasons in the sibling `…-reason` header | `ContentSafetyBlocked` (parses `categories`) | DOCUMENTED, pending live capture (#253) |
| Unrecognised refusal (fall-through) | any non-429 `4xx` | matches **none** of the discriminators above; no nested `error` envelope; no `www-authenticate` (e.g. an unrecognised `403`) | generic `PolicyViolation` (`policy="unknown"`), message says **shape unconfirmed** and names the observed status + `x-llm-proxy-*` headers | UNVERIFIED — no known contract; capture + type via #184/#253 |

The **injection body** and any **other content-moderation / federated-guardrail**
shape remain uncaptured. Rather than guess a type for them, `classify()` surfaces
any such unrecognised refusal **honestly** — a `PolicyViolation` whose message
states the shape is unconfirmed and whose remediation asks the operator to file
the observed status/headers/body so the shape can be typed (#184). This is the
last row above; it is deliberately **not** an `AuthError`, even for a `403`, once
the verified `www-authenticate` auth shape is excluded. Re-confirming all of these
against current docs and a sandbox is tracked in #253 (verification discipline: no invented docs URL
or version is recorded for them).

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
`x-ratelimit-reset-tokens` headers on a `200` are the **upstream provider's**
own quota, passed straight through — not the gateway's budget. Their reset
values are Go-style **duration strings** (`0s`, `12ms`), **not** integer
milliseconds, so they must not be parsed with the `x-token-*` / `ms` rule.

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

| Policy | Exchange asset (verified) | Status | Rejection shape | Date | Source |
|---|---|---|---|---|---|
| client-id-enforcement | `client-id-enforcement` `1.3.3` | VERIFIED (LIVE) | `401` + `www-authenticate: Client-ID-Enforcement`, `{"error":"Client ID is not present"}` | 2026-08-28 | live probe |
| model-based-routing / upstream | `model-based-routing` `1.0.3` | VERIFIED (LIVE) | passthrough of provider error (OpenAI `400 model_not_found` object) | 2026-08-28 | live probe |
| LLM proxy core | `llm-proxy-core` `1.0.5` | applied VERIFIED (LIVE) | on `openai-sdk`; rejection body not yet triggered | 2026-08-28 | `policy:list` |
| Token rate limiting | interface `llm-token-rate-limit` `1.0.2` (impl `-policy-flex` `1.0.4`) | VERIFIED (LIVE) | Two emission forms: `429` limit-exceeded → **empty body**, numeric trio `x-token-limit`/`x-token-remaining`/`x-token-reset`(ms), no `retry-after`; `200`/`403` under the same policy → the window as prose in a single `x-llm-proxy-ratelimit` header (ms reset). See "Budget window emission" above. | 2026-08-28 | applied + live probe |
| PII detection | interface `llm-pii-detection-policy` `1.0.0` (impl `-flex` `1.0.2`) | VERIFIED (LIVE) | `403`, nested `{"error":{message,type:"pii_detected"}}`, no `www-authenticate` | 2026-08-28 | applied + live probe |

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
This reshapes §5: the SDK's declarative `donkey.yaml` plan/apply either wraps
this CLI/Maven toolchain or emits its project layout — it does not invent a REST
provisioning API. Exact REST calls behind the CLI are now recorded in §12
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
| Can Local Mode run the LLM Proxy? | the Verification milestone | UNVERIFIED | — | — |
| Can Local Mode run MCP Bridge? | the Verification milestone | UNVERIFIED | — | — |
| Does Local Mode need a control-plane licence/registration artifact? | OSS/CI viability | UNVERIFIED | — | — |
| Which policies are Connected-Mode-only? (portability table) | the Verification milestone | UNVERIFIED | — | — |
| Is "deployed to gateway" readable per API instance? | `require_deployed` | VERIFIED (CLI) | Yes — `api-mgr:api:list` (per env) + `:api:describe <id>` returns Endpoint URI, gateway, deployment target | 2026-08-28 |
| Are applied policies readable per API instance? | governed-state join | VERIFIED (CLI) | Yes — `api-mgr:policy:list <id>` returns `{ID, Template ID, Asset ID, Asset Version, Label, Status, Configuration}` | 2026-08-28 |
| Are governance ruleset results exposed via API? | `require_governance_pass` | PARTIAL | `governance:api` evaluates rulesets; `governance:profile:*` manages profiles (ruleset refs = Maven GAV). Result-read shape pending | 2026-08-28 |
| Can applied policies be fetched in bulk per environment? | governed-only discovery perf | UNVERIFIED | per-instance confirmed; bulk endpoint pending plugin analysis | — |
| MCP-specific + enforcement policies observed | the Verification milestone (portability) | VERIFIED (CLI) | `mcp-support` (`injectMcpNameHeaders`), `client-id-enforcement` (client_id/client_secret headers), `header-injection` (`x-gateway-token`) | 2026-08-28 |
| Governed MCP endpoint URL shape | `McpServerHandle.endpoint_url` | VERIFIED (CLI) | ingress gw: `https://agent-network-ingress-gw-<id>.<region>.cloudhub.io/mcp/<name>/` | 2026-08-28 |
| Governed **LLM proxy** policy stack (live) | governed-state join, the Verification milestone | VERIFIED (LIVE) | on `openai-sdk`: `cors 1.3.2`, `dataweave-headers-transformation 1.0.0`, `client-id-enforcement 1.3.3`, `llm-proxy-core 1.0.5`, `model-based-routing 1.0.3`, `openai-transcoding-policy 1.0.3` — all Enabled | 2026-08-28 |

## 7. Publication / Exchange (BG §2.5)

| Item | Gates | Status | Finding | Source |
|---|---|---|---|---|
| First-class Exchange asset types for MCP servers & AI agents? | BG §2.5 + `asset_types` filter (governed-only discovery) | VERIFIED (CLI) | Yes — Exchange assets named "… MCP Server" and "… Agent Network"/agent, managed as API Manager instances | 2026-08-28: `api-mgr:api:list` |
| Publication mechanism for non-Mule assets (REST / CLI / Maven)? JVM needed? | §7 CI story | VERIFIED (CLI) | Maven + CLI: `agent-network project publish` publishes the built project to Exchange; **JVM required** (`mvnw`) | 2026-08-28: `agent-network:project:publish --help` |
| Publication uses Maven GAV coordinates | BG §2.5 | VERIFIED (CLI) | group-id/asset-id/asset-version; groupId defaults to org id | 2026-08-28: `:project:create --help`, `api-mgr:api:describe` |
| Documentation pages publishable? | BG §2.5 | PARTIAL | `exchange asset page` + `exchange asset resource` topics exist | 2026-08-28: `exchange:asset --help` |
| Can arbitrary metadata/tags be attached (content digest)? | BG §2.5 | UNVERIFIED | Tags field exists on instances (empty here); attach mechanism pending | — |
| Asset lifecycle states (draft/published/deprecated) via API? | `require_lifecycle`, BG §2.5 | PARTIAL | `Deprecated` + `Public` flags on instances; A2D showed `status: draft\|published` | 2026-08-28 |
| Native descriptor formats (MCP manifest, A2A card) vs file attach? | BG §2.5 output | VERIFIED (plugin) | typed Exchange files w/ fixed classifiers (`agent-metadata`, `mcp-metadata`, `llm-metadata`, `a2a-card`, `schema`, `mule-application`) attached to `agent-network` root asset | 2026-08-28 | §12.7 |

## 8. Framework APIs (BG §1.8) — re-verify every constructor

| Framework | Symbol / kwarg | Status | Verified value | Date | Source |
|---|---|---|---|---|---|
| LangGraph | `langchain_openai.ChatOpenAI(base_url, api_key, default_headers, http_async_client, use_responses_api=True)` | UNVERIFIED (constructor); endpoint VERIFIED | The kwargs/class name are still §8-pending, but the deep adapter (#198) pins `use_responses_api=True` so it calls the **live-verified** `/responses` data plane (§4) rather than ChatOpenAI's default `/chat/completions` route. Overridable via `chat_model(..., use_responses_api=False)`. | 2026-09-11 | #198 |
| Google ADK | `google.adk.models.lite_llm.LiteLlm(model="openai/…", api_base, extra_headers)` | UNVERIFIED | — | — | — |
| MS Agent Framework | `agent_framework.openai.OpenAIChatClient` name + `model_id` kwarg | UNVERIFIED | — | — | — |
| OpenAI Agents SDK | `agents.OpenAIChatCompletionsModel(model, openai_client=AsyncOpenAI(...))` | UNVERIFIED | — | — | — |
| Anthropic SDK | `anthropic.AsyncAnthropic(base_url, api_key, default_headers, http_client)` — model id per-call; proxy Anthropic-native route UNVERIFIED | UNVERIFIED | — | — | — |
| CrewAI | `crewai.LLM(model="openai/…", base_url, api_key, extra_headers)` | UNVERIFIED | — | — | — |
| LlamaIndex | `llama_index.llms.openai_like.OpenAILike(is_chat_model=True)` | UNVERIFIED | — | — | — |
| Strands | `strands.models.openai.OpenAIModel(client_args={...})` | UNVERIFIED | — | — | — |

### 8.1 Known upstream incompatibilities (floors, not ceilings)

Per the floors-never-ceilings rule the extras declare **floors and no ceilings**, so a fresh resolve always
takes the newest release. That is deliberate: the nightly framework matrix exists
to find breakage early rather than to pin it away. Recorded here so a red matrix
run is diagnosable instead of surprising.

| Dependency | Breaks at | Symptom | Status | Date |
|---|---|---|---|---|
| `openai` | `>=3.0` | The SDK passes its shared `DonkeyAsyncClient` (an `httpx.AsyncClient` subclass) into `AsyncOpenAI(http_client=…)`. openai 3.x retyped that parameter to `httpx2.AsyncClient`, a distinct class from a separate distribution, so `mypy --strict` flagged `llm/client.py` and `integrations/openai_agents.py`. Calls still succeed at runtime — openai duck-types the client — so this was a typecheck failure, not a test failure. **Mitigated** with a targeted `# type: ignore[arg-type]` at the three call sites (`llm/client.py` ×2, `integrations/openai_agents.py`); `mypy --strict` passes under openai 3.x (#137). | MITIGATED | 2026-09-02 |

The targeted ignores keep `mypy --strict` green against the newest `openai` a fresh
resolve pulls (the floors-never-ceilings rule). A full fix — migrating `core/transport.py` off `httpx` onto
`httpx2` — also touches every adapter that accepts `http_async_client` /
`client_args`, so it is a core-layer change (the layered architecture) and out of scope for the mitigation.
Because the ignores are code-specific and `--strict` enables `warn_unused_ignores`, a
pinned `openai<3` (where the mismatch does not occur) would now flag them as *unused* —
a non-issue while the extras stay floor-only and every resolve takes openai 3.x.

## 9. MCP tool binding classes (BG §2.7) — verify each name

| Framework | Binding class | Status | Source |
|---|---|---|---|
| LangGraph | `langchain_mcp_adapters.client.MultiServerMCPClient` | UNVERIFIED | — |
| Google ADK | `McpToolset` + `StreamableHTTPConnectionParams` | UNVERIFIED | — |
| MS Agent Framework | MCP client/tool class for streamable HTTP | UNVERIFIED | — |
| OpenAI Agents SDK | `agents.mcp.MCPServerStreamableHttp` | UNVERIFIED | — |
| Anthropic SDK | streamable-HTTP MCP via SDK `mcp_servers` integration | UNVERIFIED | — |
| CrewAI | `crewai_tools.MCPServerAdapter` | UNVERIFIED | — |
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
| CrewAI | `.name` `.description` `.args_schema.model_json_schema()` | UNVERIFIED | — |
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
> wire `core/transport.py` attribution to it without a data-plane capture.

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

Pre-existing asset: `82a0453b…/llm-test-proxy/1.0.0` — Exchange type `llm`, tag
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
