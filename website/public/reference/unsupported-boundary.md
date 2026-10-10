# Unsupported boundary

Which platform APIs does the SDK call, and are they supported for third-party
use? This page answers that question for security and procurement reviews.

This page is the maintained list. It is separate from the
[verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md): the
ledger records how a fact was established, while this page records whether
MuleSoft publishes the contract for third-party use.

Every platform API the SDK calls is classified:

| Classification | Meaning |
|---|---|
| **Documented and public** | Safe to depend on. |
| **Documented, no SLA for third-party use** | Observed on a real gateway but not promised to third parties. The SDK reads it fail-open, and the maintainers update the SDK if it changes. |
| **Undocumented** | Must stay empty. Anything here needs a written justification and an owner. |

## Current boundary

The SDK can reach two platform destinations: the Model Proxy, for every model
call, and the Anypoint connected-app token endpoint, only when a control-plane
feature needs a token. Model calls never request that token. The features that
would request it are listed in
[Which features use the control plane](https://docs.donkey-kit.dev/reference/configuration.md#which-features-use-the-control-plane);
all of them are Roadmap in this release, so the token endpoint is contacted
only if your own code calls `AnypointConnectedApp.token()`. The rows below
classify each contract the SDK consumes. Any feature that would need an unconfirmed endpoint stops
before making a network request.

| Destination / contract | Module | Classification | SDK use and official docs |
|---|---|---|---|
| Anypoint connected-app token endpoint: `POST /accounts/api/v2/oauth2/token` | `core.auth.AnypointConnectedApp` | **Documented and public** | Retrieves an OAuth bearer token with client credentials. MuleSoft documents the request and response in [Getting the Bearer Token for a Connected App](https://docs.mulesoft.com/access-management/connected-app-bearer-token-example). |
| Model Proxy OpenAI-format `/responses` endpoint, including streaming and the OpenAI `usage` object | `llm.client.LLMClient`, `core.lastcall.LastCall` | **Documented and public** | Sends buffered or streaming model requests with the documented `client_id` / `client_secret` headers and reads OpenAI-format usage. Documented in [Sending Requests to Model Proxies](https://docs.mulesoft.com/general/model-proxy-request). The raw client can also call documented OpenAI-native routes such as `/chat/completions`; `/responses` is the route tested against a deployed proxy. A streamed `/chat/completions` request that the proxy routes to a Gemini upstream does not stream OpenAI chunks; see the warning below. |
| Client ID Enforcement request headers and `401` rejection | `core.transport`, `core.errors.classify` | **Documented and public** | Sends the consumer credentials and types the `401`. The [Client ID Enforcement policy](https://docs.mulesoft.com/gateway/latest/policies-included-client-id-enforcement) documents the header expressions and `WWW-Authenticate: Client-ID-Enforcement`. |
| LLM Token Based Rate Limit `429`: `x-token-limit`, `x-token-remaining`, `x-token-reset` | `core.budget.Budget`, `core.errors.classify` | **Documented and public** | Updates `donkey.budget` and types `TokenBudgetExceeded`. Documented in the [LLM Token Based Rate Limit policy](https://docs.mulesoft.com/gateway/latest/policies-included-llm-token-rate-limit). |
| LLM PII Detection `403` body (`error.type = "pii_detected"`) | `core.errors.classify` | **Documented and public** | Types `PIIDetected`. Documented in the [LLM PII Detection policy](https://docs.mulesoft.com/gateway/latest/policies-included-llm-pii-detection). |
| Regex Prompt Guard body and Injection Protection response header | `core.errors.classify` | **Documented and public** | Types `PromptInjectionBlocked`. MuleSoft documents `matched_patterns` in [Regex Prompt Guard](https://docs.mulesoft.com/gateway/latest/policies-included-regex-prompt-guard) and `x-injection-protection: blocked` in [Injection Protection](https://docs.mulesoft.com/gateway/latest/policies-included-injection-protection). |
| Azure Content Safety and Amazon Bedrock Guardrails rejection headers and bodies | `core.errors.classify` | **Documented and public** | Types `ContentSafetyBlocked`. Documented in the [Azure Content Safety](https://docs.mulesoft.com/gateway/latest/policies-included-azure-content-safety) and [Amazon Bedrock Guardrails](https://docs.mulesoft.com/gateway/latest/policies-included-bedrock-guardrails) policy pages. |
| Upstream provider error pass-through: the nested non-`429` `4xx` envelope, and the generic `5xx` status family | `core.errors.classify` | **Documented, no SLA for third-party use** | Classifies the nested envelope as `UpstreamRequestError`; a generic `5xx` becomes `UpstreamModelError` by status only, with no assumed body. The envelope schema belongs to the upstream provider, and MuleSoft's public Model Proxy page states no pass-through compatibility contract. |
| Successful-response budget window in the `x-llm-proxy-ratelimit` sentence | `core.budget.Budget` | **Documented, no SLA for third-party use** | Updates `donkey.budget`; an absent or changed value is ignored, never fatal. |
| Gateway identity and routing headers: `x-request-id`, `x-envoy-decorator-operation`, `x-llm-proxy-{routing-type,routing-fallback,llm-provider,llm-model}` | `core.lastcall.LastCall`, `core.transport` | **Documented, no SLA for third-party use** | Populates `donkey.last_call`; missing or unrecognised values become `None`, and only an explicit `routing-fallback: true` changes retry behaviour. |

How each row was established (live capture, plugin test, and so on) is in the
[verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md), sections 1 to 4.

  **Streaming chat completions to a Gemini upstream.** On an OpenAI-format
  proxy that routes to Gemini, `chat.completions.create(..., stream=True)`
  returns a `text/event-stream` whose events are whole `chat.completion`
  objects: the text is in `choices[0].message`, not `choices[0].delta`, every
  event has `finish_reason: "stop"`, and no `data: [DONE]` is sent. The openai
  client does not validate the events, so `chunk.choices[0].delta` is `None`
  and `client.chat.completions.stream(...)` fails. This was observed live
  (VERIFIED (LIVE), non-conformant) and is a gateway gap, not an SDK one.
  Until it is fixed, use `stream=False` for chat completions on Gemini routes.
  `donkey.last_call` still records the call's token totals. OpenAI-routed
  proxies stream normal chunks, and a `Format=Gemini` proxy's native
  `streamGenerateContent` route streams correctly.

## Deliberately outside the boundary

- Exchange search and resolution, API Manager governed-state reads, MCP
  discovery and binding, and Exchange publication are
  Roadmap, not hidden dependencies. Today they
  raise `NotImplementedError("blocked on verification: …")` before making any
  network request, so they are not classified above.
- `LLMClient.list_models(live=True)` does not call `GET /models`: the Model
  Proxy has no model-catalog endpoint.
- The application and business-group attribution header names are
  placeholders that still emit a warning (see the ledger, section 3). They have
  no config key, so they cannot be overridden; leave `application_name` and
  `business_group` unset to send neither header. The gateway reads and echoes
  the correlation header. The per-call and cost-tag header names are SDK
  conventions you can override, which the gateway does not read; cost tags are
  carried on spans. The SDK does not depend on the gateway reading any of
  these, so they are not platform contracts.

## Undocumented surfaces

This section must stay empty. A surface listed here needs a written
justification and an owner, and is re-evaluated at every milestone.

_(none)_

## Local Mode is not supported

  Donkey Development Kit does not support Omni/Flex Gateway **Local Mode**.
  The [local simulator](https://docs.donkey-kit.dev/simulator.md) (`donkey mock` / `simulate()`) is the
  supported local dev loop; real-gateway behavior is exercised against a
  hosted (Connected Mode) proxy instead. A stock Flex Gateway 1.14.0 Local
  Mode image rejects the LLM Proxy and MCP Bridge policies as missing
  extensions and refuses configuration until registered to a control plane —
  enough to make this scope call, bounded to that image version. See the
  [verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md#6-governance--local-mode-the-verification-milestone)
  and [issue #661](https://github.com/Donkey-Development-Kit/donkey-development-kit/issues/661).

## Support statement

  Donkey Development Kit is an independent, community-maintained project with
  best-effort maintainer support and no SLA. It is not affiliated with, endorsed
  by, or supported by Salesforce or MuleSoft. "Agent Fabric", "Anypoint", and
  "Omni Gateway" are Salesforce trademarks.

## Why the boundary stays small

The SDK doesn't invent endpoints: every call it makes is against a classified,
known API, or it doesn't happen at all. See the
[verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md).
