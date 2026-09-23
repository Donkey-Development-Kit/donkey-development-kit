# Verification policy

This is the most important thing to understand about the SDK's trustworthiness.

  **Never invent an endpoint, header name, or class name.** A fabricated
  endpoint that 404s in a customer sandbox destroys trust in the whole package.

Every platform fact the SDK relies on is either **verified against a real
Anypoint sandbox / an installed framework**, or it is **not used** — the code
path that would need it raises `NotImplementedError("blocked on verification: …")`
or a clear `ConfigError` instead of guessing. How each value is confirmed — and
how a blocked value flips to verified — is documented for contributors in
[ARCHITECTURE.md → Verification discipline](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/ARCHITECTURE.md#verification-discipline).

## What is verified today

- **LLM proxy data plane** (live-verified): the **OpenAI-format ingress** —
  base-URL shape (`https://<ingress>/<instance>/`, no `/v1`), the `client_id` +
  `client_secret` consumer-auth header pair, model-based routing, streaming,
  token accounting, and the absence of a `/models` endpoint (`404`). The proxy's
  ingress **Format** (OpenAI / Anthropic / Gemini) is chosen at provisioning
  time; OpenAI is the primary verified path and the **Gemini**-native ingress is
  also live-verified (#540) — see the row below and
  [Which wire format does your proxy speak?](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md#which-wire-format-does-your-proxy-speak).
- **Attribution** (live-verified): the `client_id` credential is the per-agent
  attribution unit; the gateway emits identity/telemetry on the response.
- **Six policy rejection shapes** (live-verified): auth `401`, PII `403`,
  token-budget `429`, upstream passthrough, plus the **regex prompt-guard** and
  **Azure content-safety** `403` blocks (the last two confirmed 2026-09-22
  against the deployed provisioning proxies, #253). See [Error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md).

## What is still blocked — and why that's a feature

| Area | Status | What the SDK does about it |
|---|---|---|
| Non-OpenAI proxy **ingress Format** (Anthropic / Gemini) | Anthropic Format documented, route path/behavior unverified (#304); Gemini-native ingress **VERIFIED (LIVE)**, no SDK adapter (#540) | The default data-plane path is **Format=OpenAI**. MuleSoft Model Proxy [documents](https://docs.mulesoft.com/general/model-proxy) three selectable ingress Formats (OpenAI / Anthropic / Gemini) fixed at proxy creation. The **Gemini**-native ingress is now **VERIFIED (LIVE)**: a `Format=Gemini` proxy serves a native `POST …/models/<model>:generateContent` passthrough (200, native Gemini body; OpenAI-shape → 400), captured in `tests/fixtures/anypoint/gemini_inbound/` (#540). The **Anthropic**-native Messages route is documented but its exact path + live behavior are still **UNVERIFIED** (no `Format=Anthropic` proxy provisioned yet); `donkey.anthropic.client()` points `AsyncAnthropic` at it and warns on first use (#304). Either way the SDK ships **no** `donkey.gemini` adapter — a native `google-genai` adapter is demand-driven (#244); Gemini and Claude models are also reachable as **upstream providers** behind an OpenAI-format ingress via model-based routing, which works today. Ingress Format is a property of the provisioned proxy, not an SDK setting (no `llm_proxy_format` field). See [Which wire format does your proxy speak?](https://donkey-development-kit.github.io/donkey-development-kit/frameworks.md#which-wire-format-does-your-proxy-speak). |
| Framework **constructor signatures** (docs/verified-apis.md §8) | Unverified per framework | Adapters target recorded class names; a mismatch surfaces via the verification harness / nightly matrix rather than silently misbehaving. Agent Framework's client raises `blocked on verification` today. |
| MCP tool discovery + binding ([Phase 2](https://donkey-development-kit.github.io/donkey-development-kit/tool-access.md)) | Unverified | `donkey.tools.discover` and the binding classes raise `blocked on verification`. |
| Exchange publish API ([Phase 2](https://donkey-development-kit.github.io/donkey-development-kit/publishing.md)) | Partially verified | First-class MCP/agent asset types exist, but the exact publication and discovery token vocabulary is unverified. `donkey publish` and Exchange search remain blocked rather than sending the SDK's assumed `mcp` / `a2a-agent` values. |
| Token-exchange endpoint for [on-behalf-of](https://donkey-development-kit.github.io/donkey-development-kit/identity.md) (Phase 2) | Unverified | The exact endpoint and header the gateway expects are not guessed. |
| [Policy discovery](https://donkey-development-kit.github.io/donkey-development-kit/policies.md) (Phase 3) | **Missing upstream** | No such endpoint exists on the gateway today. Filed as an upstream gap — the handshake cannot be built until it ships. |
| Budget query endpoint ([Phase 1](https://donkey-development-kit.github.io/donkey-development-kit/budget.md)) | **Missing upstream** | Budget is reported in-band on response headers only, so `remaining` is last-known-good; `observed_at` exposes the staleness. |
| Whether Anypoint Monitoring ingests OTLP GenAI spans ([Phase 1](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md)) | Unverified | The SDK promises "exports OTLP" and lets the sink be your choice. |
| A provisioning control plane | **Cut** | Not built. It would compete with API Manager and Terraform — see [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md). |
| Injection-protection / Bedrock-guardrails / fall-through bodies | Not captured | The **regex prompt-guard** (`matched_patterns` list) and **Azure content-safety** (`…-action: reject` header) bodies are now **live-captured** (2026-09-22, #253). Still uncaptured: the distinct **Injection Protection** policy's body (typed by the `x-injection-protection: blocked` header; no such proxy is deployed), the **Bedrock Guardrails** sibling of content-safety, and the undiscriminated content-moderation fall-through to a generic `PolicyViolation` — all pinned from the policy pages, and no verification row flips until a live round-trip confirms them. No invented discriminator — tracked in #253. |
| First-party **TypeScript SDK** | Planned | TypeScript examples reach the proxy over its OpenAI-compatible API via the `openai` / `@anthropic-ai/sdk` clients; no first-party TS package is implied until it ships. |

## What this means for you

As a consumer you don't have to track any of this. Any platform fact the SDK
hasn't confirmed raises `blocked on verification` rather than guessing, so a
governed call either works against the verified contract or fails loudly — it
never silently reaches an unconfirmed endpoint. The mechanism behind that and
the full verified-versus-blocked ledger are documented for contributors in
[ARCHITECTURE.md → Verification discipline](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/ARCHITECTURE.md#verification-discipline).
