# Verification policy

This is the most important thing to understand about the SDK's trustworthiness.

  **Never invent an endpoint, header name, or class name.** A fabricated
  endpoint that 404s in a customer sandbox destroys trust in the whole package.

Every platform fact the SDK relies on is either **verified against a real
Anypoint sandbox / an installed framework**, or it is **not used** — the code
path that would need it raises `NotImplementedError("blocked on verification: …")`
or a clear `ConfigError` instead of guessing. How each value is confirmed — and
how a blocked value flips to verified — is documented for contributors in
[ARCHITECTURE.md → Verification discipline](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/ARCHITECTURE.md#verification-discipline-03).

## What is verified today

- **LLM proxy data plane** (live-verified): base-URL shape
  (`https://<ingress>/<instance>/`, no `/v1`), the `client_id` + `client_secret`
  consumer-auth header pair, model-based routing, streaming, token accounting,
  and the absence of a `/models` endpoint (`404`).
- **Attribution** (live-verified): the `client_id` credential is the per-agent
  attribution unit; the gateway emits identity/telemetry on the response.
- **The four policy rejection shapes** (live-verified): auth `401`, PII `403`,
  token-budget `429`, upstream passthrough. See [Error taxonomy](https://donkey-development-kit.github.io/donkey-development-kit/errors.md).

## What is still blocked — and why that's a feature

| Area | Status | What the SDK does about it |
|---|---|---|
| Framework **constructor signatures** (§8) | Unverified per framework | Adapters target recorded class names; a mismatch surfaces via the verification harness / nightly matrix rather than silently misbehaving. Agent Framework's client raises `blocked on verification` today. |
| MCP tool discovery + binding ([Phase 2](https://donkey-development-kit.github.io/donkey-development-kit/tool-access.md)) | Unverified | `donkey.tools.discover` and the binding classes raise `blocked on verification`. |
| Exchange publish API ([Phase 2](https://donkey-development-kit.github.io/donkey-development-kit/publishing.md)) | Unverified | `donkey publish` is blocked; whether Exchange exposes first-class MCP/agent asset types is unconfirmed. |
| Token-exchange endpoint for [on-behalf-of](https://donkey-development-kit.github.io/donkey-development-kit/identity.md) (Phase 2) | Unverified | The exact endpoint and header the gateway expects are not guessed. |
| [Policy discovery](https://donkey-development-kit.github.io/donkey-development-kit/policies.md) (Phase 3) | **Missing upstream** | No such endpoint exists on the gateway today. Filed as an upstream gap — the handshake cannot be built until it ships. |
| Budget query endpoint ([Phase 1](https://donkey-development-kit.github.io/donkey-development-kit/budget.md)) | **Missing upstream** | Budget is reported in-band on response headers only, so `remaining` is last-known-good; `observed_at` exposes the staleness. |
| Whether Anypoint Monitoring ingests OTLP GenAI spans ([Phase 1](https://donkey-development-kit.github.io/donkey-development-kit/telemetry.md)) | Unverified | The SDK promises "exports OTLP" and lets the sink be your choice. |
| A provisioning control plane | **Cut** | Not built. It would compete with API Manager and Terraform — see [Roadmap](https://donkey-development-kit.github.io/donkey-development-kit/roadmap.md). |
| Prompt-injection / regex-guard / content-safety bodies | Not captured | Injection is typed by the `x-injection-protection: blocked` header, regex prompt-guard by a top-level `matched_patterns` list (both → `PromptInjectionBlocked`), and content-safety by the Azure Content Safety / Bedrock Guardrails `…-action: reject` header (→ `ContentSafetyBlocked`) — all pinned from the policy pages, but their **bodies** are uncaptured and no verification row flips until a live round-trip confirms them. Any other moderation shape still falls through to a generic `PolicyViolation`. No invented discriminator — bodies tracked in #253. |
| First-party **TypeScript SDK** | Planned | TypeScript examples reach the proxy over its OpenAI-compatible API via the `openai` / `@anthropic-ai/sdk` clients; no first-party TS package is implied until it ships. |

## What this means for you

As a consumer you don't have to track any of this. Any platform fact the SDK
hasn't confirmed raises `blocked on verification` rather than guessing, so a
governed call either works against the verified contract or fails loudly — it
never silently reaches an unconfirmed endpoint. The mechanism behind that and
the full verified-versus-blocked ledger are documented for contributors in
[ARCHITECTURE.md → Verification discipline](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/ARCHITECTURE.md#verification-discipline-03).
