# Unsupported boundary

Which platform APIs does the SDK call, and are they supported for third-party
use? This page answers that question for security and procurement reviews.

The maintained list lives in the repository at
[`docs/unsupported-boundary.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/unsupported-boundary.md).
It is separate from the [verification ledger](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/verified-apis.md): the
ledger records how a fact was established, while this page records whether
MuleSoft publishes the contract for third-party use.

Every platform API the SDK calls is classified:

| Classification | Meaning |
|---|---|
| **Documented and public** | Safe to depend on. |
| **Documented, no SLA for third-party use** | May break; we'll fix. |
| **Undocumented** | Should be empty. Anything here needs a written justification and an owner. |

## Current boundary

The SDK can reach two platform destinations: the Model Proxy, for every model
call, and the Anypoint connected-app token endpoint, only when a control-plane
feature needs a token. Model calls never request that token. The features that
would request it are listed in
[Which features use the control plane](https://donkey-development-kit.github.io/donkey-development-kit/reference/configuration.md#which-features-use-the-control-plane);
all of them are Roadmap in this release, so the token endpoint is contacted
only if your own code calls `AnypointConnectedApp.token()`. The rows below
classify each contract the SDK consumes. Any feature that would need an unconfirmed endpoint stops
before making a network request.

| Destination / contract | Classification | SDK use |
|---|---|---|
| Anypoint connected-app token endpoint | **Documented and public** | Retrieves an OAuth bearer token with client credentials. |
| Model Proxy OpenAI-format `/responses` endpoint | **Documented and public** | Sends buffered or streaming model requests with the documented `client_id` / `client_secret` headers and reads OpenAI-format usage. The raw client can also call documented OpenAI-native routes such as `/chat/completions`; `/responses` is the route tested against a deployed proxy. A streamed `/chat/completions` request that the proxy routes to a Gemini upstream does not stream OpenAI chunks; see the warning below. |
| Model Proxy policy refusals (observed) | **Documented and public** | Classifies Client ID Enforcement, token-rate-limit, PII, Injection Protection, Regex Prompt Guard, Azure Content Safety, and Amazon Bedrock Guardrails responses captured from a deployed proxy. |
| Upstream provider error pass-through | **Documented, no SLA for third-party use** | Classifies the nested non-`429` `4xx` provider envelope as `UpstreamRequestError`; generic `5xx` responses become `UpstreamModelError` by status only. The envelope schema belongs to the upstream provider, and MuleSoft's public Model Proxy page states no pass-through compatibility contract. |
| `x-llm-proxy-ratelimit` success-budget sentence | **Documented, no SLA for third-party use** | Updates `donkey.budget`; an absent or changed value is ignored. |
| Gateway identity and routing extension headers | **Documented, no SLA for third-party use** | Populates `donkey.last_call`; missing or unrecognised values become `None`. |

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

  Exchange search and resolution, API Manager governed-state reads, MCP
  discovery and binding, and provisioning/publication are
  Roadmap — not hidden dependencies. Today they
  raise `NotImplementedError` before making any network request. The SDK also
  never calls a Model Proxy `/models` endpoint, because the proxy has no model
  catalog endpoint.

The full ledger links each contract to its official documentation, SDK
consumer, evidence, and maintenance owner. Its **Undocumented surfaces**
section is empty.

## Local Mode is not supported

  Donkey Development Kit does not support Omni/Flex Gateway **Local Mode**.
  The [local simulator](https://donkey-development-kit.github.io/donkey-development-kit/simulator.md) (`donkey mock` / `simulate()`) is the
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
