# Unsupported boundary

Enterprise buyers ask: *which platform APIs does this SDK call, and are they
supported for third-party use?* Having the answer pre-written turns a procurement
stall into a short conversation.

The authoritative, maintained list lives in the repository at
[`docs/unsupported-boundary.md`](https://github.com/Donkey-Development-Kit/donkey-development-kit/blob/main/docs/unsupported-boundary.md).
It is separate from the [verification ledger](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md): verification
records how a fact was established, while this page records whether MuleSoft
publishes the contract for third-party use.

Every platform API the shipping SDK calls is classified:

| Classification | Meaning |
|---|---|
| **Documented and public** | Safe to depend on. |
| **Documented, no SLA for third-party use** | May break; we'll fix. |
| **Undocumented** | Should be empty. Anything here needs a written justification and an owner. |

## Current shipping boundary

The SDK currently reaches two platform destinations. The rows below classify
the distinct contracts it consumes at those destinations. Everything else that
needs an unverified endpoint is blocked before network I/O.

| Destination / contract | Classification | SDK use |
|---|---|---|
| Anypoint connected-app token endpoint | **Documented and public** | Retrieves an OAuth bearer token with client credentials. |
| Model Proxy OpenAI-format `/responses` endpoint | **Documented and public** | Sends buffered or streaming model requests with the documented `client_id` / `client_secret` headers and reads OpenAI-format usage. The raw client can also call documented OpenAI-native routes such as `/chat/completions`, but this repository has live-probed only `/responses`. |
| Live-captured Model Proxy policy refusals | **Documented and public** | Classifies Client ID Enforcement, token-rate-limit, and PII responses observed against a deployed proxy. |
| Documentation-derived Model Proxy policy refusals | **Documented and public** | Classifies the **Injection Protection** contract (`x-injection-protection: blocked`) from its official policy page; its body is still pending direct live capture (no proxy deployed). The Regex Prompt Guard, Azure Content Safety, and Amazon Bedrock Guardrails shapes are now live-captured — see the verification page. |
| Upstream provider error pass-through | **Documented, no SLA for third-party use** | Classifies the live-captured nested non-`429` `4xx` provider envelope as `UpstreamRequestError`; generic `5xx` responses become `UpstreamModelError` by status only. The envelope schema belongs to the upstream provider, and MuleSoft's public Model Proxy page states no pass-through compatibility contract. |
| `x-llm-proxy-ratelimit` success-budget sentence | **Documented, no SLA for third-party use** | Updates `donkey.budget`; an absent or changed value is ignored. |
| Gateway identity and routing extension headers | **Documented, no SLA for third-party use** | Populates `donkey.last_call`; missing or unrecognised values become `None`. |

  Exchange search and resolution, API Manager governed-state reads, MCP discovery
  and binding, and provisioning/publication are not hidden dependencies. They
  raise `NotImplementedError("blocked on verification: ...")` before making a
  network request. The SDK also does not call a Model Proxy `/models` endpoint,
  because live verification established that no such catalog endpoint exists.

The full ledger links each contract to its official documentation, SDK consumer,
verification evidence, and maintenance owner. Its **Undocumented surfaces**
section is empty.

## Support statement (the trademark/support boundary)

  Donkey Development Kit is an independent, community-maintained project with
  best-effort maintainer support and no SLA. It is not affiliated with, endorsed
  by, or supported by Salesforce or MuleSoft. "Agent Fabric", "Anypoint", and
  "Omni Gateway" are Salesforce trademarks.

## Why this matters (verification discipline)

The SDK's rule against inventing endpoints exists to keep this boundary honest: a
call the SDK makes is either against a classified, known API or it doesn't happen
at all. See [Verification policy](https://donkey-development-kit.github.io/donkey-development-kit/concepts/verification.md).
