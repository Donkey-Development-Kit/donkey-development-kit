# Anthropic-native ingress — LIVE capture (docs/verified-apis.md §2, #304)

Captured **2026-09-24** from a real deployed Agent Network LLM proxy provisioned
with the model-proxy **Format = Anthropic** ("native Anthropic ingress") in the
DDK sandbox, env **Sandbox** (svc id `14d3b31e-4e3b-4d90-b77a-63c9d6b7ea6a`, from
the `x-envoy-decorator-operation` header). Proxy asset `ddk-anthropic-inbound`,
API Manager instance **`21194086`**, deployed to the `shared-omni-gateway` Flex
Gateway, single-route passthrough to an Anthropic upstream
(`https://api.anthropic.com/`, model `claude-haiku-4-5-20251001`).

This resolves issue **#304** ("verify whether the LLM proxy exposes an
Anthropic-native Messages API route, and at what path"), previously UNVERIFIED /
doc-read only. It is the sibling of the Gemini ingress captured in
`../gemini_inbound/`, the OpenAI ingress in `../llm_proxy/`, and the model-wallet
JWT ingress in `../model_wallet/`. The three selectable Formats (OpenAI / Gemini /
Anthropic) are documented at `docs.mulesoft.com/general/model-proxy`; all three
are now live-confirmed.

## What is verified (all witnessed live, not from the provisioning YAML's own claim)

- **The native route exists at `POST /<base-path>/v1/messages`.** The Anthropic
  Messages API request shape (header `anthropic-version: 2023-06-01`, body
  `{"model":…,"max_tokens":N,"messages":[{"role":"user","content":…}]}`) returns
  **HTTP 200** with a genuine native Anthropic body — `type:"message"`,
  `role:"assistant"`, `content[].text` (`"PONG"`), `stop_reason:"end_turn"`,
  `usage.input_tokens`/`output_tokens`/`service_tier`. See `request.success.http`
  + `responses.success.{body.json,headers.txt}`.
- **It is a passthrough, not a transcode.** The 200 carries native Anthropic
  response headers (`anthropic-ratelimit-*`, `request-id`,
  `anthropic-organization-id`, `anthropic-workspace-id`) forwarded verbatim, plus
  the gateway's own `x-llm-proxy-llm-provider: anthropic`,
  `x-llm-proxy-llm-model: claude-haiku-4-5-20251001`, and
  `x-llm-proxy-request-success`. `routingType` stays `model-based`; the
  passthrough is driven by the per-upstream
  `routing[].upstreams[].llmConfigs.format: anthropic` field (see the provisioning
  repo's `proxies/ddk-anthropic-inbound.yaml`).
- **The ingress is genuinely native Anthropic, not OpenAI.** An OpenAI-shaped
  request to `/<base-path>/chat/completions` (`{"model":…,"messages":[…]}`) is
  **rejected with HTTP 404** and an **empty body** — the OpenAI route is simply
  not served on a native-Anthropic ingress. (Note the difference from the Gemini
  ingress, where the same probe returned a native-Gemini **400**; both outcomes
  prove the ingress does not accept the OpenAI wire format, but the Anthropic
  ingress 404s the path outright rather than forwarding a malformed body
  upstream.) See `reject.openai-shape.*`. We record the observed 404 shape; we do
  not assert *why* the path is unserved beyond "the OpenAI-shape route is not
  accepted".
- **Client ID Enforcement is on.** A request with no `client_id` header →
  **HTTP 401** `{"error":"Client ID is not present"}` with
  `www-authenticate: Client-ID-Enforcement` — identical to the OpenAI and Gemini
  proxies' CIE behavior in `../llm_proxy/` and `../gemini_inbound/`. The consumer
  auth pair is the same `client_id` / `client_secret` header pair verified in §2.

## The roadmap "OAuth Client ID/Secret for configuring LLMs" does NOT change this

Issue #304's second question: whether the roadmap line "Support OAuth Client
ID/Secret for configuring LLMs" changes the verified consumer
`client_id`/`client_secret` request-header pair. It does not — that item concerns
how a proxy's **upstream provider credential** is configured (a control-plane
concern; this proxy uses a static `x-api-key` to `api.anthropic.com`). The
**consumer-facing data-plane auth** on this native-Anthropic ingress is the same
`client_id`/`client_secret` request-header CIE pair witnessed here (401 without
it), unchanged from §2/§3.

## How this was captured

A consumer `client_id`/`client_secret` minted against instance `21194086` via the
`ddk-request-llm-proxy-access` flow was used to issue the three data-plane
requests above directly. Consumer credentials are **redacted** from
`request.success.http` and were never persisted here.

## The `donkey.anthropic` adapter still requires a `Format=Anthropic` proxy

Verifying the route lifts the adapter's open verification item, but the SDK's own
DDK proxies are provisioned `Format=OpenAI` — so `donkey.anthropic.client()`
pointed at them reaches Claude only as an *upstream provider*, not via this native
surface. Point `base_url` at a `Format=Anthropic` proxy (like `ddk-anthropic-inbound`)
to use the native Messages ingress. Native ingress is **single-route only** (no
multi-routing/fallback — that stays OpenAI-only) and the upstream must speak
native Anthropic (`provider: anthropic`; Bedrock is not usable here).
