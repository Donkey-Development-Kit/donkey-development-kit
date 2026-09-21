# Unsupported boundary

Every platform API the SDK calls, classified. Enterprise buyers will ask; having
this pre-written converts a two-week procurement stall into a five-minute
conversation. Linked from the README above the fold.

This is a **support-classification ledger**, not the verification ledger.
[`verified-apis.md`](verified-apis.md) records how each fact was established
(`VERIFIED (LIVE)`, `VERIFIED (plugin)`, and so on). This file answers a
different question: whether the shipping SDK depends on a public MuleSoft
contract, a live-observed extension with no third-party compatibility SLA, or
an undocumented surface.

Classification:

- **Documented and public** — safe.
- **Documented but no SLA for third-party use** — will break; we'll fix.
- **Undocumented** — should be empty. Anything here needs a written
  justification and a named owner.

| API / surface | Module | Classification | Justification / owner |
|---|---|---|---|
| Connected-app bearer-token request: `POST /accounts/api/v2/oauth2/token` | `core.auth.AnypointConnectedApp` | **Documented and public** | MuleSoft documents the client-credentials request and response in [Getting the Bearer Token for a Connected App](https://docs.mulesoft.com/access-management/connected-app-bearer-token-example). The SDK path is also `VERIFIED (plugin)` in `verified-apis.md` §1 and §12.1. |
| Model Proxy OpenAI-format data plane: caller-configured base path plus `POST /responses`, including streaming and the OpenAI `usage` object | `llm.client.LLMClient`; `core.lastcall.LastCall` | **Documented and public** | MuleSoft documents the endpoint, `client_id` / `client_secret` headers, request bodies, and streaming in [Sending Requests to Model Proxies](https://docs.mulesoft.com/general/model-proxy-request). The SDK's exact request and response path is `VERIFIED (LIVE)` in `verified-apis.md` §2. |
| Client ID Enforcement request headers and `401` rejection contract | `core.transport.proxy_auth_headers`; `core.errors.classify` | **Documented and public** | The [Client ID Enforcement policy](https://docs.mulesoft.com/gateway/latest/policies-included-client-id-enforcement) documents the default header expressions and `WWW-Authenticate: Client-ID-Enforcement`. The configured proxy contract is `VERIFIED (LIVE)` in `verified-apis.md` §2 and §4. |
| LLM Token Based Rate Limit `429` response: `x-token-limit`, `x-token-remaining`, and `x-token-reset` | `core.budget.Budget`; `core.errors.classify` | **Documented and public** | The [LLM Token Based Rate Limit policy](https://docs.mulesoft.com/gateway/latest/policies-included-llm-token-rate-limit) documents all three headers and the reset unit. The SDK's rejection fixture is `VERIFIED (LIVE)` in `verified-apis.md` §4. |
| LLM PII Detection `403` rejection body (`error.type = "pii_detected"`) | `core.errors.classify` | **Documented and public** | The [LLM PII Detection policy](https://docs.mulesoft.com/gateway/latest/policies-included-llm-pii-detection) documents the rejection body. The SDK also captured it live; see `verified-apis.md` §4. |
| Regex Prompt Guard body and Injection Protection response header | `core.errors.classify` | **Documented and public** | MuleSoft documents `matched_patterns` in [Regex Prompt Guard](https://docs.mulesoft.com/gateway/latest/policies-included-regex-prompt-guard) and `x-injection-protection: blocked` in [Injection Protection](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection). These discriminators are documented but still pending direct live capture; see `verified-apis.md` §4. |
| Azure Content Safety and Amazon Bedrock Guardrails rejection headers and bodies | `core.errors.classify` | **Documented and public** | The [Azure Content Safety](https://docs.mulesoft.com/gateway/latest/policies-included-azure-content-safety) and [Amazon Bedrock Guardrails](https://docs.mulesoft.com/gateway/latest/policies-included-bedrock-guardrails) policy pages document their action/reason headers and `403` bodies. These shapes are pending direct live capture; see `verified-apis.md` §4. |
| Successful-response budget window in `x-llm-proxy-ratelimit` prose | `core.budget.Budget` | **Documented but no SLA for third-party use** | Captured from a real Model Proxy and parsed fail-open: an absent or changed sentence is ignored, never fatal. The exact prose contract is `VERIFIED (LIVE)` in `verified-apis.md` §4 but is not stated in the public policy page. Owner: DDK maintainers; re-capture on gateway-policy changes. |
| Gateway identity and routing response metadata: `x-request-id`, `x-envoy-decorator-operation`, and `x-llm-proxy-{routing-type,routing-fallback,llm-provider,llm-model}` | `core.lastcall.LastCall`; `core.transport.DonkeyAsyncClient` | **Documented but no SLA for third-party use** | Captured from a real Model Proxy (`verified-apis.md` §2 and §3). Parsers are fail-open: missing or unrecognised values become `None`, and only an explicit `routing-fallback: true` changes retry behavior. Owner: DDK maintainers; contract changes require new fixtures before parser changes. |

## Deliberately outside the shipping boundary

- Exchange search and asset resolution, API Manager governed-state reads, MCP
  discovery/binding, and provisioning/publication raise
  `NotImplementedError("blocked on verification: ...")` before network I/O.
  They are therefore not called platform APIs and are not classified above.
- `LLMClient.list_models(live=True)` does not call `GET /models`; live capture
  established that Model Proxy has no model-catalog endpoint.
- Correlation, per-call, application/business-group attribution, and cost-tag
  request-header names remain warning-emitting, overridable `Unverified`
  placeholders (`verified-apis.md` §3). The SDK makes no claim that the gateway
  reads them and does not depend on it doing so, so they are not represented as
  supported platform contracts here.

## Undocumented surfaces

**This section must stay empty.** If a surface lands here, it needs a written
justification and an owner, and it must be re-evaluated at every milestone.

_(none — as required)_
